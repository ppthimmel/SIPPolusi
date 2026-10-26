"""Robustness check for the feature-selection result of fixed.py.

fixed.py selects feature groups with a default LightGBM and tunes afterwards. A strongly regularised
model might still profit from groups the default model rejected, so here:
  1. LightGBM tuned on ALL feature groups (same random search, same blocked CV);
  2. forward group selection repeated with the tuned (regularised) LightGBM;
  3. ExtraTrees tuned on ALL feature groups (a very different inductive bias).
New configurations are appended to results/fixed_state.json (best_cfg) so loso.py evaluates them too.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import Panel, metrics, save_json, spatial_features, station_bootstrap
from fixed import RES, cv_rmse, cv_splits, fit_predict, log
from models import CORE, design, feature_groups, sample_configs


def main():
    target = "pm25"
    state = json.loads((RES / "fixed_state.json").read_text(encoding="utf-8"))
    p = Panel(target)
    F = spatial_features(p, p.station_split == "train")
    rows = {s: p.split == s for s in ("train", "val", "test")}
    D = {s: design(p, F, m) for s, m in rows.items()}
    dtr = D["train"].reset_index(drop=True)
    splits = cv_splits(dtr["group"].to_numpy(), dtr["hour"].to_numpy())
    groups = feature_groups(target)
    all_cols = CORE + [c for g in groups.values() for c in g]
    tgt = state["target"]["lgbm"]
    new = {}

    for name in ("lgbm", "et"):
        scores = sorted(((cv_rmse(name, hp, all_cols, tgt, dtr, splits), hp) for hp in sample_configs(name, seed=7)),
                        key=lambda x: x[0])
        new[f"{name}_allfeat_tuned"] = {"hp": scores[0][1], "cv_rmse": scores[0][0], "cols": all_cols, "target": tgt,
                                        "n_configs": len(scores)}
        log("tuned on all features", name, round(scores[0][0], 3), scores[0][1])

    hp = new["lgbm_allfeat_tuned"]["hp"]
    sel, cols = [], list(CORE)
    best = cv_rmse("lgbm", hp, cols, tgt, dtr, splits)
    trace = [{"groups": [], "cv_rmse": best}]
    while True:
        cand = {g: cv_rmse("lgbm", hp, cols + groups[g], tgt, dtr, splits) for g in groups if g not in sel}
        if not cand:
            break
        g, s = min(cand.items(), key=lambda kv: kv[1])
        log("regularised select", sel, "->", {k: round(v, 3) for k, v in sorted(cand.items(), key=lambda kv: kv[1])})
        if best - s < 0.05:
            break
        sel.append(g)
        cols += groups[g]
        best = s
        trace.append({"groups": list(sel), "cv_rmse": s, "candidates": cand})
    new["lgbm_regsel"] = {"hp": hp, "cv_rmse": best, "cols": cols, "target": tgt, "groups": sel}
    log("regularised selection", sel, round(best, 3))

    preds = {s: pd.read_parquet(RES / f"fixed_{s}_predictions.parquet") for s in ("val", "test")}
    for name, cfg in new.items():
        for s in preds:
            preds[s][name] = fit_predict(name.split("_")[0], cfg["hp"], cfg["cols"], cfg["target"], dtr, D[s])
    table = pd.read_csv(RES / "fixed_metrics.csv")
    rows_out = []
    for name in new:
        row = {"model": name, "cv_rmse": new[name]["cv_rmse"]}
        for s in ("val", "test"):
            row |= {f"{s}_{k}": v for k, v in metrics(preds[s]["y"], preds[s][name]).items()}
        lo, hi, better, k = station_bootstrap(preds["test"], name)
        row |= {"test_d_rmse_vs_idw": row["test_rmse"] - metrics(preds["test"]["y"], preds["test"]["idw_p2_k8"])["rmse"],
                "test_ci_lo": lo, "test_ci_hi": hi, "test_stations_better": f"{better}/{k}"}
        rows_out.append(row)
        log(name, {k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()})
    table = pd.concat([table[~table["model"].isin(new)], pd.DataFrame(rows_out)]).sort_values("cv_rmse")
    table.to_csv(RES / "fixed_metrics.csv", index=False)
    for s in preds:
        preds[s].to_parquet(RES / f"fixed_{s}_predictions.parquet", index=False)
    state["best_cfg"] |= new
    state["regularised_selection"] = {"trace": trace}
    save_json(state, RES / "fixed_state.json")
    log("done")


if __name__ == "__main__":
    main()
