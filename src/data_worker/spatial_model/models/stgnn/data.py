"""Shared causal inputs and sensor exclusions for correction training and evaluation."""
import os
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
import json
from pathlib import Path
import torch
import numpy as np
import pandas as pd
from pyproj import Transformer
from .preprocessing import BASE_TEMPORAL_COLUMNS, LOCAL_COLUMNS
from .sensors import idw_features, IDW_COLUMNS
from spatial_model.data.validation import verify_snapshot

from types import SimpleNamespace

def prepare(snapshot, protocol="fixed_split", fold=None):
    DATA_DIR = Path(snapshot)
    TARGET, PROTOCOL, FOLD = "pm25", protocol, fold
    hashes = verify_snapshot(DATA_DIR)
    if json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))["version"] != "ds-v0.3.0":
        raise ValueError("Require ds-v0.3.0")
    if PROTOCOL not in ("fixed_split", "loso"):
        raise ValueError("Choose fixed_split or loso")
    if PROTOCOL == "fixed_split" and (TARGET != "pm25" or FOLD is not None):
        raise ValueError("Fixed split requires PM2.5 and FOLD = None")
    if PROTOCOL == "loso" and FOLD is None:
        raise ValueError("Set FOLD for LOSO")
    manifest = json.loads((DATA_DIR / "stgnn/manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "complete" or manifest["horizon_hours"] != 0:
        raise ValueError("Require a completed estimation dataset with horizon zero")
    window = int(manifest["window"])
    if window != 24:
        raise ValueError("Architecture v1 requires a 24-hour history")
    nodes = pd.read_parquet(DATA_DIR / "stgnn/nodes.parquet").sort_values("node_index")
    if not np.array_equal(nodes.node_index, np.arange(len(nodes))) or not nodes.grid_id.is_unique:
        raise ValueError("Require contiguous canonical node indices and unique grid IDs")
    edges = pd.read_parquet(DATA_DIR / "stgnn/edges.parquet")
    edge_index = torch.tensor(edges[["source_node", "target_node"]].to_numpy().T, dtype=torch.long)
    stations = pd.read_parquet(DATA_DIR / "stations.parquet")
    labels = pd.read_parquet(DATA_DIR / "labels.parquet")
    target_column = f"{TARGET}_ugm3"
    held_out_group = None
    if PROTOCOL == "fixed_split":
        training_stations = stations[stations.station_split == "train"]
        split_labels = {}
        for split in ("train", "val", "test"):
            table = pd.read_parquet(DATA_DIR / "splits" / f"{split}.parquet")
            split_labels[split] = table[(table.target == TARGET) & (table.protocol == "fixed_split")].copy()
    else:
        folds = pd.read_parquet(DATA_DIR / "splits/loso_folds.parquet")
        held = folds[(folds.target == TARGET) & (folds.fold == FOLD)]
        if held.empty or held.group_id.nunique() != 1:
            raise ValueError("Unknown LOSO fold")
        held_out_group = str(held.group_id.iloc[0])
        training_stations = stations[stations.group_id != held_out_group]
        split_labels = {}
        for split in ("train", "val", "test"):
            eligible = (labels.group_id == held_out_group if split == "test"
                        else labels.group_id != held_out_group)
            table = labels[eligible & (labels.period == split) & labels[target_column].notna()].copy()
            split_labels[split] = table.rename(columns={target_column: "value_ugm3"})
    for split, table in split_labels.items():
        if table.empty or table.duplicated(["station_uuid", "time_utc"]).any():
            raise ValueError(f"Empty or duplicated {split} labels for this target/protocol")
        if not np.isfinite(table.value_ugm3).all() or not table.node_index.between(0, len(nodes) - 1).all():
            raise ValueError("Invalid target value or label node index")
        table["time_utc"] = pd.to_datetime(table.time_utc, utc=True)
        split_labels[split] = table.sort_values(["time_utc", "station_uuid"]).reset_index(drop=True)
    feature_columns = ["time_utc", "node_index", "grid_id", *BASE_TEMPORAL_COLUMNS, *LOCAL_COLUMNS, "dist_nearest_sensor_m"]
    features = pd.read_parquet(DATA_DIR / "stgnn/features.parquet", columns=feature_columns)
    if features.duplicated(["node_index", "time_utc"]).any():
        raise ValueError("Duplicate node/hour features")
    hours = pd.date_range(manifest["start_utc"], manifest["end_utc_exclusive"],
                               freq="h", inclusive="left")
    index = pd.MultiIndex.from_product([hours, nodes.node_index], names=["time_utc", "node_index"])
    if len(features) != len(index):
        raise ValueError("Incomplete feature history")
    features = features.set_index(["time_utc", "node_index"]).reindex(index)
    expected_grid_ids = np.tile(nodes.grid_id.to_numpy(), len(hours))
    if features.grid_id.isna().any() or not np.array_equal(features.grid_id.to_numpy(), expected_grid_ids):
        raise ValueError("Feature grid/node mapping differs from graph")
    shape = (len(hours), len(nodes), -1)
    temporal_features = features[BASE_TEMPORAL_COLUMNS].to_numpy(dtype="float32").reshape(shape)
    local_features = features[[*LOCAL_COLUMNS, "dist_nearest_sensor_m"]].to_numpy(dtype="float32").reshape(shape).copy()
    hour_index = {t: i for i, t in enumerate(hours)}
    grid_coordinates = nodes.grid_id.str.extract(r"^r(\d+)_c(\d+)$").astype(int).to_numpy()
    training_mask = np.zeros(len(nodes), dtype=bool)
    for grid_id in training_stations.grid_id.unique():
        row, column = map(int, grid_id[1:].split("_c"))
        training_mask |= (np.abs(grid_coordinates - [row, column]).max(axis=1) <= 1)
    excluded = stations[~stations.uuid.isin(training_stations.uuid)]
    for grid_id in excluded.grid_id.unique():
        row, column = map(int, grid_id[1:].split("_c"))
        training_mask &= ~(np.abs(grid_coordinates - [row, column]).max(axis=1) <= 1)
    training_nodes = np.flatnonzero(training_mask)
    if not training_mask.any():
        raise ValueError("No eligible feature-fit nodes")
    for table in split_labels.values():
        positions = [hour_index.get(t, -1) for t in table.time_utc]
        if min(positions) < window - 1:
            raise ValueError("Label lacks a complete causal input window")

    split_hours = {split: table.time_utc.drop_duplicates().tolist() for split, table in split_labels.items()}
    print(pd.DataFrame({split: {"labels": len(table), "hours": len(split_hours[split]),
        "stations": table.station_uuid.nunique()} for split, table in split_labels.items()}).T)
    print("Graph:", len(nodes), "nodes,", edge_index.shape[1], "edges")
    source_stations = training_stations.sort_values("uuid").reset_index(drop=True)
    source_ids = source_stations.uuid.tolist()
    sensor_readings = labels[labels.station_uuid.isin(source_ids)]
    sensor_readings = sensor_readings.pivot(index="time_utc", columns="station_uuid", values=target_column)
    sensor_readings = sensor_readings.reindex(index=hours, columns=source_ids).to_numpy(dtype=float)

    projection = Transformer.from_crs("EPSG:4326", "EPSG:32748", always_xy=True)
    source_xy = np.column_stack(projection.transform(source_stations.lng.to_numpy(), source_stations.lat.to_numpy()))
    station_xy = np.column_stack(projection.transform(stations.lng.to_numpy(), stations.lat.to_numpy()))
    node_xy = nodes[["easting", "northing"]].to_numpy()
    node_distances = np.linalg.norm(node_xy[:, None, :] - source_xy[None, :, :], axis=-1)
    station_distances = np.linalg.norm(station_xy[:, None, :] - source_xy[None, :, :], axis=-1)
    # Exclude the target group from its label baseline and its GAT receptive field.
    same_group = stations.group_id.to_numpy()[:, None] == source_stations.group_id.to_numpy()[None, :]
    station_distances[same_group] = np.inf
    source_grid = source_stations.grid_id.str.extract(r"^r(\d+)_c(\d+)$").astype(int).to_numpy()
    source_allowed = np.ones_like(node_distances, dtype=bool)

    # One GAT layer sees one hop. Two cells safely cover each target's neighbours.
    for group_id in source_stations.group_id.unique():
        group_sources = source_stations.group_id.to_numpy() == group_id
        group_grid = source_grid[group_sources]
        grid_separation = np.abs(grid_coordinates[:, None, :] - group_grid[None, :, :]).max(axis=-1)
        near_group = (grid_separation <= 2).any(axis=1)
        source_allowed[np.ix_(near_group, group_sources)] = False

    node_distances[~source_allowed] = np.inf
    print("Source stations:", len(source_stations))
    sensor_features = np.full((len(hours), len(nodes), len(IDW_COLUMNS)), np.nan, dtype="float32")
    station_baselines = np.full((len(hours), len(stations)), np.nan, dtype="float32")

    for hour_position, readings in enumerate(sensor_readings):
        sensor_features[hour_position] = idw_features(node_distances, readings)
        station_baselines[hour_position] = idw_features(station_distances, readings)[:, 0]

    station_position = {uuid: i for i, uuid in enumerate(stations.uuid)}
    for split, table in split_labels.items():
        time_positions = table.time_utc.map(hour_index).to_numpy()
        station_positions = table.station_uuid.map(station_position).to_numpy()
        table["idw_prediction"] = station_baselines[time_positions, station_positions]
        missing = ~np.isfinite(table.idw_prediction)
        if split == "train":
            skipped_training_labels = table.loc[missing].copy()
            split_labels[split] = table.loc[~missing].reset_index(drop=True)
            print("Training labels without another sensor group:", int(missing.sum()))
        elif missing.any():
            raise ValueError(f"IDW has no eligible sources for some {split} labels; comparison is unevaluable")

    split_hours = {split: table.time_utc.drop_duplicates().tolist() for split, table in split_labels.items()}

    temporal_features = np.concatenate([temporal_features, sensor_features], axis=-1)


    # The dynamic IDW distance replaces distance to a sensor that may be the target itself.

    local_features = local_features[..., :-1]
    return SimpleNamespace(hashes=hashes, split_labels=split_labels, split_hours=split_hours, edge_index=edge_index, training_nodes=training_nodes, hours=hours, hour_index=hour_index, window=window, temporal_features=temporal_features, local_features=local_features, skipped_training_labels=skipped_training_labels, source_ids=source_ids)

def make_batch(data, preprocessing, hours, split, device="cpu"):
    DEVICE = device
    hour_index = data.hour_index
    window = data.window
    temporal_features = data.temporal_features
    local_features = data.local_features
    split_labels = data.split_labels
    edge_index = data.edge_index

    temporal, local, labels = [], [], []
    for b, hour in enumerate(hours):
        hour = pd.Timestamp(hour)
        i = hour_index[hour]
        temporal.append(temporal_features[i - window + 1:i + 1].transpose(1, 0, 2))
        local.append(local_features[i])
        table = split_labels[split]
        labels.append(table[table.time_utc == hour].assign(batch_index=b))
    raw_t, raw_s = np.stack(temporal), np.stack(local)
    t, s = preprocessing.transform(raw_t, raw_s)
    lab = pd.concat(labels, ignore_index=True)
    return dict(temporal=torch.from_numpy(t).to(DEVICE), local=torch.from_numpy(s).to(DEVICE),
                edge_index=edge_index.to(DEVICE),
                label_batch=torch.tensor(lab.batch_index.to_numpy(), dtype=torch.long, device=DEVICE),
                label_node=torch.tensor(lab.node_index.to_numpy(dtype="int64"), dtype=torch.long, device=DEVICE),
                idw_values=torch.tensor(lab.idw_prediction.to_numpy(), dtype=torch.float32, device=DEVICE),
                label_values=torch.tensor(lab.value_ugm3.to_numpy(), dtype=torch.float32, device=DEVICE),
                labels=lab)
