"""Summarize a completed ST-GNN LOSO batch without fitting any model."""

import argparse
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def paired_bootstrap(predictions, baseline, n_boot=4000):
    errors = predictions.assign(model_sq=(predictions.predicted-predictions.observed)**2,
                                baseline_sq=(predictions[baseline]-predictions.observed)**2)
    groups = errors.groupby("group_id").agg(a=("model_sq", "sum"), b=("baseline_sq", "sum"), n=("observed", "size"))
    indices = np.random.default_rng(0).integers(0, len(groups), size=(n_boot, len(groups)))
    count = groups.n.to_numpy()[indices].sum(axis=1)
    difference = np.sqrt(groups.a.to_numpy()[indices].sum(axis=1)/count) - np.sqrt(groups.b.to_numpy()[indices].sum(axis=1)/count)
    return np.percentile(difference, [2.5, 97.5]).tolist()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    root = args.run.resolve()
    plan = json.loads((root / "batch_manifest.json").read_text(encoding="utf-8"))
    if plan["status"] != "completed" or not all(f["status"] == "completed" for f in plan["folds"]):
        raise ValueError("Run is not complete; partial metrics remain in its output folder")
    manifests = []
    for fold in plan["folds"]:
        manifest = json.loads((Path(fold["output"]) / "run_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"
        assert manifest["settings"]["FOLD"] == fold["fold"]
        assert manifest["held_out_group"] == fold["group_id"]
        assert manifest["settings"]["PROTOCOL"] == "loso"
        assert manifest["settings"]["TARGET"] == "pm25"
        assert manifest["settings"]["SEED"] == 42
        assert manifest["settings"]["DEVICE"] == "cuda" and manifest["cached_inputs"]
        manifests.append(manifest)
    for manifest in manifests[1:]:
        for key in ("snapshot_hashes", "model_config", "versions", "temporal_columns", "local_columns", "gpu"):
            assert manifest[key] == manifests[0][key], f"Mixed experiment inputs: {key}"
    prediction = pd.read_parquet(root / "test_predictions.parquet")
    expected = sum(f["test_labels"] for f in plan["folds"])
    assert len(prediction) == expected
    assert prediction.group_id.nunique() == len(plan["folds"])
    assert not prediction.duplicated(["station_uuid", "time_utc"]).any()
    full = len(plan["folds"]) == plan["expected_all_groups"]
    if full:
        assert expected == plan["expected_all_test_labels"]
        assert prediction.station_uuid.nunique() == plan["expected_all_stations"]
    pooled = pd.read_csv(root / "pooled_metrics.csv")
    training = pd.read_csv(root / "fold_training.csv")
    station = pd.read_csv(root / "station_metrics.csv")
    intervals = []
    for name, column in (("IDW p2 k8", "idw_prediction"), ("IDW p1 all", "idw_p1_all")):
        low, high = paired_bootstrap(prediction, column)
        base = pooled.set_index("method").loc[name, "rmse"]
        model = pooled.set_index("method").loc["IDW-ST-GNN", "rmse"]
        intervals.append(dict(baseline=name, rmse_difference=float(model-base), ci_low=low, ci_high=high))
    comparison = pd.DataFrame(intervals)
    comparison.to_csv(root / "paired_bootstrap.csv", index=False)
    destination = HERE / "stgnn_idw_residual" / ("ds-v0.3.0_" + root.name)
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("batch_manifest.json", "pooled_metrics.csv", "station_metrics.csv", "fold_metrics.csv", "fold_training.csv", "paired_bootstrap.csv"):
        shutil.copyfile(root / name, destination / name)
    report = f'''# IDW-ST-GNN PM2.5 LOSO on ds-v0.3.0

- Coverage: {len(plan['folds'])} held-out groups, {prediction.station_uuid.nunique()} stations, {len(prediction)} station-hour observations.
- Full test coverage: {full}; seed 42; RTX 3050 GPU; forecast and calendar inputs included.
- Each held-out group is excluded from train labels, validation labels, feature fitting and every sensor input.
- Checkpoint selection uses validation from the other groups, never test metrics.
- Configuration is fixed across folds; no per-fold hyperparameter search.
- GPU input caching is checked against original preprocessing before training each fold.
- Complete artifacts/checkpoints: `{root.as_posix()}` (local; not committed).
- Reproduce: `python run_stgnn_loso.py --all`; resume an interrupted batch with `--resume <run directory>`.

## Pooled test metrics

| Method | n | MAE | RMSE | Bias | R2 |
|---|---:|---:|---:|---:|---:|
'''
    for row in pooled.itertuples():
        report += f'| {row.method} | {row.n} | {row.mae:.6f} | {row.rmse:.6f} | {row.bias:.6f} | {row.r2:.6f} |\n'
    report += '\n## Paired comparison\n\nBootstrap resamples held-out station groups (4,000 draws, seed 0). Differences are RMSE(ST-GNN) - RMSE(baseline).\n\n'
    for row in comparison.itertuples():
        report += f'- Versus {row.baseline}: {row.rmse_difference:+.4f}; 95% interval [{row.ci_low:+.4f}, {row.ci_high:+.4f}] ug/m3.\n'
    report += f'\nBest epoch median: {training.best_epoch.median():.1f}; range {training.best_epoch.min()}–{training.best_epoch.max()}. '
    report += f'{int((training.best_epoch == 0).sum())} folds selected zero correction (plain IDW).\n'
    for baseline in ("IDW p2 k8", "IDW p1 all"):
        paired = station.pivot(index="station_uuid", columns="method", values="rmse")
        better = int((paired["IDW-ST-GNN"] < paired[baseline]).sum())
        report += f'\nStation RMSE improved at {better}/{len(paired)} stations versus {baseline}.\n'
    report += '\nThis is one seed on a previously inspected dataset. Confidence intervals do not include training-seed variability, and this run is exploratory.\n'
    (destination / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print("Saved report:", destination / "report.md")


if __name__ == "__main__":
    main()
