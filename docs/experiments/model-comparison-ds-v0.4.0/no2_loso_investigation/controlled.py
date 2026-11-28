"""Controlled NO2 LOSO comparison: notebook code vs repository code, same snapshot, same machine (CPU).

1. Notebook NO2 code (cells 17/19/23/27/29 of train_all_models_colab.ipynb) run on CPU: design tables + 4 variants.
2. Repository design per fold compared with the notebook's (train and val/test tables): keys, label, 62 columns.
3. Repository design, 4 variants x 5 seeds (seed spread), pooled val/test metrics.
Usage (repository root, CPU; writes the design tables the other scripts read):
    python docs/experiments/model-comparison-ds-v0.4.0/no2_loso_investigation/controlled.py <ds-v0.4.0 snapshot> <out dir>
"""
import json
import os
import sys
import time
from pathlib import Path

import torch
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src/data_worker"))
SNAP, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
SEEDS = [42, 1, 2, 3, 4]
log = open(OUT / "log.txt", "a", encoding="utf-8")


def say(*a):
    msg = " ".join(str(x) for x in a)
    print(msg, flush=True)
    log.write(msg + "\n")
    log.flush()


# ---------------------------------------------------------------- 1. notebook code on CPU
nb = json.loads((ROOT / "notebooks/spatial_model/train_all_models_colab.ipynb").read_text(encoding="utf-8"))
src = {i: "".join(nb["cells"][i]["source"]) for i in (2, 17, 19, 23, 27, 29)}
ns = dict(np=np, pd=pd, torch=torch, os=os, json=json, Path=Path, SNAPSHOT=SNAP, CACHE=OUT / "nb_cache",
          RUN_ROOT=OUT / "nb_runs", DEVICE="cpu", loso_folds=pd.read_parquet(SNAP / "splits/loso_folds.parquet"),
          display=lambda *a: None)
settings = {}
exec(src[2], {}, settings)                       # XGB_PARAMS, SEED from the settings cell
ns.update(XGB_PARAMS=settings["XGB_PARAMS"], SEED=settings["SEED"])
for i in (17, 19, 23, 27, 29):
    exec(src[i], ns)
t = time.time()
ns["prepare"]("no2", "loso", "all")
say(f"notebook design built {time.time() - t:.0f}s")
for runner, corr in (("run_lightgbm", False), ("run_lightgbm", True), ("run_xgboost", False), ("run_xgboost", True)):
    t = time.time()
    ns[runner]("no2", "loso", idw_corr=corr)
    say(f"notebook {runner} idw_corr={corr} {time.time() - t:.0f}s")
nb_results = pd.DataFrame(ns["RESULTS"])
nb_results = nb_results[(nb_results.target == "no2") & (nb_results.protocol == "loso")]
nb_results.to_csv(OUT / "notebook_cpu_results.csv", index=False)
nb_eval = ns["EVAL"][("no2", "loso")]
nb_eval.to_parquet(OUT / "notebook_cpu_eval.parquet", index=False)
say(nb_results.pivot_table(index="model", columns="split", values="rmse").round(3).to_string())

# ---------------------------------------------------------------- 2./3. repository design and models
from spatial_model.data.features import Panel, spatial_features  # noqa: E402
from spatial_model.models.lightgbm.data import CORE, design, feature_groups  # noqa: E402
from spatial_model.models.lightgbm.model import make_model as make_lgbm  # noqa: E402
from spatial_model.models.xgboost.model import make_model as make_xgb  # noqa: E402
from spatial_model.configs import load_model_config  # noqa: E402
from spatial_model.evaluation.metrics import compute_metrics  # noqa: E402

p = Panel("no2", SNAP)
columns = CORE + [c for g in feature_groups("no2").values() for c in g]
assert columns == ns["feature_columns"]("no2"), "column order differs"
lgb_cfg = load_model_config("lightgbm")
lgb_hp = lgb_cfg["targets"]["no2"]["hp"]
xgb_hp = load_model_config("xgboost")["hp"]
assert lgb_hp | {} == {k: v for k, v in (ns["LGBM_BASE"] | ns["LGBM_HP"]["no2"]).items() if k in lgb_hp}
assert xgb_hp == settings["XGB_PARAMS"]
variants = {"LightGBM": ("lgbm", False), "LightGBM + IDW corr": ("lgbm", True),
            "XGBoost": ("xgb", False), "XGBoost + IDW corr": ("xgb", True)}
folds = pd.read_parquet(SNAP / "splits/loso_folds.parquet")
folds = folds[folds.target == "no2"].drop_duplicates("fold").sort_values("fold")
diffs, preds = [], []
for f in folds.itertuples():
    t = time.time()
    allowed = p.group != f.group_id
    F = spatial_features(p, allowed)
    train_rows = (p.period == "train")[:, None] & np.isfinite(p.Y) & allowed[None, :]
    d_train = design(p, F, train_rows)
    nb_train = ns["train_table"]("no2", "loso", f.fold)
    tables = {"train": (d_train, nb_train)}
    evals = []
    for split in ("val", "test"):
        rows = (p.period == split)[:, None] & np.isfinite(p.Y) & (p.group == f.group_id)[None, :]
        if rows.any():
            d = design(p, F, rows).assign(split=split, fold=f.fold)
            evals.append(d)
            tables[split] = (d, nb_eval[(nb_eval.fold == f.fold) & (nb_eval.split == split)].reset_index(drop=True))
    # 2. compare designs
    for name, (mine, theirs) in tables.items():
        row = dict(fold=f.fold, table=name, n_repo=len(mine), n_notebook=len(theirs))
        row["keys_equal"] = bool(len(mine) == len(theirs) and (mine.hour.to_numpy() == theirs.hour.to_numpy()).all()
                                 and (mine.station.to_numpy() == theirs.station.to_numpy()).all())
        y_nb = theirs["observed" if "observed" in theirs else "y"].to_numpy()
        row["y_equal"] = bool(row["keys_equal"] and np.array_equal(mine.y.to_numpy(), y_nb, equal_nan=True))
        worst, worst_col, nan_mismatch = 0.0, None, 0
        if row["keys_equal"]:
            for c in columns:
                a, b = mine[c].to_numpy(float), theirs[c].to_numpy(float)
                nan_mismatch += int((np.isnan(a) != np.isnan(b)).sum())
                ok = ~np.isnan(a) & ~np.isnan(b)
                if ok.any():
                    rel = np.max(np.abs(a[ok] - b[ok]) / np.maximum(1.0, np.abs(b[ok])))
                    if rel > worst:
                        worst, worst_col = float(rel), c
        row.update(max_rel_diff=worst, worst_column=worst_col, nan_mismatches=nan_mismatch)
        diffs.append(row)
    # 3. models on the repository design, several seeds
    ev = pd.concat(evals, ignore_index=True) if evals else None
    for seed in SEEDS:
        for name, (fam, corr) in variants.items():
            y = (d_train.y - d_train.idw_p2_k8) if corr else d_train.y
            keep = np.isfinite(y.to_numpy())
            model = (make_lgbm(lgb_hp, seed=seed) if fam == "lgbm" else make_xgb(xgb_hp, seed=seed))
            model.fit(d_train.loc[keep, columns].astype("float64").copy(),
                      np.array(y[keep], dtype="float32", order="C", copy=True))
            if ev is None:
                continue
            raw = model.predict(ev[columns].astype("float64"))
            base = ev.idw_p2_k8.to_numpy() if corr else 0
            preds.append(pd.DataFrame(dict(fold=f.fold, split=ev.split, hour=ev.hour, station=ev.station,
                                           observed=ev.y, seed=seed, model=name,
                                           predicted=np.maximum(base + raw, 0))))
    say(f"repo fold {f.fold} {time.time() - t:.0f}s")

diffs = pd.DataFrame(diffs)
diffs.to_csv(OUT / "design_comparison.csv", index=False)
preds = pd.concat(preds, ignore_index=True)
preds.to_parquet(OUT / "repo_cpu_predictions.parquet", index=False)
rows = []
for (model, seed, split), g in preds.groupby(["model", "seed", "split"]):
    rows.append(dict(model=model, seed=seed, split=split, **compute_metrics(g.observed, g.predicted)))
summary = pd.DataFrame(rows)
summary.to_csv(OUT / "repo_cpu_seeds.csv", index=False)
say("design: all keys equal", diffs.keys_equal.all(), "| all labels equal", diffs.y_equal.all(),
    "| max rel diff", diffs.max_rel_diff.max(), "| nan mismatches", diffs.nan_mismatches.sum())
say(summary.pivot_table(index=["model", "split"], columns="seed", values="rmse").round(3).to_string())
say(summary.groupby(["model", "split"]).rmse.agg(["mean", "std", "min", "max"]).round(3).to_string())
