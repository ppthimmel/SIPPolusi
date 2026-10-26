"""Optional kriging comparison and station-cluster uncertainty analysis."""
import numpy as np
import pandas as pd
from spatial_model.data.features import Panel


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

