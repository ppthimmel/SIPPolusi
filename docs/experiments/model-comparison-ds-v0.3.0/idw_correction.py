"""IDW correction: models that learn (observed - IDW) and add IDW back, as in the IDW-residual ST-GNN.

Two IDW bases (the baseline p = 2, k = 8 and the best interpolator p = 1 over all stations) x three
models (ridge and LightGBM with the columns selected in fixed.py, LightGBM with all feature groups).
The IDW base is always a feature. Hyperparameters are re-tuned for every target with the same blocked
CV as fixed.py. New configurations are appended to results/fixed_state.json (best_cfg), so loso.py
evaluates them too, and their fixed-split rows are appended to results/fixed_metrics.csv.

``direct`` mode adds the counterpart that predicts the concentration itself with both IDW results as
features (LightGBM with the selected columns, and with all feature groups).

    python idw_correction.py           # IDW correction rows
    python idw_correction.py direct    # LightGBM direct rows
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from common import Panel, metrics, save_json, spatial_features, station_bootstrap
from fixed import RES, cv_rmse, cv_splits, fit_predict, log
from models import CORE, design, feature_groups, sample_configs

BASES = {"idwcorr": ("resid_idw", "idw_p2_k8"), "idwp1corr": ("resid_idw_p1all", "idw_p1_all")}


def main(mode="idw"):
    target = "pm25"
    state = json.loads((RES / "fixed_state.json").read_text(encoding="utf-8"))
    p = Panel(target)
    F = spatial_features(p, p.station_split == "train")
    D = {s: design(p, F, p.split == s) for s in ("train", "val", "test")}
    dtr = D["train"].reset_index(drop=True)
    splits = cv_splits(dtr["group"].to_numpy(), dtr["hour"].to_numpy())
    groups = feature_groups(target)
    all_cols = CORE + [c for g in groups.values() for c in g]
    variants = []  # (name, model, cols, target)
    if mode == "direct":
        both_idw = list(dict.fromkeys([*state["best_cfg"]["lgbm"]["cols"], "idw_p2_k8", "idw_p1_all"]))
        variants = [("lgbm_direct", "lgbm", both_idw, "direct"),
                    ("lgbm_allfeat_direct", "lgbm", all_cols, "direct")]
    else:
        for suffix, (tgt, base) in BASES.items():
            families = {"ridge": state["best_cfg"]["ridge"]["cols"], "lgbm": state["best_cfg"]["lgbm"]["cols"],
                        "lgbm_allfeat": all_cols}
            for fam, cols in families.items():
                variants.append((f"{fam}_{suffix}", fam.split("_")[0], list(dict.fromkeys([*cols, base])), tgt))
    new = {}
    for name, model, cols, tgt in variants:
        configs = sample_configs(model, seed=11)[:15] if model == "lgbm" else sample_configs(model)
        scores = sorted(((cv_rmse(model, hp, cols, tgt, dtr, splits), hp) for hp in configs), key=lambda x: x[0])
        new[name] = {"hp": scores[0][1], "cv_rmse": scores[0][0], "cols": cols, "target": tgt,
                     "n_configs": len(scores)}
        log("tuned", name, round(scores[0][0], 3), scores[0][1])

    table = pd.read_csv(RES / "fixed_metrics.csv")
    rows = []
    idw_test = metrics(D["test"]["y"], D["test"]["idw_p2_k8"])["rmse"]
    for name, cfg in new.items():
        row = {"model": name, "cv_rmse": cfg["cv_rmse"]}
        pred = {s: fit_predict(name.split("_")[0], cfg["hp"], cfg["cols"], cfg["target"], dtr, D[s]) for s in ("val", "test")}
        for s in pred:
            row |= {f"{s}_{k}": v for k, v in metrics(D[s]["y"], pred[s]).items()}
        boot = D["test"][["station", "y", "idw_p2_k8"]].assign(**{name: pred["test"]})
        lo, hi, better, k = station_bootstrap(boot, name)
        row |= {"test_d_rmse_vs_idw": row["test_rmse"] - idw_test, "test_ci_lo": lo, "test_ci_hi": hi,
                "test_stations_better": f"{better}/{k}"}
        rows.append(row)
        log(name, "cv", round(cfg["cv_rmse"], 3), "val", round(row["val_rmse"], 3), "test", round(row["test_rmse"], 3))
    table = pd.concat([table[~table["model"].isin(new)], pd.DataFrame(rows)]).sort_values("cv_rmse")
    table.to_csv(RES / "fixed_metrics.csv", index=False)
    state["best_cfg"] |= new
    save_json(state, RES / "fixed_state.json")
    log("done")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "idw")
