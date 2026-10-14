"""NO2: which feature groups carry the LOSO gain? LightGBM with the all-feature tuned hyperparameters,
group-wise add-one (to CORE) and drop-one (from all), evaluated by LOSO on the val block and the
test block, plus gain importance of the all-feature model.

    python no2_ablation.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from common import OUT, Panel, metrics, spatial_features, station_bootstrap
from fixed import fit_predict
from loso import remap_cols
from models import CORE, design, feature_groups, make_model, TARGETS

RES = OUT / "results"


def fold(g, variants, hp, target_param):
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
        for name, cols in variants.items():
            pr[name] = fit_predict("lgbm", hp, cols, target_param, dtr, dte)
        out.append(pr)
    return pd.concat(out) if out else None


def main():
    state = json.loads((RES / "fixed_state.json").read_text(encoding="utf-8"))
    cfg = state["best_cfg"]["lgbm_allfeat_tuned"]
    hp, tp = cfg["hp"], cfg["target"]
    groups = feature_groups("no2")
    all_cols = CORE + [c for g in groups.values() for c in g]
    variants = {"core": list(CORE), "all": all_cols}
    for g, cols in groups.items():
        variants[f"core+{g}"] = CORE + cols
        variants[f"all-{g}"] = [c for c in all_cols if c not in cols]
    variants["core+cal+met"] = CORE + groups["cal"] + groups["met"]
    variants["core+cal+met+ctm"] = CORE + groups["cal"] + groups["met"] + groups["ctm_ana"] + groups["ctm_fcst"]
    p = Panel("no2")
    folds = sorted(set(p.group[np.isfinite(p.Y).any(0)]))
    pred = pd.concat([r for r in Parallel(n_jobs=8)(delayed(fold)(g, variants, hp, tp) for g in folds) if r is not None],
                     ignore_index=True)
    pred.to_parquet(RES / "no2_ablation_predictions.parquet", index=False)
    rows = []
    for name in ["idw_p2_k8", *variants]:
        r = {"variant": name}
        for b in ("val", "test"):
            d = pred[pred["block"] == b]
            m = metrics(d["y"], d[name])
            r |= {f"{b}_rmse": m["rmse"], f"{b}_bias": m["bias"], f"{b}_r2": m["r2"]}
            if name != "idw_p2_k8":
                lo, hi, better, k = station_bootstrap(d, name)
                r |= {f"{b}_ci": f"[{lo:+.1f}, {hi:+.1f}]", f"{b}_better": f"{better}/{k}"}
        rows.append(r)
    table = pd.DataFrame(rows).sort_values("val_rmse")
    table.to_csv(RES / "no2_ablation.csv", index=False)
    pd.set_option("display.width", 250)
    print(table.round(2).to_string(index=False))

    # gain importance of the all-feature model fitted on all NO2 stations x train block
    F = spatial_features(p, np.ones(len(p.group), bool))
    tr = (p.period == "train")[:, None] & np.isfinite(p.Y)
    d = design(p, F, tr).reset_index(drop=True)
    z = TARGETS[tp][0](d)
    ok = np.isfinite(z)
    m = make_model("lgbm", hp).fit(d.loc[ok, all_cols], z[ok])
    imp = pd.Series(m.booster_.feature_importance("gain"), index=all_cols)
    imp = (imp / imp.sum()).sort_values(ascending=False)
    imp.to_csv(RES / "no2_importance.csv")
    print(imp.head(15).round(3).to_string())


if __name__ == "__main__":
    main()
