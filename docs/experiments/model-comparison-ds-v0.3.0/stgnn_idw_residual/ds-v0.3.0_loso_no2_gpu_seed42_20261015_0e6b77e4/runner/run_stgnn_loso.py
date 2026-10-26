"""Resumable GPU LOSO for the standalone IDW-ST-GNN notebook; no hyperparameter search.

python run_stgnn_loso.py --all                       # PM2.5 (default)
python run_stgnn_loso.py --target no2 --all --out /content/drive/MyDrive/stgnn_runs
python run_stgnn_loso.py --resume <existing run directory>
"""

import argparse
import gc
import hashlib
import json
import shutil
import time
from pathlib import Path
from uuid import uuid4

from run_stgnn import HERE, REPO, NOTEBOOK, main as train_fold

import numpy as np
import pandas as pd
from pyproj import Transformer
import torch

SNAPSHOT = REPO / "docs/experiments/ds-v0.3.0"


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def metrics(observed, predicted):
    error = np.asarray(predicted) - np.asarray(observed)
    variation = np.sum((observed - np.mean(observed)) ** 2)
    return dict(n=len(error), mae=float(np.mean(np.abs(error))), rmse=float(np.sqrt(np.mean(error ** 2))),
                bias=float(np.mean(error)), r2=float(1 - np.sum(error ** 2) / variation) if variation > 0 else None)


def idw_reference(labels, stations, target):
    """Tuned IDW p=1/all at test stations, excluding their entire groups at each hour."""
    stations = stations.set_index("uuid")
    records = []
    transform = Transformer.from_crs("EPSG:4326", "EPSG:32748", always_xy=True)
    column = f"{target}_ugm3"
    valid = labels[(labels.period == "test") & labels[column].notna()]
    for hour, table in valid.groupby("time_utc", sort=True):
        locations = stations.loc[table.station_uuid]
        x, y = transform.transform(locations.lng.to_numpy(), locations.lat.to_numpy())
        xy = np.column_stack([x, y])
        distance = np.linalg.norm(xy[:, None] - xy[None, :], axis=-1)
        allowed = table.group_id.to_numpy()[:, None] != table.group_id.to_numpy()[None, :]
        weight = np.where(allowed, 1 / np.maximum(distance, 1.0), 0)
        estimate = (weight * table[column].to_numpy()[None, :]).sum(axis=1) / weight.sum(axis=1)
        frame = table[["station_uuid", "group_id", "time_utc", column]].copy()
        frame = frame.rename(columns={column: "observed"})
        frame["idw_p1_all"] = estimate
        records.append(frame)
    reference = pd.concat(records, ignore_index=True)
    assert not reference.duplicated(["station_uuid", "time_utc"]).any()
    published = pd.read_csv(HERE / f"results/loso_{target}_metrics.csv").set_index("model")
    actual = metrics(reference.observed.to_numpy(), reference.idw_p1_all.to_numpy())
    assert actual["n"] == int(published.loc["idw_p1_all", "n"])
    np.testing.assert_allclose(actual["rmse"], published.loc["idw_p1_all", "rmse"], rtol=1e-8)
    return reference


def aggregate(root, plan, reference):
    completed = [entry for entry in plan["folds"] if entry["status"] == "completed"]
    if not completed:
        return
    frames = []
    histories = []
    for entry in completed:
        directory = Path(entry["output"])
        prediction = pd.read_parquet(directory / "test_predictions.parquet")
        prediction = prediction.rename(columns={"observed": "model_observed"}).merge(
            reference, on=["station_uuid", "time_utc"], validate="one_to_one")
        assert len(prediction) == entry["test_labels"]
        assert (prediction.group_id == entry["group_id"]).all()
        np.testing.assert_array_equal(prediction.model_observed, prediction.observed)
        assert np.isfinite(prediction[["predicted", "idw_prediction", "idw_p1_all"]]).all().all()
        prediction["fold"] = entry["fold"]
        frames.append(prediction)
        histories.append(dict(fold=entry["fold"], group_id=entry["group_id"],
                              epochs_run=entry["epochs_run"], best_epoch=entry["best_epoch"],
                              elapsed_seconds=entry["elapsed_seconds"]))
    predictions = pd.concat(frames, ignore_index=True)
    assert not predictions.duplicated(["station_uuid", "time_utc"]).any()
    columns = {"IDW p2 k8": "idw_prediction", "IDW p1 all": "idw_p1_all", "IDW-ST-GNN": "predicted"}
    pooled, per_station, per_fold = [], [], []
    for method, column in columns.items():
        pooled.append(dict(method=method, **metrics(predictions.observed.to_numpy(), predictions[column].to_numpy())))
        for station, table in predictions.groupby("station_uuid", sort=True):
            per_station.append(dict(method=method, station_uuid=station,
                **metrics(table.observed.to_numpy(), table[column].to_numpy())))
        for fold, table in predictions.groupby("fold", sort=True):
            per_fold.append(dict(method=method, fold=int(fold),
                **metrics(table.observed.to_numpy(), table[column].to_numpy())))
    pd.DataFrame(pooled).to_csv(root / "pooled_metrics.csv", index=False)
    pd.DataFrame(per_station).to_csv(root / "station_metrics.csv", index=False)
    pd.DataFrame(per_fold).to_csv(root / "fold_metrics.csv", index=False)
    pd.DataFrame(histories).to_csv(root / "fold_training.csv", index=False)
    predictions.to_parquet(root / "test_predictions.parquet", index=False)
    print(pd.DataFrame(pooled).round(4).to_string(index=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--fold-count", type=int, default=5)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--target", choices=("pm25", "no2"), default="pm25")
    parser.add_argument("--data", type=Path, default=SNAPSHOT, help="ds-v0.3.0 snapshot folder")
    parser.add_argument("--out", type=Path, default=REPO / "graphify-out/stgnn-ds-v0.3.0", help="Parent folder for runs")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--epochs", type=int, help="Smoke tests only; the notebook default is used otherwise")
    args = parser.parse_args()
    snapshot = args.data.resolve()
    labels = pd.read_parquet(snapshot / "labels.parquet")
    stations = pd.read_parquet(snapshot / "stations.parquet")
    test = labels[(labels.period == "test") & labels[f"{args.target}_ugm3"].notna()]
    folds = pd.read_parquet(snapshot / "splits/loso_folds.parquet")
    folds = folds[(folds.target == args.target) & folds.group_id.isin(test.group_id.unique())]
    folds = folds[["fold", "group_id"]].drop_duplicates().sort_values("fold")
    fingerprints = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (NOTEBOOK, HERE / "run_stgnn.py", Path(__file__))}
    if args.resume:
        root = args.resume.resolve()
        plan = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
        args.target = plan.get("target", args.target)
        args.device = plan.get("device", args.device)
        if plan["source_hashes"] != fingerprints:
            raise ValueError("Training source changed; start a new run instead of mixing implementations")
    else:
        if not args.all:
            if not 1 <= args.fold_count <= len(folds):
                parser.error("Invalid fold count")
            indices = np.random.default_rng(42).choice(len(folds), args.fold_count, replace=False)
            folds = folds.iloc[np.sort(indices)]
        root = args.out.resolve() / f"loso_{args.target}_{args.device}_seed42_{time.strftime('%Y%m%d')}_{uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=False)
        plan = dict(status="running", target=args.target, seed=42, device=args.device, source_hashes=fingerprints,
                    expected_all_groups=int(test.group_id.nunique()), expected_all_stations=int(test.station_uuid.nunique()),
                    expected_all_test_labels=len(test), folds=[dict(fold=int(row.fold), group_id=row.group_id,
                        test_labels=int((test.group_id == row.group_id).sum()), status="pending") for row in folds.itertuples()])
        for path in (NOTEBOOK, HERE / "run_stgnn.py", Path(__file__)):
            shutil.copyfile(path, root / path.name)
        write_json(root / "batch_manifest.json", plan)
    reference = idw_reference(labels, stations, args.target)
    started = time.perf_counter()
    print("LOSO output:", root, "| Planned folds:", len(plan["folds"]), flush=True)
    try:
        for index, entry in enumerate(plan["folds"]):
            if entry["status"] == "completed":
                continue
            for name, digest in fingerprints.items():
                if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != digest:
                    raise ValueError("Source changed during training")
            if entry.get("output"):
                entry.setdefault("previous_attempts", []).append(entry["output"])
            output = root / f"fold_{entry['fold']:03d}_{uuid4().hex[:8]}"
            entry.update(status="running", output=str(output))
            plan.update(status="running", current_fold=entry["fold"])
            write_json(root / "batch_manifest.json", plan)
            print(f"Starting fold {index+1}/{len(plan['folds'])}: {entry['fold']} {entry['group_id']}", flush=True)
            try:
                fold_args = ["--protocol", "loso", "--target", args.target, "--fold", str(entry["fold"]),
                             "--seed", "42", "--output", str(output), "--cache-inputs",
                             "--data", str(snapshot), "--device", args.device]
                if args.epochs:
                    fold_args += ["--epochs", str(args.epochs)]
                train_fold(fold_args)
            except BaseException as error:
                entry.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed", error=repr(error))
                raise
            result = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
            assert result["status"] == "completed" and result["held_out_group"] == entry["group_id"]
            entry.update(status="completed", epochs_run=result["epochs_run"], best_epoch=result["best_epoch"],
                         elapsed_seconds=result["elapsed_seconds"])
            plan["completed_folds"] = sum(fold["status"] == "completed" for fold in plan["folds"])
            write_json(root / "batch_manifest.json", plan)
            aggregate(root, plan, reference)
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        plan["status"] = "completed"
    except BaseException:
        plan["status"] = "interrupted"
        raise
    finally:
        plan["last_session_seconds"] = time.perf_counter() - started
        write_json(root / "batch_manifest.json", plan)
    print("LOSO completed:", root, flush=True)


if __name__ == "__main__":
    main()
