"""NO2 LightGBM LOSO with the system training code: the maintained config against the previous one.

The previous configs/lightgbm.json trained NO2 with the hyperparameters of another comparison model
(best_cfg.lgbm_allfeat_tuned, a source-mean residual model) and the direct target of the selected one
(best_cfg.lgbm_allfeat_direct). This runs every NO2 LOSO fold with both and pools the predictions.
From src/data_worker:

    python ../../docs/experiments/TI-AI-05/no2_lightgbm_config_check.py <ds-v0.3.0> <new output dir>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import spatial_model.models.lightgbm.train as train_module
from spatial_model.configs import load_model_config

PREVIOUS_NO2_HP = {"n_estimators": 150, "learning_rate": 0.02, "num_leaves": 7, "min_child_samples": 400,
                   "subsample": 1.0, "colsample_bytree": 0.4, "reg_lambda": 1, "min_split_gain": 1.0}


def run(snapshot, out, variant):
    config = load_model_config("lightgbm")
    if variant == "previous":
        config["targets"]["no2"]["hp"] = PREVIOUS_NO2_HP
    train_module.load_model_config = lambda name: config
    folds = pd.read_parquet(snapshot / "splits/loso_folds.parquet")
    tables = {"val": [], "test": []}
    for fold in sorted(folds.loc[folds.target == "no2", "fold"].unique()):
        run_dir = out / variant / f"fold_{fold:02d}"
        train_module.train(snapshot, run_dir, fold=int(fold), target="no2")
        for split in tables:
            if (run_dir / f"{split}_predictions.parquet").exists():
                tables[split].append(pd.read_parquet(run_dir / f"{split}_predictions.parquet"))
    result = {"hp": config["targets"]["no2"]["hp"], "target": config["target"]}
    pooled = {}
    for split, parts in tables.items():
        t = pd.concat(parts, ignore_index=True)
        err = t.predicted - t.observed
        result[split] = {"rows": len(t), "stations": int(t.station.nunique()),
                         "rmse": round(float(np.sqrt((err ** 2).mean())), 4), "bias": round(float(err.mean()), 4)}
        pooled[split] = t.set_index(["hour", "station"]).sort_index()
    return result, pooled


def main(snapshot, out):
    snapshot, out = Path(snapshot), Path(out)
    report, pooled = {}, {}
    for variant in ("maintained", "previous"):
        report[variant], pooled[variant] = run(snapshot, out, variant)
    a, b = pooled["maintained"]["test"], pooled["previous"]["test"].loc[pooled["maintained"]["test"].index]
    gap = ((a.predicted - a.observed) ** 2 - (b.predicted - b.observed) ** 2).groupby(level="station").mean()
    rng, stations, diffs = np.random.default_rng(0), gap.index.to_numpy(), []
    for _ in range(4000):
        pick = rng.choice(stations, len(stations))
        ea = np.concatenate([(a.xs(s, level="station").predicted - a.xs(s, level="station").observed) for s in pick])
        eb = np.concatenate([(b.xs(s, level="station").predicted - b.xs(s, level="station").observed) for s in pick])
        diffs.append(np.sqrt((ea ** 2).mean()) - np.sqrt((eb ** 2).mean()))
    report["test_comparison"] = {
        "rmse_difference_maintained_minus_previous": round(report["maintained"]["test"]["rmse"] - report["previous"]["test"]["rmse"], 4),
        "station_bootstrap_95ci": [round(float(x), 2) for x in np.percentile(diffs, [2.5, 97.5])],
        "stations_better_with_maintained": f"{int((gap < 0).sum())}/{len(gap)}"}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:3])
