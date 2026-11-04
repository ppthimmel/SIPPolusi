"""Hourly edge exposure fields for MORL training, built from dataset ds-v0.3.0.

Run from the repo root, with the training requirements installed:

    python -m src.backend.route_model.morl.fields --out src/backend/route_model/runs/fields

Each selected time window becomes one `<window>.parquet` (edge_id, exposure_index)
through the Data Worker's own code path: IDW on a 100 m grid, then length-weighted
aggregation to edges (spatial_model.inference). This is the path production takes
when no registered model is available, so the policy trains on the kind of field
the cache really holds.

Time windows keep the blocks of ds-v0.3.0 (split_spec.json, 24 h gaps between
blocks), so test windows never reach training. Every station with a valid hourly
value is used: here the field is an input to routing, not a target being evaluated,
and production interpolates from all stations too.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import sys
from pathlib import Path

from ..astar_osm import DEFAULT_GRAPH
from ..training_env import GRAPH_FILES, file_sha256

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "docs/experiments/ds-v0.3.0"
DATA_WORKER = ROOT / "src/data_worker"
FIELDS_VERSION = "fields-1"
# Hours between selected windows, per split. The strides are not multiples of 24,
# so each block covers different hours of the day.
DEFAULT_STRIDES = {"train": 16, "val": 11, "test": 7}
POLLUTANTS = {"pm25": "pm25_ugm3", "no2": "no2_ugm3"}


def iso(moment) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def select_windows(split_spec: dict, strides: dict[str, int], available: set[str]) -> dict[str, list[str]]:
    """Every `stride` hours inside each block; hours without any label are skipped."""
    chosen = {}
    for split, block in split_spec["blocks"].items():
        moment = dt.datetime.fromisoformat(block["first_window"].replace("Z", "+00:00"))
        end = dt.datetime.fromisoformat(block["end_window_exclusive"].replace("Z", "+00:00"))
        windows = []
        while moment < end:
            if iso(moment) in available:
                windows.append(iso(moment))
            moment += dt.timedelta(hours=strides[split])
        chosen[split] = windows
    return chosen


def ground_truth_frame(labels, stations, window: str):
    """station_id, lon, lat, pollutant, value of one window; excluded hours are dropped."""
    import pandas as pd

    rows = labels[labels["window"] == window]
    frames = []
    for pollutant, column in POLLUTANTS.items():
        valid = rows[rows[f"{pollutant}_exclusion_reason"].isna() & rows[column].notna()]
        frames.append(pd.DataFrame({"station_id": valid["station_uuid"].astype(str), "pollutant": pollutant,
                                    "value": valid[column].astype(float)}))
    frame = pd.concat(frames, ignore_index=True).merge(stations, on="station_id", how="inner")
    return frame[["station_id", "lon", "lat", "pollutant", "value"]]


def road_edges(graph_dir: Path):
    """(graph_version, edge_ids, coords_lonlat, coord_edge_index) of the active graph."""
    import csv

    import numpy as np
    import shapely

    with (graph_dir / "graph_version.csv").open(newline="", encoding="utf-8-sig") as stream:
        version = next(row["version"] for row in csv.DictReader(stream) if row["status"] == "active")
    ids, wkts = [], []
    with gzip.open(graph_dir / "road_edge.csv.gz", "rt", newline="", encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["graph_version"] == version:
                ids.append(int(row["edge_id"]))
                wkts.append(row["geom_wkt"])
    order = np.argsort(ids)
    edge_ids = np.asarray(ids, dtype=np.int64)[order]
    geometries = shapely.from_wkt([wkts[i] for i in order])
    coords, index = shapely.get_coordinates(geometries, return_index=True)
    return version, edge_ids, coords, index


def build(out: Path, graph_dir: Path = DEFAULT_GRAPH, dataset: Path = DATASET, strides=None, limit=None) -> dict:
    if str(DATA_WORKER) not in sys.path:
        sys.path.insert(0, str(DATA_WORKER))
    import pandas as pd
    from contracts import JAKARTA_BBOX
    from spatial_model.configs import load_idw_settings
    from spatial_model.data.grid import GridSpec
    from spatial_model.inference.aggregate import aggregate_grid_to_edges, edge_cell_pieces
    from spatial_model.inference.fallback import run_idw_fallback

    split_spec = json.loads((dataset / "split_spec.json").read_text(encoding="utf-8"))
    labels = pd.read_parquet(dataset / "labels.parquet")
    labels["window"] = labels["time_window_start"].map(iso)
    stations = pd.read_parquet(dataset / "stations.parquet", columns=["uuid", "lat", "lng"]).rename(
        columns={"uuid": "station_id", "lng": "lon"})
    stations["station_id"] = stations["station_id"].astype(str)

    settings = load_idw_settings()
    spec = GridSpec.from_bbox(JAKARTA_BBOX.as_tuple(), settings["cell_m"])
    version, edge_ids, coords, index = road_edges(Path(graph_dir))
    pieces, edge_table = edge_cell_pieces(edge_ids, coords, index, spec)

    windows = select_windows(split_spec, strides or DEFAULT_STRIDES, set(labels["window"]))
    if limit:
        windows = {split: names[:limit] for split, names in windows.items()}
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    entries = {}
    for split, names in windows.items():
        for window in names:
            truth = ground_truth_frame(labels, stations, window)
            grid = run_idw_fallback(truth, spec, power=settings["power"], neighbors=settings["neighbors"])
            weights, _ = aggregate_grid_to_edges(grid, pieces, edge_table)
            frame = weights[["edge_id", "exposure_index"]].dropna().sort_values("edge_id")
            frame["exposure_index"] = frame["exposure_index"].round(6)
            path = out / (window.replace(":", "") + ".parquet")
            frame.to_parquet(path, index=False)
            # Hash the values, not the file: Parquet bytes vary with the writer's version.
            digest = hashlib.sha256(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()).hexdigest()
            entries[window] = {"split": split, "file": path.name, "edges": len(frame), "values_sha256": digest,
                               "stations": {p: int((truth["pollutant"] == p).sum()) for p in POLLUTANTS},
                               "exposure_index": {"min": float(frame["exposure_index"].min()),
                                                  "median": float(frame["exposure_index"].median()),
                                                  "max": float(frame["exposure_index"].max())}}
            print(f"{window} {split}: {len(frame)} edges, median index {entries[window]['exposure_index']['median']:.3f}",
                  flush=True)
    manifest = {
        "fields_version": FIELDS_VERSION,
        "dataset": {"version": split_spec["version"],
                    "labels_sha256": file_sha256(dataset / "labels.parquet"),
                    "split_spec_sha256": file_sha256(dataset / "split_spec.json")},
        "graph_version": version,
        "graph_files_sha256": {name: file_sha256(Path(graph_dir) / name) for name in GRAPH_FILES},
        "estimator": {"method": "idw", "power": settings["power"], "neighbors": settings["neighbors"],
                      "cell_m": settings["cell_m"], "config_sha256": settings["config_sha256"]},
        "strides_hours": strides or DEFAULT_STRIDES,
        "window_splits": windows,
        "windows": entries,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--graph-dir", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--limit", type=int, help="at most this many windows per split (quick trial)")
    args = parser.parse_args()
    manifest = build(args.out, args.graph_dir, limit=args.limit)
    print(json.dumps({split: len(names) for split, names in manifest["window_splits"].items()}))


if __name__ == "__main__":
    main()
