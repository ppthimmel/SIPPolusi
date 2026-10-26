"""PM2.5 fixed double-blind split: feature/target/hyperparameter selection by grouped CV on the
training pool (train stations x train block), model choice on val (val stations x val block),
single report on test (test stations x test block).

    python fixed.py            # writes results/fixed_*.{json,csv,parquet}
"""
from __future__ import annotations

import json
import sys
import time

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from common import OUT, Panel, fit_variogram, kriging_feature, metrics, save_json, spatial_features, station_bootstrap
from models import CORE, TARGETS, feature_groups, make_model, sample_configs

import os

QUICK = os.environ.get("QUICK") == "1"
RES = OUT / ("results_quick" if QUICK else "results")
RES.mkdir(exist_ok=True)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.0f}s]", *a, flush=True)


def cv_splits(groups, hours, n_splits=5, repeats=1 if QUICK else 3, buffer_h=24):
    """Spatio-temporal blocked CV (mirrors the double-blind split): fold f validates on
    (station groups of fold f) x (time chunk f) and trains on (other groups) x (hours outside
    chunk f +/- buffer). Plain station-grouped CV leaks: shared hours let trees learn each hour's
    field from the other stations' labels, which is impossible for new hours."""
    groups, hours = np.asarray(groups), np.asarray(hours)
    uh = np.unique(hours)
    chunks = np.array_split(uh, n_splits)
    out = []
    for r in range(repeats):
        gk = GroupKFold(n_splits, shuffle=True, random_state=100 + r)
        for f, (_, va_g) in enumerate(gk.split(groups, groups=groups)):
            vgroups = set(groups[va_g])
            ch = chunks[(f + r) % n_splits]
            in_g = np.isin(groups, list(vgroups))
            va = np.flatnonzero(in_g & np.isin(hours, ch))
            tr = np.flatnonzero(~in_g & ((hours < ch.min() - buffer_h) | (hours > ch.max() + buffer_h)))
            out.append((r, tr, va))
    return out


def fit_predict(name, hp, cols, target, dtr, dte):
    enc, dec = TARGETS[target]
    m = make_model(name, hp)
    z = enc(dtr)
    ok = np.isfinite(z)  # rows without any source station have no residual target
    m.fit(dtr.loc[ok, cols], z[ok])
    return np.maximum(dec(dte, m.predict(dte[cols])), 0.0)


def cv_rmse(name, hp, cols, target, d, splits):
    oof = np.full((max(s[0] for s in splits) + 1, len(d)), np.nan)
    for r, tr, va in splits:
        oof[r, va] = fit_predict(name, hp, cols, target, d.iloc[tr], d.iloc[va])
    y = d["y"].to_numpy()
    return float(np.mean([np.sqrt(np.nanmean((o - y) ** 2)) for o in oof]))


def masked_idw(p, values, allowed, rows_mask, power=2, k=8):
    """IDW of an arbitrary (H, N) field from allowed stations minus own group, for rows_mask cells."""
    out = np.full(values.shape, np.nan)
    src = np.isfinite(values) & allowed[None, :]
    for t in np.flatnonzero(rows_mask.any(1)):
        for i in np.flatnonzero(rows_mask[t]):
            s = np.flatnonzero(src[t] & ~p.same_group[i])
            if len(s) == 0:
                continue
            d = p.D[i, s]
            o = np.argsort(d)[:k]
            w = 1 / np.maximum(d[o], 1.0) ** power
            out[t, i] = (w * values[t, s[o]]).sum() / w.sum()
    return out


def main():
    target = "pm25"
    p = Panel(target)
    train_st = p.station_split == "train"
    F = spatial_features(p, train_st)
    nug, rng = fit_variogram(p, p.period == "train", train_st)
    F["kriging_ok"] = kriging_feature(p, train_st, nug, rng, rows=np.isfinite(p.Y))
    rows = {s: p.split == s for s in ("train", "val", "test")}
    D = {s: design(p, F, m) for s, m in rows.items()}
    dtr = D["train"].reset_index(drop=True)
    splits = cv_splits(dtr["group"].to_numpy(), dtr["hour"].to_numpy())
    groups = feature_groups(target)
    all_cols = CORE + [c for g in groups.values() for c in g]
    log("rows", {k: len(v) for k, v in D.items()}, "variogram", round(nug, 3), round(rng))
    state = {"variogram": {"nugget_fraction": nug, "range_m": rng}}
    va_all = np.unique(np.concatenate([v for _, _, v in splits]))
    state["cv_idw_p2_k8"] = float(np.sqrt(np.mean((dtr["idw_p2_k8"].iloc[va_all] - dtr["y"].iloc[va_all]) ** 2)))
    state["cv_rows"] = {"train_mean": float(np.mean([len(t) for _, t, _ in splits])),
                        "val_mean": float(np.mean([len(v) for _, _, v in splits]))}
    log("cv idw_p2_k8", round(state["cv_idw_p2_k8"], 3), state["cv_rows"])

    # A. target parameterisation (all features)
    state["target_choice"] = {}
    for name, hp in (("lgbm", {}), ("ridge", {"alpha": 10.0})):
        sc = {t: cv_rmse(name, hp, all_cols, t, dtr, splits) for t in TARGETS}
        state["target_choice"][name] = sc
        log("target", name, {k: round(v, 3) for k, v in sc.items()})
    tgt = {n: min(sc, key=sc.get) for n, sc in state["target_choice"].items()}

    # B. forward feature-group selection (stop when the CV gain is < 0.05 ug/m3)
    state["selection"] = {}
    chosen_groups = {}
    for name, hp in (("lgbm", {}), ("ridge", {"alpha": 10.0})):
        sel, cols = [], list(CORE)
        best = cv_rmse(name, hp, cols, tgt[name], dtr, splits)
        trace = [{"groups": [], "cv_rmse": best}]
        while True:
            cand = {g: cv_rmse(name, hp, cols + groups[g], tgt[name], dtr, splits) for g in groups if g not in sel}
            if not cand:
                break
            g, s = min(cand.items(), key=lambda kv: kv[1])
            log("select", name, sel, "->", {k: round(v, 3) for k, v in sorted(cand.items(), key=lambda kv: kv[1])})
            if best - s < 0.05:
                break
            sel.append(g)
            cols += groups[g]
            best = s
            trace.append({"groups": list(sel), "cv_rmse": s, "candidates": cand})
        state["selection"][name] = {"trace": trace, "all_groups_cv": cv_rmse(name, hp, all_cols, tgt[name], dtr, splits)}
        chosen_groups[name] = sel
        log("selected", name, sel, round(best, 3), "all groups", round(state["selection"][name]["all_groups_cv"], 3))
    state["chosen_groups"] = chosen_groups
    state["target"] = tgt
    save_json(state, RES / "fixed_state.json")

    # C. hyperparameter search
    family = {"lgbm": "lgbm", "xgb": "lgbm", "rf": "lgbm", "et": "lgbm", "ridge": "ridge", "mlp": "ridge"}
    best_cfg = {}
    for name in ("ridge", "lgbm", "xgb", "rf", "et", "mlp"):
        fam = family[name]
        cols = CORE + [c for g in chosen_groups[fam] for c in groups[g]]
        scores = []
        for hp in sample_configs(name)[:2 if QUICK else None]:
            s = cv_rmse(name, hp, cols, tgt[fam], dtr, splits)
            scores.append((s, hp))
        scores.sort(key=lambda x: x[0])
        best_cfg[name] = {"hp": scores[0][1], "cv_rmse": scores[0][0], "cols": cols, "target": tgt[fam],
                          "n_configs": len(scores)}
        log("tuned", name, round(scores[0][0], 3), scores[0][1])
        state["tuning"] = {k: {kk: vv for kk, vv in v.items() if kk != "cols"} for k, v in best_cfg.items()}
        save_json(state, RES / "fixed_state.json")
    # also the all-features LightGBM with tuned hyperparameters, for transparency
    best_cfg["lgbm_allfeat"] = best_cfg["lgbm"] | {"cols": all_cols,
                                                    "cv_rmse": cv_rmse("lgbm", best_cfg["lgbm"]["hp"], all_cols, tgt["lgbm"], dtr, splits)}
    log("lgbm all features", round(best_cfg["lgbm_allfeat"]["cv_rmse"], 3))

    # D. fit on the full training pool, predict val/test
    preds = {s: D[s][["hour", "station_idx", "station", "group", "y"]].copy() for s in ("val", "test")}
    interp = ["idw_p2_k8", "idw_p1_k8", "idw_p1_all", "idw_p2_all", "idw_p3_k4", "gauss_2km", "gauss_5km", "gauss_10km",
              "src_mean", "src_median", "nn_val", "kriging_ok", "ctm_add_idw", "ctm_mul_idw"]
    for s in preds:
        for c in interp:
            preds[s][c] = D[s][c].to_numpy()
        preds[s][f"geoscf_fcst_{target}_ugm3"] = D[s][f"geoscf_fcst_{target}_ugm3"].to_numpy()
    for name, cfg in best_cfg.items():
        model = name.split("_")[0]
        for s in preds:
            preds[s][name] = fit_predict(model, cfg["hp"], cfg["cols"], cfg["target"], dtr, D[s])
        log("fit", name, "val", round(metrics(preds["val"]["y"], preds["val"][name])["rmse"], 3))

    # E. regression kriging on the best learned model by CV: model + IDW of out-of-fold residuals at sources
    learned = ["ridge", "lgbm", "xgb", "rf", "et", "mlp"]
    best_learned = min(learned, key=lambda n: best_cfg[n]["cv_rmse"])
    cfg = best_cfg[best_learned]
    allrows = np.isfinite(p.Y) & train_st[None, :]
    dall = design(p, F, allrows).reset_index(drop=True)
    resid = np.full(p.Y.shape, np.nan)
    for tr_idx, va_idx in GroupKFold(5, shuffle=True, random_state=7).split(dtr, groups=dtr["group"]):
        fit_g = set(dtr["group"].iloc[tr_idx])
        hold = ~dall["group"].isin(fit_g)
        pr = fit_predict(best_learned, cfg["hp"], cfg["cols"], cfg["target"], dtr.iloc[tr_idx], dall[hold])
        resid[dall["hour"][hold].to_numpy(), dall["station_idx"][hold].to_numpy()] = dall["y"][hold].to_numpy() - pr
    for s in preds:
        m = rows[s]
        field = masked_idw(p, resid, train_st, m)
        preds[s][f"rk_{best_learned}"] = np.maximum(preds[s][best_learned] + field[preds[s]["hour"], preds[s]["station_idx"]], 0)
    log("rk", best_learned, "val", round(metrics(preds["val"]["y"], preds["val"][f"rk_{best_learned}"])["rmse"], 3))

    # F. ensembles chosen on val
    vr = {n: metrics(preds["val"]["y"], preds["val"][n])["rmse"] for n in learned}
    ranked = sorted(vr, key=vr.get)
    for k in (2, 3):
        for s in preds:
            preds[s][f"ens_top{k}"] = preds[s][ranked[:k]].mean(1)
    for s in preds:
        preds[s]["ens_all"] = preds[s][learned].mean(1)
    state["ensemble_ranking_val"] = ranked

    # G. metrics + station bootstrap on test
    table = []
    for c in [c for c in preds["val"].columns if c not in ("hour", "station_idx", "station", "group", "y")]:
        row = {"model": c}
        for s in ("val", "test"):
            mm = metrics(preds[s]["y"], preds[s][c])
            row |= {f"{s}_{k}": v for k, v in mm.items()}
        lo, hi, better, k = station_bootstrap(preds["test"], c)
        row |= {"test_d_rmse_vs_idw": row["test_rmse"] - metrics(preds["test"]["y"], preds["test"]["idw_p2_k8"])["rmse"],
                "test_ci_lo": lo, "test_ci_hi": hi, "test_stations_better": f"{better}/{k}"}
        if c in best_cfg:
            row["cv_rmse"] = best_cfg[c]["cv_rmse"]
        elif c in dtr:
            y = dtr["y"].to_numpy()
            row["cv_rmse"] = float(np.mean([np.sqrt(np.mean((dtr[c].to_numpy()[va] - y[va]) ** 2))
                                            for r in range(max(s[0] for s in splits) + 1)
                                            for va in [np.concatenate([v for rr, _, v in splits if rr == r])]]))
        else:
            row["cv_rmse"] = np.nan
        table.append(row)
    table = pd.DataFrame(table).sort_values("cv_rmse")
    table.to_csv(RES / "fixed_metrics.csv", index=False)
    for s in preds:
        preds[s].to_parquet(RES / f"fixed_{s}_predictions.parquet", index=False)
    state["best_cfg"] = {k: {kk: vv for kk, vv in v.items()} for k, v in best_cfg.items()}
    state["rk_model"] = best_learned
    save_json(state, RES / "fixed_state.json")
    pd.set_option("display.width", 250)
    print(table[["model", "cv_rmse", "val_rmse", "test_rmse", "test_mae", "test_bias", "test_r2", "test_d_rmse_vs_idw",
                 "test_ci_lo", "test_ci_hi", "test_stations_better"]].round(3).to_string(index=False))
    log("done")


from models import design  # noqa: E402  (after helpers for readability)

if __name__ == "__main__":
    main()
