"""Seed spread of the NO2 LOSO comparison on CPU: XGBoost + IDW corr vs LightGBM (direct), production code paths.

Usage (repository root; snapshot ds-v0.4.0 and the design tables written by controlled.py):
    python docs/experiments/model-comparison-ds-v0.4.0/no2_loso_investigation/seeds.py <controlled output dir> <out dir> [n_seeds]

Models are built with the repository's make_model functions (the Worker's ``xgboost-cpu`` wheel, 8 threads) on the
notebook design tables, which controlled.py showed equal the repository design (keys, labels, 62 columns to 1e-13).
Each seed gives one pooled LOSO val/test RMSE over all held-out rows.
"""
import sys
from pathlib import Path

import torch  # noqa: F401  (Windows: load before other native runtimes)
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src/data_worker"))
from spatial_model.configs import load_model_config  # noqa: E402
from spatial_model.evaluation.metrics import compute_metrics  # noqa: E402
from spatial_model.models.lightgbm.data import CORE, feature_groups  # noqa: E402
from spatial_model.models.lightgbm.model import make_model as make_lgbm  # noqa: E402
from spatial_model.models.xgboost.model import library_version, make_model as make_xgb  # noqa: E402

ctrl, out = Path(sys.argv[1]), Path(sys.argv[2])
n = int(sys.argv[3]) if len(sys.argv) > 3 else 25
out.mkdir(parents=True, exist_ok=True)
seeds = [42] + [s for s in range(1, n + 5) if s != 42][: n - 1]
cols = CORE + [c for g in feature_groups("no2").values() for c in g]
ev = pd.read_parquet(ctrl / "notebook_cpu_eval.parquet")
folds = sorted(ev.fold.unique())
train = {f: pd.read_parquet(ctrl / f"nb_cache/no2/loso/fold-{int(f)}/train.parquet") for f in folds}
lgb_hp = load_model_config("lightgbm")["targets"]["no2"]["hp"]
xgb_hp = load_model_config("xgboost")["hp"]
variants = {"XGBoost + IDW corr": (lambda s: make_xgb(xgb_hp, seed=s, threads=8), True),
            "LightGBM": (lambda s: make_lgbm(lgb_hp, seed=s, threads=8), False)}
rows, keep = [], []
for name, (make, corr) in variants.items():
    for seed in seeds:
        pred = np.full(len(ev), np.nan)
        for f in folds:
            t = train[f]
            y = (t.y - t.idw_p2_k8) if corr else t.y
            ok = np.isfinite(y.to_numpy())
            model = make(seed).fit(t.loc[ok, cols].astype("float64").copy(),
                                   np.array(y[ok], dtype="float32", order="C", copy=True))
            sel = (ev.fold == f).to_numpy()
            base = ev.loc[sel, "idw_p2_k8"].to_numpy() if corr else 0
            pred[sel] = np.maximum(base + model.predict(ev.loc[sel, cols].astype("float64")), 0)
        for split in ("val", "test"):
            s = (ev.split == split).to_numpy()
            rows.append(dict(model=name, seed=seed, split=split, **compute_metrics(ev.observed[s], pred[s])))
        if seed in seeds[:2]:
            keep.append(pd.DataFrame({"model": name, "seed": seed, "row": np.arange(len(ev)), "predicted": pred}))
        print(name, seed, round(rows[-2]["rmse"], 3), flush=True)
table = pd.DataFrame(rows)
table.to_csv(out / "seeds.csv", index=False)
pd.concat(keep).to_parquet(out / "first_two_seeds_predictions.parquet", index=False)
summary = table.groupby(["model", "split"]).rmse.agg(["count", "mean", "std", "min", "max"])
summary.to_csv(out / "seeds_summary.csv")
print("xgboost", library_version())
print(summary.round(3).to_string())
val = table[table.split == "val"].groupby("model").rmse
d = val.mean()["LightGBM"] - val.mean()["XGBoost + IDW corr"]
se = float(np.sqrt((val.var() / val.count()).sum()))
print(f"val lead of XGBoost + IDW corr over LightGBM: {d:.3f} (standard error {se:.3f}, {d / se:.1f} SE)")
