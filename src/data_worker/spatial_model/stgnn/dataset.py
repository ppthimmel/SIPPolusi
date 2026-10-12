"""Frozen snapshot adapter with explicit target/protocol keys and train-only fitting."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from feature_matrix.prepare_stgnn import sensor_distances, stgnn_hash
from .preprocessing import LOCAL_COLUMNS, TEMPORAL_COLUMNS, Preprocessor


def verify_snapshot(root):
    """Verify the frozen checksum list before reading training inputs."""
    root = Path(root).resolve()
    hashes = {}
    for line in (root / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Snapshot path outside root: {name}")
        actual = stgnn_hash(path)
        # Git may check text out as CRLF on Windows. Accept only this text-only
        # conversion; Parquet and other binary inputs must match byte for byte.
        if actual != digest and path.suffix in (".md", ".json", ".toml"):
            actual = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if actual != digest:
            raise ValueError(f"Snapshot checksum mismatch: {name}")
        hashes[name] = digest
    required = {"labels.parquet", "stations.parquet", "split_spec.json", "stgnn/features.parquet",
                "stgnn/nodes.parquet", "stgnn/edges.parquet", "stgnn/manifest.json",
                "splits/train.parquet", "splits/val.parquet", "splits/test.parquet",
                "splits/loso_folds.parquet"}
    if not required <= hashes.keys():
        raise ValueError("Incomplete frozen snapshot checksum list")
    return hashes


class SnapshotData:
    def __init__(self, root, target="pm25", protocol="fixed_split", fold=None):
        self.root = Path(root).resolve()
        if target not in ("pm25", "no2") or protocol not in ("fixed_split", "loso"):
            raise ValueError("Unsupported target/protocol")
        if (protocol == "fixed_split" and (target != "pm25" or fold is not None)
                or protocol == "loso" and fold is None):
            raise ValueError("Fixed split is PM2.5 only; LOSO requires an explicit fold")
        self.target, self.protocol, self.fold = target, protocol, fold
        self.hashes = verify_snapshot(self.root)
        self.manifest = json.loads((self.root / "stgnn/manifest.json").read_text(encoding="utf-8"))
        if self.manifest["status"] != "complete" or self.manifest["horizon_hours"] != 0:
            raise ValueError("Require a completed estimation dataset with horizon zero")
        self.window = int(self.manifest["window"])
        if self.window != 24:
            raise ValueError("Architecture v1 requires a 24-hour history")
        self.nodes = pd.read_parquet(self.root / "stgnn/nodes.parquet").sort_values("node_index")
        if not np.array_equal(self.nodes.node_index, np.arange(len(self.nodes))) or not self.nodes.grid_id.is_unique:
            raise ValueError("Require contiguous canonical node indices and unique grid IDs")
        edges = pd.read_parquet(self.root / "stgnn/edges.parquet")
        self.edges = torch.tensor(edges[["source_node", "target_node"]].to_numpy().T, dtype=torch.long)
        self.stations = pd.read_parquet(self.root / "stations.parquet")
        labels = pd.read_parquet(self.root / "labels.parquet")
        self.held_out_group = None
        if protocol == "fixed_split":
            self.sensors = self.stations[self.stations.station_split == "train"]
            self.keys = {}
            for split in ("train", "val", "test"):
                table = pd.read_parquet(self.root / "splits" / f"{split}.parquet")
                self.keys[split] = table[(table.target == target) & (table.protocol == "fixed_split")].copy()
        else:
            folds = pd.read_parquet(self.root / "splits/loso_folds.parquet")
            held = folds[(folds.target == target) & (folds.fold == fold)]
            if held.empty or held.group_id.nunique() != 1:
                raise ValueError("Unknown LOSO fold")
            self.held_out_group = str(held.group_id.iloc[0])
            self.sensors = self.stations[self.stations.group_id != self.held_out_group]
            self.keys = {}
            for split in ("train", "val", "test"):
                eligible = (labels.group_id == self.held_out_group if split == "test"
                            else labels.group_id != self.held_out_group)
                table = labels[eligible & (labels.period == split) & labels[f"{target}_ugm3"].notna()].copy()
                self.keys[split] = table.rename(columns={f"{target}_ugm3": "value_ugm3"})
        for split, table in self.keys.items():
            if table.empty or table.duplicated(["station_uuid", "time_utc"]).any():
                raise ValueError(f"Empty or duplicated {split} labels for this target/protocol")
            if not np.isfinite(table.value_ugm3).all() or not table.node_index.between(0, len(self.nodes) - 1).all():
                raise ValueError("Invalid target value or label node index")
            table["time_utc"] = pd.to_datetime(table.time_utc, utc=True)
            self.keys[split] = table.sort_values(["time_utc", "station_uuid"]).reset_index(drop=True)
        cols = ["time_utc", "node_index", "grid_id", *TEMPORAL_COLUMNS, *LOCAL_COLUMNS]
        f = pd.read_parquet(self.root / "stgnn/features.parquet", columns=cols)
        if f.duplicated(["node_index", "time_utc"]).any():
            raise ValueError("Duplicate node/hour features")
        self.times = pd.date_range(self.manifest["start_utc"], self.manifest["end_utc_exclusive"],
                                   freq="h", inclusive="left")
        index = pd.MultiIndex.from_product([self.times, self.nodes.node_index], names=["time_utc", "node_index"])
        if len(f) != len(index):
            raise ValueError("Incomplete feature history")
        f = f.set_index(["time_utc", "node_index"]).reindex(index)
        expected_grid = np.tile(self.nodes.grid_id.to_numpy(), len(self.times))
        if f.grid_id.isna().any() or not np.array_equal(f.grid_id.to_numpy(), expected_grid):
            raise ValueError("Feature grid/node mapping differs from graph")
        shape = (len(self.times), len(self.nodes), -1)
        self.temporal = f[TEMPORAL_COLUMNS].to_numpy(dtype="float32").reshape(shape)
        self.local = f[LOCAL_COLUMNS].to_numpy(dtype="float32").reshape(shape).copy()
        # Recompute for the selected protocol rather than trusting a distance from another fold.
        sensors = self.sensors.rename(columns={"lng": "station_longitude", "lat": "station_latitude"})
        self.local[:, :, -1] = sensor_distances(self.nodes, sensors).astype("float32")
        self.time_index = {t: i for i, t in enumerate(self.times)}
        self.training_nodes = self._training_nodes()
        for table in self.keys.values():
            positions = [self.time_index.get(t, -1) for t in table.time_utc]
            if min(positions) < self.window - 1:
                raise ValueError("Label lacks a complete causal input window")

    def _patch(self, stations):
        rc = self.nodes.grid_id.str.extract(r"^r(\d+)_c(\d+)$").astype(int).to_numpy()
        selected = np.zeros(len(self.nodes), dtype=bool)
        for name in stations.grid_id.unique():
            r, c = map(int, name[1:].split("_c"))
            selected |= (np.abs(rc - [r, c]).max(axis=1) <= 1)
        return selected

    def _training_nodes(self):
        selected = self._patch(self.sensors)
        excluded = self.stations[~self.stations.uuid.isin(self.sensors.uuid)]
        selected &= ~self._patch(excluded)
        if not selected.any():
            raise ValueError("No eligible feature-fit nodes")
        return np.flatnonzero(selected)

    def fit_preprocessor(self):
        train = self.keys["train"]
        first = self.time_index[train.time_utc.min()] - self.window + 1
        last = self.time_index[train.time_utc.max()] + 1
        self.fit_times = [str(self.times[first]), str(self.times[last - 1])]
        return Preprocessor().fit(self.temporal[first:last, self.training_nodes],
                                  self.local[first:last, self.training_nodes], train.value_ugm3.to_numpy())

    def hours(self, split):
        return self.keys[split].time_utc.drop_duplicates().tolist()

    def batch(self, hours, split, preprocessing, device="cpu"):
        temporal, local, labels = [], [], []
        for b, hour in enumerate(hours):
            hour = pd.Timestamp(hour)
            i = self.time_index[hour]
            temporal.append(self.temporal[i - self.window + 1:i + 1].transpose(1, 0, 2))
            local.append(self.local[i])
            table = self.keys[split]
            labels.append(table[table.time_utc == hour].assign(batch_index=b))
        raw_t, raw_s = np.stack(temporal), np.stack(local)
        t, s = preprocessing.transform(raw_t, raw_s)
        lab = pd.concat(labels, ignore_index=True)
        return dict(temporal=torch.from_numpy(t).to(device), local=torch.from_numpy(s).to(device),
                    edge_index=self.edges.to(device),
                    label_batch=torch.tensor(lab.batch_index.to_numpy(), dtype=torch.long, device=device),
                    label_node=torch.tensor(lab.node_index.to_numpy(dtype="int64"), dtype=torch.long, device=device),
                    label_values=torch.tensor(lab.value_ugm3.to_numpy(), dtype=torch.float32, device=device),
                    labels=lab)

    def identity(self):
        return dict(target=self.target, protocol=self.protocol, fold=self.fold,
                    held_out_group=self.held_out_group, snapshot_hashes=self.hashes,
                    training_nodes=self.training_nodes.tolist(),
                    sensor_stations=sorted(self.sensors.uuid.astype(str).tolist()))
