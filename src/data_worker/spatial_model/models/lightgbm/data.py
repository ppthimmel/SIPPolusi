"""Ordered feature groups, design matrices and training targets."""
from __future__ import annotations

import numpy as np
import pandas as pd


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
    "resid_idw_p1all": (lambda d: (d["y"] - d["idw_p1_all"]).to_numpy(), lambda d, z: d["idw_p1_all"].to_numpy() + z),
    "logratio_mean": (lambda d: np.log1p(d["y"]).to_numpy() - np.log1p(d["src_mean"]).to_numpy(),
                      lambda d, z: np.expm1(np.log1p(d["src_mean"].to_numpy()) + z)),
}


