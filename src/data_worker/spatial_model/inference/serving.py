"""Registered concentration models and bounded, feature-consistent grid inference."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from spatial_model.artifacts.bundles import validate_bundle


def load_model(directory, target):
    directory = Path(directory)
    manifest = validate_bundle(directory, target)
    if target == "pm25":
        import torch
        from spatial_model.models.stgnn.model import ModelConfig, STGNN
        from spatial_model.models.stgnn.preprocessing import Preprocessor, TEMPORAL_COLUMNS, LOCAL_COLUMNS
        artifact = torch.load(directory / "best.pt", map_location="cpu", weights_only=True)
        if artifact["artifact_version"] != "idw_residual_v1":
            raise ValueError("Require idw_residual_v1 checkpoint")
        if artifact["metadata"]["target"] != "pm25":
            raise ValueError("Checkpoint target mismatch")
        preprocessing = Preprocessor.from_state(artifact["preprocessing"])
        if preprocessing.temporal.columns != TEMPORAL_COLUMNS or preprocessing.local.columns != LOCAL_COLUMNS:
            raise ValueError("Checkpoint feature order mismatch")
        config = dict(artifact["model_config"])
        config["dilations"] = tuple(config["dilations"])
        model = STGNN(ModelConfig(**config))
        model.load_state_dict(artifact["state_dict"])
        model.eval()
        return SimpleNamespace(model=model, preprocessing=preprocessing, metadata=artifact["metadata"], manifest=manifest)
    import lightgbm as lgb
    metadata = json.loads((directory / "metadata.json").read_text())
    model = lgb.Booster(model_file=str(directory / "model.txt"))
    if model.feature_name() != metadata["columns"]:
        raise ValueError("LightGBM feature order mismatch")
    return SimpleNamespace(model=model, metadata=metadata, manifest=manifest)


class GridInputs:
    """As-of environmental features plus measured sensor history, without training labels.

    reader(grid_ids, start, end) follows feature_matrix.SourceReader's hourly contract.
    readings: time_utc (completed-hour end), station_uuid, pm25_ugm3, no2_ugm3.
    """

    def __init__(self, reader, readings):
        self.reader = reader
        self._hash = hashlib.sha256()
        self.readings = readings.copy()
        self.readings["time_utc"] = pd.to_datetime(self.readings.time_utc, utc=True)
        if self.readings.duplicated(["time_utc", "station_uuid"]).any():
            raise ValueError("Duplicate sensor hour")

    @property
    def fingerprint(self):
        return self._hash.hexdigest()

    def reset(self):
        self._hash = hashlib.sha256()

    def frames(self, nodes, stations, target, end):
        end = pd.Timestamp(end)
        hours = pd.date_range(end - pd.Timedelta(hours=23), end, freq="h")
        ids = list(dict.fromkeys([*nodes.grid_id, *stations.grid_id]))
        frame = self.reader(ids, hours[0], end + pd.Timedelta(hours=1))
        self._hash.update(pd.util.hash_pandas_object(frame, index=False).values.tobytes())
        if frame.duplicated(["time_utc", "grid_id"]).any():
            raise ValueError("Duplicate environmental grid hour")
        # SourceReader already performs as-of joins; reject explicit future provenance.
        for col in frame.columns:
            if col.endswith("available_at_utc"):
                if (pd.to_datetime(frame[col], utc=True) > pd.to_datetime(frame.time_utc, utc=True)).any():
                    raise ValueError("Future environmental input")
        index = pd.MultiIndex.from_product([hours, ids], names=["time_utc", "grid_id"])
        frame = frame.set_index(["time_utc", "grid_id"]).reindex(index)
        if frame["easting"].isna().any():
            raise ValueError("Incomplete grid history")
        sensor = self.readings[self.readings.station_uuid.isin(stations.uuid)]
        values = sensor.pivot(index="time_utc", columns="station_uuid", values=f"{target}_ugm3")
        values = values.reindex(index=hours, columns=stations.uuid).to_numpy(dtype=float)
        return hours, frame, values


def query_nodes(spec):
    from feature_matrix.grid import grid_id, row_col
    x, y = spec.cell_centers()
    rows, cols = row_col(x, y)
    return pd.DataFrame(dict(cell_id=np.arange(len(x)), grid_id=grid_id(rows, cols),
                             easting=x, northing=y, row=rows, col=cols))


def tiles(nodes, width=16):
    """Interior tiles with one Chebyshev-cell halo for the single GAT layer."""
    from feature_matrix.grid import grid_id
    for _, inside in nodes.groupby([nodes.row // width, nodes.col // width], sort=True):
        rows, cols = np.meshgrid(np.arange(inside.row.min()-1, inside.row.max()+2),
                                 np.arange(inside.col.min()-1, inside.col.max()+2), indexing="ij")
        r, c = rows.ravel(), cols.ravel()
        valid = (r >= 0) & (r < 445) & (c >= 0) & (c < 445)
        r, c = r[valid], c[valid]
        halo = pd.DataFrame(dict(grid_id=grid_id(r, c), row=r, col=c,
                                  easting=676900 + 100*c + 50, northing=9336600 - 100*r - 50))
        yield inside, halo


def predict_pm25(loaded, inputs, nodes, stations, end):
    import torch
    from spatial_model.data.grid import to_grid_xy
    from spatial_model.models.stgnn.preprocessing import BASE_TEMPORAL_COLUMNS, LOCAL_COLUMNS
    from spatial_model.models.stgnn.sensors import idw_features
    _, frame, readings = inputs.frames(nodes, stations, "pm25", end)
    n = len(nodes)
    raw = frame.reindex(pd.MultiIndex.from_product([frame.index.levels[0], nodes.grid_id], names=frame.index.names))
    temporal = raw[BASE_TEMPORAL_COLUMNS].to_numpy(dtype="float32").reshape(24, n, -1)
    local = raw[LOCAL_COLUMNS].to_numpy(dtype="float32").reshape(24, n, -1)[-1]
    sx, sy = to_grid_xy(stations.lng, stations.lat)
    distance = np.linalg.norm(nodes[["easting", "northing"]].to_numpy()[:, None] - np.column_stack([sx, sy])[None], axis=-1)
    source_grid = stations.grid_id.str.extract(r"r(\d+)_c(\d+)").astype(int).to_numpy()
    # Match training exclusions for source groups in the one-hop receptive field.
    for group in stations.group_id.unique():
        selected = stations.group_id.to_numpy() == group
        near = (np.abs(nodes[["row", "col"]].to_numpy()[:, None] - source_grid[None, selected]).max(-1) <= 2).any(-1)
        distance[np.ix_(near, selected)] = np.inf
    sensor = np.stack([idw_features(distance, row) for row in readings])
    temporal = np.concatenate([temporal, sensor], axis=-1).transpose(1, 0, 2)[None]
    temporal, local = loaded.preprocessing.transform(temporal, local[None])
    coordinates = nodes[["row", "col"]].to_numpy()
    near = np.abs(coordinates[:, None] - coordinates[None]).max(-1) <= 1
    np.fill_diagonal(near, False)
    edges = torch.as_tensor(np.array(np.nonzero(near)), dtype=torch.long)
    with torch.no_grad():
        correction = loaded.model(torch.from_numpy(temporal), torch.from_numpy(local), edges).numpy()[0, :, 0]
    predicted = np.maximum(sensor[-1, :, 0] + correction * loaded.preprocessing.target_scale, 0)
    return predicted


def tabular_frame(inputs, nodes, stations, target, end):
    """Use exactly the training feature algebra on sources plus unlabeled query nodes."""
    from spatial_model.data.features import spatial_features
    from spatial_model.data.grid import to_grid_xy
    hours, frame, readings = inputs.frames(nodes, stations, target, end)
    h, s, q = len(hours), len(stations), len(nodes)
    sx, sy = to_grid_xy(stations.lng, stations.lat)
    xy = np.vstack([np.column_stack([sx, sy]), nodes[["easting", "northing"]]])
    p = SimpleNamespace(target=target, Y=np.column_stack([readings, np.full((h, q), np.nan)]),
                        is_ref=np.r_[stations.station_type.eq("Reference"), np.zeros(q, dtype=bool)])
    groups = np.r_[stations.group_id, [f"query:{i}" for i in range(q)]]
    p.same_group = groups[:, None] == groups[None]
    p.D = np.linalg.norm(xy[:, None] - xy[None], axis=-1)
    order = list(stations.grid_id) + list(nodes.grid_id)
    p.G = {}
    for column in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[column]):
            pivot = frame[column].unstack("grid_id").reindex(index=hours, columns=order)
            p.G[column] = pivot.to_numpy(dtype=float)
    th = np.deg2rad(p.G.pop("wind_direction_10m"))
    p.G["wind_u"] = -p.G["wind_speed_10m"] * np.sin(th)
    p.G["wind_v"] = -p.G["wind_speed_10m"] * np.cos(th)
    p.source_mask = lambda allowed: np.isfinite(p.Y) & allowed[None]
    f = spatial_features(p, np.r_[np.ones(s, dtype=bool), np.zeros(q, dtype=bool)])
    return pd.DataFrame({k: v[-1, s:] for k, v in (f | p.G).items()})


def predict_grid(loaded, inputs, spec, bundle, end):
    target = loaded.manifest["target"]
    stations = pd.read_parquet(Path(bundle) / "stations.parquet")
    stations = stations[stations.uuid.isin(loaded.metadata["sensor_stations"])].sort_values("uuid").reset_index(drop=True)
    if stations.empty:
        raise ValueError("No eligible model sensors")
    nodes = query_nodes(spec)
    values = np.full(len(nodes), np.nan)
    for inside, halo in tiles(nodes):
        if target == "pm25":
            prediction = predict_pm25(loaded, inputs, halo, stations, end)
            lookup = pd.Series(prediction, index=halo.grid_id)
            values[inside.cell_id] = lookup.reindex(inside.grid_id)
        else:
            from spatial_model.models.lightgbm.model import predict
            frame = tabular_frame(inputs, inside, stations, target, end)
            values[inside.cell_id] = predict(loaded.model, loaded.metadata, frame)
    return values
