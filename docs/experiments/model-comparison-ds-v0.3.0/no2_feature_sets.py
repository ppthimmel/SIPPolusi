"""NO2: do models other than LightGBM gain from the same features? LOSO on the val and test blocks.

Ridge, MLP, XGBoost, random forest and LightGBM, each with the hyperparameters already tuned for its family
(no new search), the direct target, and two feature sets: all 62 columns, and the IDW feature plus the 7
sensor-geometry columns. The feature set per model is chosen on the val block; test is reported.

    THREADS=1 python no2_feature_sets.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from common import OUT, Panel, metrics, spatial_features, station_bootstrap
from fixed import fit_predict
from models import CORE, design, feature_groups

RES = OUT / "results"
MODELS = {"ridge": "ridge", "mlp": "mlp", "xgb": "xgb", "rf": "rf", "lgbm": "lgbm_allfeat_tuned"}


def fold(g, sets, cfgs):
    p = Panel("no2")
    allowed = p.group != g
    F = spatial_features(p, allowed)
    tr = (p.period == "train")[:, None] & np.isfinite(p.Y) & allowed[None, :]
    dtr = design(p, F, tr).reset_index(drop=True)
    out = []
    for block in ("val", "test"):
        hold = (p.period == block)[:, None] & np.isfinite(p.Y) & (p.group == g)[None, :]
        if not hold.any():
            continue
        dte = design(p, F, hold).reset_index(drop=True)
        pr = dte[["hour", "station", "group", "y", "idw_p2_k8"]].assign(block=block)
        for model, key in MODELS.items():
            for set_name, cols in sets.items():
                pr[f"{model}|{set_name}"] = fit_predict(model, cfgs[key]["hp"], cols, "direct", dtr, dte)
        out.append(pr)
    return pd.concat(out) if out else None


def main():
    cfgs = json.loads((RES / "fixed_state.json").read_text(encoding="utf-8"))["best_cfg"]
    groups = feature_groups("no2")
    sets = {"all": CORE + [c for g in groups.values() for c in g], "idw_geom": CORE + groups["geom"]}
    p = Panel("no2")
    folds = sorted(set(p.group[np.isfinite(p.Y).any(0)]))
    parts = Parallel(n_jobs=8)(delayed(fold)(g, sets, cfgs) for g in folds)
    pred = pd.concat([r for r in parts if r is not None], ignore_index=True)
    pred.to_parquet(RES / "no2_feature_sets_predictions.parquet", index=False)
    rows = []
    for col in [c for c in pred.columns if "|" in c]:
        model, set_name = col.split("|")
        r = {"model": model, "features": set_name}
        for b in ("val", "test"):
            d = pred[pred["block"] == b]
            m = metrics(d["y"], d[col])
            r |= {f"{b}_rmse": m["rmse"], f"{b}_r2": m["r2"]}
        d = pred[pred["block"] == "test"]
        lo, hi, better, k = station_bootstrap(d, col)
        r |= {"test_ci_lo": lo, "test_ci_hi": hi, "test_better": f"{better}/{k}"}
        rows.append(r)
    table = pd.DataFrame(rows).sort_values(["model", "val_rmse"])
    table["selected_on_val"] = ~table.duplicated("model")
    table.to_csv(RES / "no2_feature_sets.csv", index=False)
    pd.set_option("display.width", 200)
    print(table.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
