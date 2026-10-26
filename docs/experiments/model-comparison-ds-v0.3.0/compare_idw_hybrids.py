"""Train residual XGBoost on all 95 LOSO folds and compare existing fixed/LOSO runs.

python compare_idw_hybrids.py
python compare_idw_hybrids.py --resume <run directory>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
import time

os.environ.setdefault("MKL_THREADING_LAYER", "SEQUENTIAL")
os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import pandas as pd
import xgboost as xgb

import common
from models import design
from run_xgboost_fixed import verify_snapshot_windows

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
FIXED_XGB = HERE / "xgboost_fixed/pm25_gpu_seed42_20261014_202354_090525"
FIXED_GNN = REPO / "graphify-out/stgnn-ds-v0.3.0/fcst_calendar_gpu_seed42_20261014_847559c6"
LOSO_GNN = REPO / "graphify-out/stgnn-ds-v0.3.0/loso_pm25_gpu_seed42_20261014_a7eabc1b"


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def compare(root):
    """Require identical keys, labels and IDW predictions before pooling metrics."""
    pairs = {
        "fixed_split": (pd.read_parquet(FIXED_XGB / "test_predictions.parquet"),
                        pd.read_parquet(FIXED_GNN / "test_predictions.parquet")),
        "loso": (pd.concat([pd.read_parquet(d / "test_predictions.parquet")
                            for d in sorted(root.glob("fold_*")) if d.is_dir()], ignore_index=True),
                 pd.read_parquet(LOSO_GNN / "test_predictions.parquet")),
    }
    output = HERE / "idw_hybrid_comparison" / root.name
    output.mkdir(parents=True, exist_ok=True)
    pooled, stations, intervals = [], [], []
    inputs = {}
    for protocol, (tree, graph) in pairs.items():
        keys = ["station_uuid", "time_utc"]
        assert not tree.duplicated(keys).any() and not graph.duplicated(keys).any()
        merged = tree.merge(graph, on=keys, suffixes=("_xgb", "_gnn"), validate="one_to_one")
        assert len(merged) == len(tree) == len(graph), protocol
        np.testing.assert_allclose(merged.y, merged.observed, rtol=0, atol=1e-10)
        np.testing.assert_allclose(merged.idw_p2_k8, merged.idw_prediction, rtol=1e-6, atol=1e-6)
        # Use the same IDW values for every paired comparison.
        frame = merged[keys + ["group", "y", "idw_p2_k8", "xgboost", "predicted"]].copy()
        frame = frame.rename(columns={"idw_p2_k8": "IDW", "xgboost": "IDW + XGBoost",
                                      "predicted": "IDW + ST-GNN"})
        expected = 689 if protocol == "fixed_split" else 4358
        assert len(frame) == expected
        for method in ("IDW", "IDW + XGBoost", "IDW + ST-GNN"):
            assert np.isfinite(frame[method]).all()
            pooled.append(dict(protocol=protocol, method=method, **common.metrics(frame.y, frame[method])))
            for station, rows in frame.groupby("station_uuid", sort=True):
                stations.append(dict(protocol=protocol, method=method, station_uuid=station,
                                     **common.metrics(rows.y, rows[method])))
        # Bootstrap held station groups, preserving within-group dependence.
        for method, baseline in (("IDW + XGBoost", "IDW"), ("IDW + ST-GNN", "IDW"),
                                 ("IDW + XGBoost", "IDW + ST-GNN")):
            boot = frame.rename(columns={"group": "station"})
            lo, hi, better, count = common.station_bootstrap(boot, method, base=baseline)
            intervals.append(dict(protocol=protocol, method=method, baseline=baseline,
                                  ci_low=lo, ci_high=hi, groups_better=better, groups_total=count))
        frame.to_parquet(root / f"{protocol}_comparison_predictions.parquet", index=False)
        inputs[protocol] = dict(n=len(frame), stations=frame.station_uuid.nunique(),
                                groups=frame.group.nunique(), identical_labels=True, identical_idw=True)
    metrics = pd.DataFrame(pooled)
    metrics.to_csv(output / "test_metrics.csv", index=False)
    pd.DataFrame(stations).to_csv(output / "station_metrics.csv", index=False)
    pd.DataFrame(intervals).to_csv(output / "paired_group_bootstrap.csv", index=False)
    pd.read_csv(root / "fold_training.csv").to_csv(output / "fold_training.csv", index=False)
    save_json(output / "verification.json", inputs)
    manifest = json.loads((root / "run_manifest.json").read_text())
    manifest["comparison_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (output / "comparison_runner.py").write_bytes(Path(__file__).read_bytes())
    save_json(output / "run_manifest.json", manifest)
    report = "# IDW hybrid comparison — PM2.5 ds-v0.3.0\n\n"
    report += metrics.round(4).to_markdown(index=False) + "\n\n"
    report += "## Protocol\n\n"
    report += (
        "IDW uses p=2 and at most 8 source stations at the current hour. Fixed split uses "
        "train stations as sources. LOSO holds out entire station groups from training, validation "
        "and every sensor-derived feature; other groups supply current-hour readings. "
        "Training uses the train time block; model selection uses the validation time block. "
        "Test uses the held group in the test time block. All three methods have identical test "
        "keys and observations within each protocol; fixed split and LOSO have different test populations.\n\n"
        "Training rows without any eligible IDW source are excluded, matching ST-GNN. "
        "Their counts are recorded per fold. Validation and test rows are never dropped.\n\n"
        "XGBoost learns a correction to IDW from 54 tabular features, including spatial interpolation, "
        "sensor geometry, satellite, weather, GEOS-CF analysis/forecast, calendar and coordinates. "
        "No row-shift lags are used. For LOSO, depths 2/4/6 and early stopping are selected separately "
        "on non-held groups' validation data. A zero correction is eligible. Learning rate=0.03, "
        "maximum 1500 trees, patience=50, min_child_weight=20, reg_lambda=10, subsample=0.8, "
        "colsample_bytree=0.8, seed=42. Fixed split reuses the validation-selected residual model "
        "from the earlier six-candidate direct/residual experiment.\n\n"
        "ST-GNN reuses the completed GPU runs: 58 temporal channels, 8 local channels, "
        "TCN + local MLP + GAT + residual regression. Thus this compares pipelines with different "
        "feature representations and selection budgets, not architecture alone.\n\n"
        "## Paired uncertainty\n\n")
    report += pd.DataFrame(intervals).round(4).to_markdown(index=False)
    report += (
        "\n\n95% percentile intervals for RMSE(method) − RMSE(baseline), using 4,000 paired "
        "resamples of station groups, seed=0. Negative values favor the hybrid. "
        "Intervals do not capture training-seed variability.\n\n"
        "## Interpretation\n\n"
        "ST-GNN has the lowest fixed-split MAE/RMSE among these pipelines. XGBoost has the lowest "
        "LOSO MAE/RMSE among these pipelines. Both LOSO hybrid-versus-IDW RMSE intervals "
        "include zero, so these point estimates do not establish a reliable improvement. "
        "For context, the previously evaluated IDW p=1/all baseline has LOSO RMSE 22.1785, "
        "below all three pipelines in the main table. The main table consistently uses p=2/k=8.\n\n"
        "## Limitations\n\nSingle seed and previously inspected test data: exploratory results. "
        "No new ST-GNN training was performed. The input representations and tuning budgets differ.\n")
    (output / "report.md").write_text(report, encoding="utf-8")
    print(metrics.to_string(index=False), flush=True)
    print("Report:", output / "report.md", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args()
    hashes = verify_snapshot_windows()
    common.verify_snapshot = verify_snapshot_windows
    p = common.Panel("pm25")
    columns = json.loads((FIXED_XGB / "run_manifest.json").read_text())["features"]
    source_hashes = {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                     for name in (Path(__file__).name, "common.py", "models.py", "run_xgboost_fixed.py")}
    folds = pd.read_parquet(common.SNAP / "splits/loso_folds.parquet")
    groups = set(p.group[np.isfinite(p.Y[p.period == "test"]).any(axis=0)])
    folds = folds[(folds.target == "pm25") & folds.group_id.isin(groups)]
    folds = folds[["fold", "group_id"]].drop_duplicates().sort_values("fold")
    assert len(folds) == 95
    root = args.resume or (REPO / "graphify-out/xgboost-ds-v0.3.0" /
                          datetime.now().strftime("loso_pm25_gpu_seed42_%Y%m%d_%H%M%S_%f"))
    if args.resume:
        manifest = json.loads((root / "run_manifest.json").read_text())
        assert manifest["source_hashes"] == source_hashes and manifest["snapshot_hashes"] == hashes
    else:
        root.mkdir(parents=True, exist_ok=False)
        for name in source_hashes:
            (root / name).write_bytes((HERE / name).read_bytes())
        manifest = dict(status="running", target="pm25", protocol="loso", seed=42,
                        expected_folds=95, snapshot_hashes=hashes, source_hashes=source_hashes,
                        features=columns, xgboost_version=xgb.__version__,
                        comparisons=dict(fixed_xgboost=str(FIXED_XGB), fixed_stgnn=str(FIXED_GNN),
                                         loso_stgnn=str(LOSO_GNN)), folds=[])
    save_json(root / "run_manifest.json", manifest)
    started = time.perf_counter()
    print("LOSO output:", root, flush=True)
    completed = {row["fold"] for row in manifest["folds"]}
    for row in folds.itertuples():
        if int(row.fold) in completed:
            continue
        fold_started = time.perf_counter()
        allowed = p.group != row.group_id
        fields = common.spatial_features(p, allowed)
        data = {}
        for split in ("train", "val", "test"):
            eligible = ~allowed if split == "test" else allowed
            mask = (p.period == split)[:, None] & np.isfinite(p.Y) & eligible[None, :]
            data[split] = design(p, fields, mask).reset_index(drop=True)
        assert row.group_id not in set(data["train"].group) | set(data["val"].group)
        assert set(data["test"].group) == {row.group_id}
        missing_train = ~np.isfinite(data["train"].idw_p2_k8)
        skipped_training_labels = int(missing_train.sum())
        data["train"] = data["train"].loc[~missing_train].reset_index(drop=True)
        for frame in data.values():
            assert len(frame) and np.isfinite(frame.idw_p2_k8).all()
            assert not np.isinf(frame[columns].to_numpy()).any()
        folder = root / f"fold_{int(row.fold):03d}"
        folder.mkdir(exist_ok=True)
        baseline_rmse = common.metrics(data["val"].y, data["val"].idw_p2_k8)["rmse"]
        selected = dict(max_depth=0, best_iteration=-1, validation_rmse=baseline_rmse, device="cuda:0")
        candidates = [selected.copy()]
        best_model = None
        for depth in (2, 4, 6):
            model = xgb.XGBRegressor(
                device="cuda", tree_method="hist", objective="reg:squarederror",
                n_estimators=1500, learning_rate=0.03, max_depth=depth, min_child_weight=20,
                reg_lambda=10, subsample=0.8, colsample_bytree=0.8, random_state=42,
                n_jobs=8, early_stopping_rounds=50, eval_metric="rmse")
            residual = {s: (d.y - d.idw_p2_k8).to_numpy() for s, d in data.items() if s != "test"}
            model.fit(data["train"][columns], residual["train"],
                      eval_set=[(data["val"][columns], residual["val"])], verbose=False)
            booster = model.get_booster()
            device = json.loads(booster.save_config())["learner"]["generic_param"]["device"]
            assert device.startswith("cuda"), device
            correction = booster.predict(xgb.DMatrix(data["val"][columns]),
                                         iteration_range=(0, model.best_iteration + 1))
            score = common.metrics(data["val"].y,
                                   np.maximum(data["val"].idw_p2_k8 + correction, 0))["rmse"]
            candidate = dict(max_depth=depth, best_iteration=model.best_iteration,
                             validation_rmse=score, device=device)
            candidates.append(candidate)
            if score < selected["validation_rmse"]:
                best_model, selected = booster, candidate.copy()
        # Only the selected model is evaluated on held-group test labels.
        test = data["test"]
        correction = 0 if best_model is None else best_model.predict(
            xgb.DMatrix(test[columns]), iteration_range=(0, selected["best_iteration"] + 1))
        prediction = np.maximum(test.idw_p2_k8 + correction, 0)
        assert np.isfinite(prediction).all()
        frame = test[["hour", "station_idx", "station", "group", "y", "idw_p2_k8"]].copy()
        frame["station_uuid"] = p.stations.uuid.to_numpy()[test.station_idx]
        frame["time_utc"] = p.hours[test.hour].to_numpy()
        frame["xgboost"] = prediction
        frame["fold"] = int(row.fold)
        frame.to_parquet(folder / "test_predictions.parquet", index=False)
        if best_model is not None:
            best_model.save_model(folder / "best.ubj")
        save_json(folder / "selection.json", dict(selected=selected, candidates=candidates))
        manifest["folds"].append(dict(fold=int(row.fold), group_id=row.group_id,
                                     test_labels=len(frame), **selected,
                                     skipped_training_labels=skipped_training_labels,
                                     elapsed_seconds=time.perf_counter() - fold_started))
        save_json(root / "run_manifest.json", manifest)
        pd.DataFrame(manifest["folds"]).to_csv(root / "fold_training.csv", index=False)
        print(f"Fold {len(manifest['folds']):02d}/95 | {row.group_id} | depth {selected['max_depth']} "
              f"| trees {selected['best_iteration'] + 1} | {time.perf_counter() - fold_started:.1f}s", flush=True)
    manifest.update(status="completed", elapsed_seconds_this_session=time.perf_counter() - started)
    save_json(root / "run_manifest.json", manifest)
    compare(root)


if __name__ == "__main__":
    main()
