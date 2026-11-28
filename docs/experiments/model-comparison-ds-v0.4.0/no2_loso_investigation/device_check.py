"""Device/version check for NO2 XGBoost on the notebook design (built on CPU by no2_controlled.py).

Run with the full xgboost 3.2.0 wheel (CUDA build) first on the path, e.g.
    pip install --no-deps --target <dir> xgboost==3.2.0
    PYTHONPATH=<dir> python docs/experiments/model-comparison-ds-v0.4.0/no2_loso_investigation/device_check.py         <controlled output dir> <Colab runs/ copy> <out dir>
<Colab runs/ copy>: the notebook's RUN_ROOT (xgboost_idwcorr/ and lightgbm/ with no2/loso/fold-N/ flattened to fold-N/).
1. Colab per-row predictions (XGBoost 3.4.1, cuda) joined to the local design: Colab metrics recomputed.
2. Colab fold-0 model.json predicted locally on the local design rows -> input identity check.
3. XGBoost 3.2.0 trained on cpu and on cuda, seeds 42/1/2/3/4, XGBoost + IDW corr and XGBoost direct.
argv: <controlled output dir> <colab runs dir> <out dir>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

CTRL, COLAB, OUT = (Path(a) for a in sys.argv[1:4])
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src/data_worker"))
from spatial_model.evaluation.metrics import compute_metrics  # noqa: E402
from spatial_model.models.lightgbm.data import CORE, feature_groups  # noqa: E402

COLS = CORE + [c for g in feature_groups("no2").values() for c in g]
HP = dict(n_estimators=600, learning_rate=0.03, max_depth=4, min_child_weight=20, subsample=0.8,
          colsample_bytree=0.5, reg_lambda=5.0, tree_method="hist")
KEYS = ["split", "hour", "station"]
log = open(OUT / "log.txt", "a", encoding="utf-8")


def say(*a):
    m = " ".join(str(x) for x in a)
    print(m, flush=True)
    log.write(m + "\n")
    log.flush()


say("xgboost", xgb.__version__, "cuda build", xgb.build_info().get("USE_CUDA"))
ev = pd.read_parquet(CTRL / "notebook_cpu_eval.parquet")


def pooled(frame, col):
    return {s: compute_metrics(g.observed, g[col]) for s, g in frame.groupby("split")}


# 1. Colab predictions on the local rows
for model in ("xgboost_idwcorr", "lightgbm"):
    parts = []
    for f in sorted((COLAB / model).glob("fold-*/predictions.parquet")):
        parts.append(pd.read_parquet(f).assign(fold=int(f.parent.name.split("-")[1])))
    colab = pd.concat(parts, ignore_index=True).rename(columns={"predicted": f"colab_{model}"})
    before = len(ev)
    ev = ev.merge(colab, on=KEYS + ["fold"], how="left", validate="one_to_one")
    assert len(ev) == before and ev[f"colab_{model}"].notna().all(), f"{model}: key sets differ"
    say(f"Colab {model}: all {len(colab)} rows matched local keys;",
        {s: round(m["rmse"], 3) for s, m in pooled(ev, f'colab_{model}').items()})

# 2. Colab fold-0 model on local fold-0 rows
booster = xgb.Booster()
booster.load_model(str(COLAB / "xgboost_idwcorr/fold-0/model.json"))
rows = ev[ev.fold == 0]
raw = booster.predict(xgb.DMatrix(rows[COLS].astype("float64"), feature_names=COLS))
mine = np.maximum(rows.idw_p2_k8.to_numpy() + raw, 0)
diff = np.abs(mine - rows.colab_xgboost_idwcorr.to_numpy())
say(f"Colab fold-0 model predicted here: max |diff| vs Colab predictions {diff.max():.2e} over {len(rows)} rows")

# 3. cpu vs cuda, same library version, same design
records, keep_preds = [], []
for corr in (True, False):
    name = "XGBoost + IDW corr" if corr else "XGBoost"
    for device in ("cpu", "cuda"):
        for seed in (42, 1, 2, 3, 4):
            ev["p"] = np.nan
            for fold in sorted(ev.fold.unique()):
                train = pd.read_parquet(CTRL / f"nb_cache/no2/loso/fold-{int(fold)}/train.parquet")
                y = train.y - train.idw_p2_k8 if corr else train.y
                ok = np.isfinite(y.to_numpy())
                model = xgb.XGBRegressor(**HP, device=device, random_state=seed, n_jobs=8)
                model.fit(train.loc[ok, COLS].astype("float64"), y[ok].to_numpy("float32"))
                sel = ev.fold == fold
                base = ev.loc[sel, "idw_p2_k8"].to_numpy() if corr else 0
                ev.loc[sel, "p"] = np.maximum(base + model.predict(ev.loc[sel, COLS].astype("float64")), 0)
            m = pooled(ev, "p")
            rec = dict(model=name, device=device, seed=seed, val_rmse=m["val"]["rmse"], test_rmse=m["test"]["rmse"])
            if corr and seed == 42:
                rec["max_abs_diff_vs_colab"] = float(np.abs(ev.p - ev.colab_xgboost_idwcorr).max())
                rec["rmse_diff_vs_colab"] = float(np.sqrt(np.mean((ev.p - ev.colab_xgboost_idwcorr) ** 2)))
                keep_preds.append(ev[KEYS + ["fold", "p"]].rename(columns={"p": f"{device}_seed42"}))
            records.append(rec)
            say(json.dumps(rec))
table = pd.DataFrame(records)
table.to_csv(OUT / "device_seeds.csv", index=False)
say(table.groupby(["model", "device"])[["val_rmse", "test_rmse"]].agg(["mean", "std", "min", "max"]).round(3).to_string())
