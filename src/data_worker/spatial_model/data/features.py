"""Shared ds-v0.3.0 sensor and environmental feature construction.

Panel = all snapshot stations x all hourly label times (time_utc = end of the label window).
Spatial features for a station-hour are computed only from *allowed* source stations that have a
valid label at that hour, never from the station's own group (same rule as LOSO-per-group IDW).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

SNAP = next((p / "docs/experiments/ds-v0.3.0" for p in Path(__file__).resolve().parents
             if (p / "docs/experiments/ds-v0.3.0").is_dir()), Path("docs/experiments/ds-v0.3.0"))
PERIODS = ("train", "val", "test")
_TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32748", always_xy=True)


from spatial_model.data.validation import verify_snapshot


class Panel:
    """Dense (hour x station) arrays for one target pollutant."""

    def __init__(self, target: str = "pm25", snapshot=SNAP):
        self.snapshot = Path(snapshot)
        verify_snapshot(self.snapshot)
        self.target = target
        st = pd.read_parquet(self.snapshot / "stations.parquet").sort_values("kode").reset_index(drop=True)
        x, y = _TO_UTM.transform(st["lng"].to_numpy(), st["lat"].to_numpy())
        st["x"], st["y"] = x, y
        self.stations = st
        lab = pd.read_parquet(self.snapshot / "labels.parquet")
        lab = lab[lab["period"].isin(PERIODS)]
        self.hours = pd.DatetimeIndex(sorted(lab["time_utc"].unique()))
        hi = pd.Series(np.arange(len(self.hours)), index=self.hours)
        si = pd.Series(st.index.to_numpy(), index=st["uuid"].to_numpy())
        H, N = len(self.hours), len(st)
        self.Y = np.full((H, N), np.nan)
        ok = lab[f"{target}_ugm3"].notna()
        rows = lab[ok]
        self.Y[hi[rows["time_utc"]].to_numpy(), si[rows["station_uuid"].astype(str)].to_numpy()] = rows[f"{target}_ugm3"]
        self.period = pd.Series(lab.drop_duplicates("time_utc").set_index("time_utc")["period"]).reindex(self.hours).astype(str).to_numpy()
        # double-blind split label of each labelled station-hour (PM2.5 fixed split); '' outside split
        self.split = np.full((H, N), "", dtype=object)
        sc = f"{target}_split"
        r2 = lab[ok & lab[sc].notna() & (lab[sc].astype(str) != "")]
        self.split[hi[r2["time_utc"]].to_numpy(), si[r2["station_uuid"].astype(str)].to_numpy()] = r2[sc].astype(str)
        self.group = st["group_id"].to_numpy()
        self.gcode = pd.factorize(self.group)[0]
        self.station_split = st["station_split"].to_numpy()
        self.is_ref = (st["station_type"] == "Reference").to_numpy()
        self.xy = st[["x", "y"]].to_numpy()
        self.D = np.sqrt(((self.xy[:, None, :] - self.xy[None, :, :]) ** 2).sum(-1))
        self.same_group = self.gcode[:, None] == self.gcode[None, :]
        self.G = self._grid_features()

    # ------------------------------------------------------------------ grid (non-sensor) features
    def _grid_features(self) -> dict[str, np.ndarray]:
        cols = ["time_utc", "node_index", "easting", "northing", "geoscf_pm25_ugm3", "geoscf_pm25_age_hours",
                "geoscf_no2_ugm3", "geoscf_no2_age_hours", "geoscf_fcst_pm25_ugm3", "geoscf_fcst_pm25_lead_hours",
                "geoscf_fcst_no2_ugm3", "geoscf_fcst_no2_lead_hours", "temperature_2m", "relative_humidity_2m",
                "wind_speed_10m", "wind_direction_10m", "boundary_layer_height", "precipitation", "surface_pressure",
                "weather_age_hours", "no2_mol_m2", "no2_mol_m2_age_hours", "ntl", "ntl_age_hours", "ndvi",
                "ndvi_age_hours", "ndbi", "ndbi_age_hours", "hour_sin", "hour_cos", "weekday_sin", "weekday_cos"]
        f = pd.read_parquet(self.snapshot / "stgnn/features.parquet", columns=cols)
        nodes = self.stations["node_index"].to_numpy()
        f = f[f["node_index"].isin(nodes) & f["time_utc"].isin(self.hours)]
        out = {}
        hi = pd.Series(np.arange(len(self.hours)), index=self.hours)[f["time_utc"]].to_numpy()
        for c in cols[2:]:
            per_node = np.full((len(self.hours), int(nodes.max()) + 1), np.nan)
            per_node[hi, f["node_index"].to_numpy()] = f[c].to_numpy(dtype=float)
            out[c] = per_node[:, nodes]
        th = np.deg2rad(out.pop("wind_direction_10m"))
        out["wind_u"] = -out["wind_speed_10m"] * np.sin(th)
        out["wind_v"] = -out["wind_speed_10m"] * np.cos(th)
        return out

    def source_mask(self, allowed_station: np.ndarray) -> np.ndarray:
        """(H, N) sources with a valid label among allowed stations."""
        return np.isfinite(self.Y) & allowed_station[None, :]


# ---------------------------------------------------------------------- spatial features
def _idw(dist, val, power, k=None):
    """dist/val sorted ascending along last axis, inf/NaN for unavailable sources."""
    if k is not None:
        dist, val = dist[..., :k], val[..., :k]
    ok = np.isfinite(dist)
    w = np.where(ok, 1.0 / np.maximum(dist, 1.0) ** power, 0.0)
    s = w.sum(-1)
    return np.where(s > 0, (w * np.nan_to_num(val)).sum(-1) / np.where(s > 0, s, 1), np.nan)


def spatial_features(p: Panel, allowed_station: np.ndarray, gkey: str = "geoscf_fcst") -> dict[str, np.ndarray]:
    """Sensor-derived features for every (hour, station) using allowed sources minus the own group."""
    H, N = p.Y.shape
    src = p.source_mask(allowed_station)                          # (H, N)
    mask = src[:, None, :] & ~p.same_group[None, :, :]            # (H, target, source)
    Dm = np.where(mask, p.D[None], np.inf)
    order = np.argsort(Dm, axis=2, kind="stable")
    ds = np.take_along_axis(Dm, order, 2)
    Yb = np.broadcast_to(p.Y[:, None, :], (H, N, N))
    vs = np.where(np.isfinite(ds), np.take_along_axis(Yb, order, 2), np.nan)
    gname = f"{gkey}_{p.target}_ugm3"
    g = p.G[gname]                                                # (H, N) model value at each station cell
    gs = np.take_along_axis(np.broadcast_to(g[:, None, :], (H, N, N)), order, 2)
    refs = np.take_along_axis(np.broadcast_to(p.is_ref[None, None, :].astype(float), (H, N, N)), order, 2)
    n_src = np.isfinite(ds).sum(-1).astype(float)
    with np.errstate(all="ignore"):
        F = {
            "n_src": n_src,
            "src_mean": np.nanmean(vs, -1),
            "src_median": np.nanmedian(vs, -1),
            "src_std": np.nanstd(vs, -1),
            "src_p25": np.nanpercentile(vs, 25, axis=-1),
            "src_p75": np.nanpercentile(vs, 75, axis=-1),
            "nn_val": vs[..., 0],
            "nn_dist": np.where(np.isfinite(ds[..., 0]), ds[..., 0], np.nan),
            "mean_dist_k8": np.nanmean(np.where(np.isfinite(ds[..., :8]), ds[..., :8], np.nan), -1),
            "idw_p2_k8": _idw(ds, vs, 2, 8),
            "idw_p1_k8": _idw(ds, vs, 1, 8),
            "idw_p1_all": _idw(ds, vs, 1),
            "idw_p2_all": _idw(ds, vs, 2),
            "idw_p3_k4": _idw(ds, vs, 3, 4),
            "ref_frac_k8": np.nanmean(np.where(np.isfinite(ds[..., :8]), refs[..., :8], np.nan), -1),
        }
        for bw in (2000, 5000, 10000):
            w = np.where(np.isfinite(ds), np.exp(-0.5 * (ds / bw) ** 2), 0.0)
            F[f"gauss_{bw // 1000}km"] = np.where(w.sum(-1) > 1e-6, (w * np.nan_to_num(vs)).sum(-1) / np.maximum(w.sum(-1), 1e-12), np.nan)
            F[f"gauss_wsum_{bw // 1000}km"] = w.sum(-1)
        # GEOS-CF bias-corrected interpolation (additive and multiplicative)
        F["ctm_add_idw"] = g + _idw(ds, vs - gs, 2, 8)
        F["ctm_mul_idw"] = g * _idw(ds, vs / gs, 2, 8)
        F["ctm_grad"] = g - _idw(ds, gs, 2, 8)
        F["idw_minus_median"] = F["idw_p2_k8"] - F["src_median"]
    # temporal context of the spatial field (lags are computed per station column; NaN at block starts)
    for name in ("idw_p2_k8", "src_median"):
        a = F[name]
        lag1 = np.vstack([np.full((1, N), np.nan), a[:-1]])
        lag2 = np.vstack([np.full((2, N), np.nan), a[:-2]])
        F[f"{name}_lag1"] = lag1
        F[f"{name}_lag2"] = lag2
        F[f"{name}_d1"] = a - lag1
        F[f"{name}_roll3"] = pd.DataFrame(a).rolling(3, min_periods=1).mean().to_numpy()
    return F


