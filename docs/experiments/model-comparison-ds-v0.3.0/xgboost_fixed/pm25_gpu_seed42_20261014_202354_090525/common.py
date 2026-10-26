"""Shared data layer for the ds-v0.3.0 model comparison.

Panel = all snapshot stations x all hourly label times (time_utc = end of the label window).
Spatial features for a station-hour are computed only from *allowed* source stations that have a
valid label at that hour, never from the station's own group (same rule as LOSO-per-group IDW).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

OUT = Path(__file__).resolve().parent
SNAP = OUT.parent / "ds-v0.3.0"
PERIODS = ("train", "val", "test")
_TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32748", always_xy=True)


def verify_snapshot(root: Path = SNAP) -> None:
    bad = []
    for line in (root / "CHECKSUMS.sha256").read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
            bad.append(name)
    if bad:
        raise SystemExit(f"snapshot changed: {bad}")


class Panel:
    """Dense (hour x station) arrays for one target pollutant."""

    def __init__(self, target: str = "pm25"):
        verify_snapshot()
        self.target = target
        st = pd.read_parquet(SNAP / "stations.parquet").sort_values("kode").reset_index(drop=True)
        x, y = _TO_UTM.transform(st["lng"].to_numpy(), st["lat"].to_numpy())
        st["x"], st["y"] = x, y
        self.stations = st
        lab = pd.read_parquet(SNAP / "labels.parquet")
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
        f = pd.read_parquet(SNAP / "stgnn/features.parquet", columns=cols)
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


# ---------------------------------------------------------------------- ordinary kriging
def exp_variogram(h, nugget, rng):
    """Normalised exponential variogram (sill = 1); OK weights do not depend on the sill."""
    return np.where(h > 0, nugget + (1 - nugget) * (1 - np.exp(-h / rng)), 0.0)


def fit_variogram(p: Panel, hours_mask: np.ndarray, allowed_station: np.ndarray) -> tuple[float, float]:
    """Pooled exponential variogram of standardised hourly anomalies (allowed stations, given hours)."""
    from scipy.optimize import curve_fit
    bins = np.array([0, 500, 1000, 2000, 3000, 4000, 5000, 6500, 8000, 10000, 12500, 15000, 20000])
    num = np.zeros(len(bins) - 1)
    cnt = np.zeros(len(bins) - 1)
    iu = np.triu_indices(len(p.xy), 1)
    d = p.D[iu]
    b = np.digitize(d, bins) - 1
    for t in np.flatnonzero(hours_mask):
        v = np.where(allowed_station, p.Y[t], np.nan)
        if np.isfinite(v).sum() < 10:
            continue
        z = (v - np.nanmean(v)) / (np.nanstd(v) + 1e-9)
        sq = 0.5 * (z[iu[0]] - z[iu[1]]) ** 2
        ok = np.isfinite(sq) & (b >= 0) & (b < len(num))
        np.add.at(num, b[ok], sq[ok])
        np.add.at(cnt, b[ok], 1)
    gam = num / np.maximum(cnt, 1)
    mid = 0.5 * (bins[1:] + bins[:-1])
    keep = cnt > 30
    popt, _ = curve_fit(lambda h, n, r: n + (1 - n) * (1 - np.exp(-h / r)), mid[keep], gam[keep],
                        p0=[0.5, 3000], bounds=([0, 100], [1, 1e5]))
    return float(popt[0]), float(popt[1])


def kriging_feature(p: Panel, allowed_station: np.ndarray, nugget: float, rng: float,
                    rows: np.ndarray | None = None) -> np.ndarray:
    """OK prediction for every (hour, station) [or only rows mask], sources = allowed minus own group.

    Uses the full-system inverse per hour and removes the target's own group by a Schur complement.
    """
    H, N = p.Y.shape
    out = np.full((H, N), np.nan)
    src = p.source_mask(allowed_station)
    G = exp_variogram(p.D, nugget, rng)
    for t in range(H):
        targets = np.arange(N) if rows is None else np.flatnonzero(rows[t])
        if len(targets) == 0:
            continue
        s = np.flatnonzero(src[t])
        if len(s) < 3:
            continue
        n = len(s)
        A = np.ones((n + 1, n + 1))
        A[:n, :n] = G[np.ix_(s, s)]
        A[n, n] = 0.0
        Ainv = np.linalg.inv(A)
        vals = p.Y[t, s]
        for i in targets:
            rem = np.flatnonzero(p.same_group[i, s])
            b = np.append(G[i, s], 1.0)
            if len(rem):
                keep = np.setdiff1d(np.arange(n + 1), rem)
                B = Ainv[np.ix_(keep, keep)] - Ainv[np.ix_(keep, rem)] @ np.linalg.solve(
                    Ainv[np.ix_(rem, rem)], Ainv[np.ix_(rem, keep)])
                lam = B @ b[keep]
                v = np.append(vals, 0.0)[keep]
                out[t, i] = lam[:-1] @ v[:-1]
            else:
                lam = Ainv @ b
                out[t, i] = lam[:n] @ vals
    return out


# ---------------------------------------------------------------------- metrics
def metrics(y, yhat) -> dict:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    ok = np.isfinite(y) & np.isfinite(yhat)
    y, yhat = y[ok], yhat[ok]
    e = yhat - y
    return dict(n=int(len(y)), mae=float(np.abs(e).mean()), rmse=float(np.sqrt((e ** 2).mean())),
                bias=float(e.mean()), r2=float(1 - (e ** 2).sum() / ((y - y.mean()) ** 2).sum()))


def station_bootstrap(df: pd.DataFrame, col: str, base: str = "idw_p2_k8", n_boot: int = 4000, seed: int = 0):
    """95% CI of RMSE(col) - RMSE(base), resampling stations (clusters)."""
    rng = np.random.default_rng(seed)
    se = df.assign(a=(df[col] - df["y"]) ** 2, b=(df[base] - df["y"]) ** 2).groupby("station")[["a", "b"]].agg(["sum", "count"])
    sa, sb, n = se[("a", "sum")].to_numpy(), se[("b", "sum")].to_numpy(), se[("a", "count")].to_numpy()
    k = len(sa)
    idx = rng.integers(0, k, size=(n_boot, k))
    d = np.sqrt(sa[idx].sum(1) / n[idx].sum(1)) - np.sqrt(sb[idx].sum(1) / n[idx].sum(1))
    st_better = int((np.sqrt(sa / n) < np.sqrt(sb / n)).sum())
    return float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5)), st_better, k


def save_json(obj, path: Path):
    path.write_text(json.dumps(obj, indent=2, default=float), encoding="utf-8")
