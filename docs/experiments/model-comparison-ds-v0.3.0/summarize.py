"""Summary tables (markdown + CSV) from results/: LOSO val/test per target and the fixed split."""
import numpy as np
import pandas as pd

from common import station_bootstrap

R = "results/"
NAMES = {"idw_p2_k8": "IDW p=2, k=8 (baseline TI-AI-04)", "idw_p1_all": "IDW p=1, all stations",
         "idw_p2_all": "IDW p=2, all stations", "gauss_5km": "Gaussian kernel 5 km", "gauss_10km": "Gaussian kernel 10 km",
         "src_mean": "Mean of source stations", "kriging_ok": "Ordinary kriging", "nn_val": "Nearest neighbour",
         "ctm_mul_idw": "GEOS-CF x IDW(obs/GEOS-CF)", "ctm_add_idw": "GEOS-CF + IDW(obs-GEOS-CF)", "ridge": "Ridge",
         "lgbm": "LightGBM", "xgb": "XGBoost", "rf": "Random forest", "et": "Extra trees", "mlp": "MLP",
         "lgbm_allfeat_tuned": "LightGBM, all features, tuned", "et_allfeat_tuned": "Extra trees, all features, tuned",
         "lgbm_regsel": "LightGBM, regularised selection", "rk_ridge": "Regression kriging (ridge)",
         "ens_all": "Ensemble, all 6 learned", "ens_top3": "Ensemble, top 3 (val)",
         "ridge_idwcorr": "IDW correction p2k8: ridge", "lgbm_idwcorr": "IDW correction p2k8: LightGBM",
         "lgbm_allfeat_idwcorr": "IDW correction p2k8: LightGBM, all features",
         "ridge_idwp1corr": "IDW correction p1 all: ridge", "lgbm_idwp1corr": "IDW correction p1 all: LightGBM",
         "lgbm_allfeat_idwp1corr": "IDW correction p1 all: LightGBM, all features",
         "lgbm_direct": "LightGBM direct (IDW p2k8 + p1 all as features)",
         "lgbm_allfeat_direct": "LightGBM direct, all features"}

for tgt in ("pm25", "no2"):
    v = pd.read_csv(R + f"loso_{tgt}_val_metrics.csv").set_index("model")
    t = pd.read_csv(R + f"loso_{tgt}_metrics.csv").set_index("model")
    d = pd.read_parquet(R + f"loso_{tgt}_predictions.parquet")
    rows = []
    for m in NAMES:
        lo, hi, _, _ = station_bootstrap(d, m, base="idw_p1_all")
        rows.append({"model": NAMES[m], "val RMSE": v.loc[m, "rmse"], "test RMSE": t.loc[m, "rmse"],
                     "test MAE": t.loc[m, "mae"], "test bias": t.loc[m, "bias"], "test R2": t.loc[m, "r2"],
                     "vs IDW p2k8 [95% CI]": f'{t.loc[m, "d_rmse_vs_idw"]:+.2f} [{t.loc[m, "ci_lo"]:+.2f}, {t.loc[m, "ci_hi"]:+.2f}]',
                     "vs IDW p1 all [95% CI]": f'{t.loc[m, "rmse"] - t.loc["idw_p1_all", "rmse"]:+.2f} [{lo:+.2f}, {hi:+.2f}]',
                     "stations better than p2k8": t.loc[m, "stations_better"]})
    out = pd.DataFrame(rows).sort_values("val RMSE")
    out.to_csv(R + f"summary_loso_{tgt}.csv", index=False)
    print(f"\n### LOSO {tgt} (sorted by val-block RMSE)\n")
    print(out.round(2).to_markdown(index=False))

f = pd.read_csv(R + "fixed_metrics.csv").set_index("model")
rows = []
for m in NAMES:
    if m in f.index:
        r = f.loc[m]
        rows.append({"model": NAMES[m], "CV RMSE": r["cv_rmse"], "val RMSE": r["val_rmse"], "test RMSE": r["test_rmse"],
                     "test bias": r["test_bias"],
                     "vs IDW p2k8 [95% CI]": f'{r["test_d_rmse_vs_idw"]:+.2f} [{r["test_ci_lo"]:+.2f}, {r["test_ci_hi"]:+.2f}]',
                     "stations better": r["test_stations_better"]})
fx = pd.DataFrame(rows).sort_values("CV RMSE", na_position="last")
fx.to_csv(R + "summary_fixed_pm25.csv", index=False)
print("\n### Fixed split PM2.5 (sorted by blocked-CV RMSE)\n")
print(fx.round(2).to_markdown(index=False))
