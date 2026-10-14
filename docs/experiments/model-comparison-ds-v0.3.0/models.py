"""Design matrix, feature groups, target parameterisations, and model factories."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

SEED = 42
import os

THREADS = int(os.environ.get("THREADS", "8"))


def feature_groups(target: str) -> dict[str, list[str]]:
    o = "no2" if target == "pm25" else "pm25"
    return {
        "interp": ["src_mean", "src_median", "src_std", "src_p25", "src_p75", "nn_val", "idw_p1_k8", "idw_p1_all",
                   "idw_p2_all", "idw_p3_k4", "gauss_2km", "gauss_5km", "gauss_10km", "idw_minus_median"],
        "geom": ["n_src", "nn_dist", "mean_dist_k8", "gauss_wsum_2km", "gauss_wsum_5km", "gauss_wsum_10km", "ref_frac_k8"],
        "lag": ["idw_p2_k8_lag1", "idw_p2_k8_lag2", "idw_p2_k8_d1", "idw_p2_k8_roll3", "src_median_lag1",
                "src_median_lag2", "src_median_d1", "src_median_roll3"],
        "ctm_ana": [f"geoscf_{target}_ugm3", f"geoscf_{target}_age_hours", f"geoscf_{o}_ugm3"],
        "ctm_fcst": [f"geoscf_fcst_{target}_ugm3", f"geoscf_fcst_{target}_lead_hours", f"geoscf_fcst_{o}_ugm3",
                     "ctm_add_idw", "ctm_mul_idw", "ctm_grad"],
        "met": ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "wind_u", "wind_v",
                "boundary_layer_height", "precipitation", "surface_pressure", "weather_age_hours"],
        "sat": ["no2_mol_m2", "no2_mol_m2_age_hours", "ntl", "ntl_age_hours"],
        "land": ["ndvi", "ndbi", "ndvi_age_hours", "ndbi_age_hours"],
        "cal": ["hour_sin", "hour_cos", "weekday_sin", "weekday_cos"],
        "coord": ["easting", "northing"],
    }


CORE = ["idw_p2_k8"]


def design(p, F: dict, rows: np.ndarray) -> pd.DataFrame:
    """One row per True cell of ``rows`` (H x N) with every candidate column plus keys."""
    h, i = np.nonzero(rows)
    cols = {"hour": h, "station_idx": i, "station": p.stations["kode"].to_numpy()[i], "group": p.group[i],
            "y": p.Y[h, i]}
    for k, a in F.items():
        cols[k] = a[h, i]
    for k, a in p.G.items():
        cols[k] = a[h, i]
    return pd.DataFrame(cols)


# ---------------------------------------------------------------------- target parameterisations
TARGETS = {
    "direct": (lambda d: d["y"].to_numpy(), lambda d, z: z),
    "resid_idw": (lambda d: (d["y"] - d["idw_p2_k8"]).to_numpy(), lambda d, z: d["idw_p2_k8"].to_numpy() + z),
    "resid_mean": (lambda d: (d["y"] - d["src_mean"]).to_numpy(), lambda d, z: d["src_mean"].to_numpy() + z),
    "logratio_mean": (lambda d: np.log1p(d["y"]).to_numpy() - np.log1p(d["src_mean"]).to_numpy(),
                      lambda d, z: np.expm1(np.log1p(d["src_mean"].to_numpy()) + z)),
}


# ---------------------------------------------------------------------- model factories
def make_model(name: str, hp: dict):
    if name == "ridge":
        return make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                             Ridge(alpha=hp.get("alpha", 10.0)))
    if name == "mlp":
        return make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                             MLPRegressor(hidden_layer_sizes=hp.get("hidden", (64,)), alpha=hp.get("alpha", 1e-3),
                                          learning_rate_init=hp.get("lr", 1e-3), early_stopping=True,
                                          validation_fraction=0.15, n_iter_no_change=15, max_iter=400,
                                          random_state=SEED))
    if name == "rf":
        return make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                             RandomForestRegressor(n_estimators=hp.get("n_estimators", 300), max_depth=hp.get("max_depth"),
                                                   min_samples_leaf=hp.get("min_samples_leaf", 20),
                                                   max_features=hp.get("max_features", 0.5), n_jobs=THREADS,
                                                   random_state=SEED))
    if name == "et":
        return make_pipeline(SimpleImputer(strategy="median", add_indicator=True),
                             ExtraTreesRegressor(n_estimators=hp.get("n_estimators", 300), max_depth=hp.get("max_depth"),
                                                 min_samples_leaf=hp.get("min_samples_leaf", 20),
                                                 max_features=hp.get("max_features", 0.5), n_jobs=THREADS,
                                                 random_state=SEED))
    if name == "lgbm":
        import lightgbm as lgb
        base = dict(n_estimators=400, learning_rate=0.05, num_leaves=15, min_child_samples=50, subsample=0.8,
                    subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0)
        return lgb.LGBMRegressor(**(base | hp), random_state=SEED, n_jobs=THREADS, deterministic=True,
                                 force_row_wise=True, verbose=-1)
    if name == "xgb":
        import xgboost as xgb
        base = dict(n_estimators=400, learning_rate=0.05, max_depth=4, subsample=0.8, colsample_bytree=0.8,
                    min_child_weight=5, reg_lambda=1.0)
        return xgb.XGBRegressor(**(base | hp), random_state=SEED, n_jobs=THREADS, tree_method="hist")
    raise ValueError(name)


SEARCH = {
    "ridge": [{"alpha": a} for a in (0.01, 0.1, 1, 10, 100, 1000, 10000)],
    "lgbm": dict(n_estimators=[150, 300, 600, 1000], learning_rate=[0.02, 0.05], num_leaves=[4, 7, 15, 31, 63],
                 min_child_samples=[20, 50, 100, 200, 400], subsample=[0.6, 0.8, 1.0], colsample_bytree=[0.4, 0.6, 0.8, 1.0],
                 reg_lambda=[0, 1, 10, 50], min_split_gain=[0, 0.1, 1.0]),
    "xgb": dict(n_estimators=[150, 300, 600, 1000], learning_rate=[0.02, 0.05], max_depth=[2, 3, 4, 6, 8],
                min_child_weight=[1, 5, 20, 50, 100], subsample=[0.6, 0.8, 1.0], colsample_bytree=[0.4, 0.6, 0.8, 1.0],
                reg_lambda=[1, 10, 50], gamma=[0, 1, 10]),
    "rf": dict(max_depth=[None, 6, 10, 16], min_samples_leaf=[5, 20, 50, 100, 200], max_features=[0.2, 0.33, 0.5, 0.8, 1.0]),
    "et": dict(max_depth=[None, 6, 10, 16], min_samples_leaf=[5, 20, 50, 100, 200], max_features=[0.2, 0.33, 0.5, 0.8, 1.0]),
    "mlp": dict(hidden=[(32,), (64,), (128,), (64, 32), (128, 64)], alpha=[1e-4, 1e-3, 1e-2, 1e-1, 1.0],
                lr=[3e-4, 1e-3, 3e-3]),
}
N_RANDOM = {"lgbm": 30, "xgb": 30, "rf": 16, "et": 16, "mlp": 12}


def sample_configs(name: str, seed: int = SEED) -> list[dict]:
    space = SEARCH[name]
    if isinstance(space, list):
        return space
    rng = np.random.default_rng(seed)
    seen, out = set(), []
    while len(out) < N_RANDOM[name]:
        c = {k: v[rng.integers(len(v))] for k, v in space.items()}
        key = repr(sorted(c.items()))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out
