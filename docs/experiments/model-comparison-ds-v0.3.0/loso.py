"""Leave-one-group-out on the test block (primary protocol) with the configurations chosen in fixed.py.

Fold k: every station of group k is removed from training rows AND from the sensor-derived features
(sources), training rows = all other stations x train block; prediction = group k x test block.

    python loso.py pm25        # or no2
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from common import OUT, Panel, fit_variogram, kriging_feature, metrics, save_json, spatial_features, station_bootstrap
from fixed import fit_predict, masked_idw
from models import design, feature_groups

T0 = time.time()
QUICK = os.environ.get("QUICK") == "1"
BLOCK = os.environ.get("BLOCK", "test")  # block held out for evaluation; "val" = configuration choice


def log(*a):
    print(f"[{time.time() - T0:7.0f}s]", *a, flush=True)


def remap_cols(cols, target):
    """PM2.5-selected columns translated to the NO2 target (pollutant-specific names swap)."""
    if target == "pm25":
        return cols
    swap = {"pm25": "no2", "no2": "pm25"}
    return [c.replace("pm25", "#").replace("no2", "pm25").replace("#", "no2") if "geoscf" in c else c for c in cols]


def run_fold(target, g, cfgs, ranking, rk_model, learned, interp, block="test"):
    p = Panel(target)
    test_rows = (p.period == block)[:, None] & np.isfinite(p.Y)
    allowed = p.group != g
    F = spatial_features(p, allowed)
    nug, rng = fit_variogram(p, p.period == "train", allowed)
    hold = test_rows & (p.group == g)[None, :]
    F["kriging_ok"] = kriging_feature(p, allowed, nug, rng, rows=hold)
    tr_rows = (p.period == "train")[:, None] & np.isfinite(p.Y) & allowed[None, :]
    dtr = design(p, F, tr_rows).reset_index(drop=True)
    dte = design(p, F, hold).reset_index(drop=True)
    pr = dte[["hour", "station_idx", "station", "group", "y"]].copy()
    for c in interp + ["kriging_ok"]:
        pr[c] = dte[c].to_numpy()
    for name in cfgs:
        c = cfgs[name]
        pr[name] = fit_predict(name.split("_")[0], c["hp"], remap_cols(c["cols"], target), c["target"], dtr, dte)
    # regression kriging: OOF residuals at all allowed source stations for test-block hours
    c = cfgs[rk_model]
    cols = remap_cols(c["cols"], target)
    src_rows = (p.period == block)[:, None] & np.isfinite(p.Y) & allowed[None, :]
    dsrc = design(p, F, src_rows).reset_index(drop=True)
    resid = np.full(p.Y.shape, np.nan)
    for tr_idx, _ in GroupKFold(5, shuffle=True, random_state=7).split(dtr, groups=dtr["group"]):
        fit_g = set(dtr["group"].iloc[tr_idx])
        h = ~dsrc["group"].isin(fit_g)
        if h.any():
            pp = fit_predict(rk_model, c["hp"], cols, c["target"], dtr.iloc[tr_idx], dsrc[h])
            resid[dsrc["hour"][h].to_numpy(), dsrc["station_idx"][h].to_numpy()] = dsrc["y"][h].to_numpy() - pp
    field = masked_idw(p, resid, allowed, hold)
    pr[f"rk_{rk_model}"] = np.maximum(pr[rk_model] + field[pr["hour"], pr["station_idx"]], 0)
    for k in (2, 3):
        pr[f"ens_top{k}"] = pr[ranking[:k]].mean(1)
    pr["ens_all"] = pr[learned].mean(1)
    return pr


def main(target):
    res = OUT / ("results_quick" if QUICK else "results")
    state = json.loads((res / "fixed_state.json").read_text(encoding="utf-8"))
    cfgs = state["best_cfg"]
    learned = ["ridge", "lgbm", "xgb", "rf", "et", "mlp"]
    ranking = state["ensemble_ranking_val"]
    rk_model = state["rk_model"]
    p = Panel(target)
    test_rows = (p.period == BLOCK)[:, None] & np.isfinite(p.Y)
    folds = sorted(set(p.group[test_rows.any(0)]))
    if QUICK:
        folds = folds[:3]
    interp = ["idw_p2_k8", "idw_p1_k8", "idw_p1_all", "idw_p2_all", "idw_p3_k4", "gauss_2km", "gauss_5km", "gauss_10km",
              "src_mean", "src_median", "nn_val", "ctm_add_idw", "ctm_mul_idw"]
    from joblib import Parallel, delayed
    jobs = int(os.environ.get("JOBS", "8"))
    out = Parallel(n_jobs=jobs, verbose=5)(delayed(run_fold)(target, g, cfgs, ranking, rk_model, learned, interp, BLOCK)
                                           for g in folds)
    pred = pd.concat(out, ignore_index=True)
    pred.to_parquet(res / f"loso_{target}{'' if BLOCK == 'test' else '_' + BLOCK}_predictions.parquet", index=False)
    table = []
    for c in [c for c in pred.columns if c not in ("hour", "station_idx", "station", "group", "y")]:
        row = {"model": c} | metrics(pred["y"], pred[c])
        lo, hi, better, k = station_bootstrap(pred, c)
        row |= {"d_rmse_vs_idw": row["rmse"] - metrics(pred["y"], pred["idw_p2_k8"])["rmse"], "ci_lo": lo, "ci_hi": hi,
                "stations_better": f"{better}/{k}"}
        table.append(row)
    table = pd.DataFrame(table).sort_values("rmse")
    table.to_csv(res / f"loso_{target}{'' if BLOCK == 'test' else '_' + BLOCK}_metrics.csv", index=False)
    pd.set_option("display.width", 250)
    print(table.round(3).to_string(index=False))
    log("done")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pm25")
