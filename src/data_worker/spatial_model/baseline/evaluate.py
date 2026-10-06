"""Evaluasi leave-one-station-out baseline IDW (Dokumen Desain, subbab 4.3.4 dan 4.4.1).

Untuk setiap polutan, time window t, dan stasiun uji s, estimasi pada
koordinat s dihitung dengan ``idw_interpolate`` (implementasi yang sama
dengan fallback) dari pengukuran stasiun lain pada time window yang sama.
Pengukuran s tidak pernah dipakai untuk memprediksi dirinya sendiri, sesuai
syarat "saat suatu stasiun menjadi lokasi uji, data dari stasiun tersebut
tidak digunakan dalam perhitungan IDW".

Karena IDW tidak memiliki parameter terlatih, split pelatihan tidak dipakai
untuk pelatihan. Split validasi dipakai untuk analisis sensitivitas
power/neighbors, dan split uji untuk hasil yang dilaporkan.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from spatial_model.idw import idw_interpolate

#: Kolom metadata stasiun yang dibawa ke setiap baris prediksi.
STATION_META = ["station_uuid", "kode", "name", "type", "kota", "lat", "lon", "x", "y", "suspected_low_bias"]

#: Definisi metrik. Satuan mengikuti satuan konsentrasi (µg/m³) kecuali disebut lain.
METRIC_DEFINITIONS = {
    "n": "jumlah pasangan pengamatan-estimasi (stasiun-jam)",
    "mae": "MAE = (1/n) Σ |ŷ − y|, µg/m³",
    "rmse": "RMSE = sqrt((1/n) Σ (ŷ − y)²), µg/m³",
    "r2": "R² = 1 − Σ(y − ŷ)² / Σ(y − ȳ)², tanpa satuan; dapat negatif bila lebih buruk dari ȳ",
    "bias": "bias = (1/n) Σ (ŷ − y), µg/m³; positif berarti estimasi terlalu tinggi",
    "mean_obs": "ȳ, rerata ground truth, µg/m³",
    "nrmse": "RMSE / ȳ, tanpa satuan",
}


def compute_metrics(observed, predicted) -> dict[str, float]:
    y = np.asarray(observed, dtype=float)
    yhat = np.asarray(predicted, dtype=float)
    ok = np.isfinite(y) & np.isfinite(yhat)
    y, yhat = y[ok], yhat[ok]
    n = len(y)
    if n == 0:
        return {k: np.nan for k in ("mae", "rmse", "r2", "bias", "mean_obs", "nrmse")} | {"n": 0}
    err = yhat - y
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    mean_obs = float(y.mean())
    return {
        "n": int(n),
        "mae": float(np.mean(np.abs(err))),
        "rmse": rmse,
        "r2": float(1.0 - np.sum(err ** 2) / ss_tot) if ss_tot > 0 else np.nan,
        "bias": float(err.mean()),
        "mean_obs": mean_obs,
        "nrmse": rmse / mean_obs if mean_obs else np.nan,
    }


def grouped_metrics(pred: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    rows = []
    for key, g in pred.groupby(by, observed=True, dropna=False):
        key = key if isinstance(key, tuple) else (key,)
        rows.append(dict(zip(by, key)) | compute_metrics(g["observed"], g["predicted"]))
    return pd.DataFrame(rows)


def loso_predict(
    df: pd.DataFrame,
    power: float,
    neighbors: int,
    min_sources: int = 1,
) -> pd.DataFrame:
    """Prediksi leave-one-station-out untuk seluruh baris ``df``.

    ``df`` adalah potongan dataset stasiun-jam (satu atau lebih polutan dan
    split). Setiap baris menghasilkan satu prediksi, atau NaN bila jumlah
    stasiun lain pada time window tersebut kurang dari ``min_sources``.
    """
    out = []
    for (pollutant, window), g in df.groupby(["pollutant", "window_start_utc"], sort=True):
        xy = g[["x", "y"]].to_numpy()
        values = g["value_ugm3"].to_numpy()
        ref = (g["type"] == "Reference").to_numpy()
        n = len(g)
        pred = np.full(n, np.nan)
        n_used = np.zeros(n, dtype=int)
        nearest = np.full(n, np.nan)
        mean_dist = np.full(n, np.nan)
        ref_frac = np.full(n, np.nan)
        if n - 1 >= max(1, min_sources):
            for i in range(n):
                others = np.arange(n) != i
                res = idw_interpolate(xy[others], values[others], xy[i:i + 1], power, neighbors)
                pred[i] = res["value"][0]
                n_used[i] = res["n_used"][0]
                nearest[i] = res["nearest_m"][0]
                mean_dist[i] = res["mean_dist_m"][0]
                # Komposisi tipe stasiun sumber yang terpakai.
                ref_frac[i] = ref[others][res["source_index"][0]].mean()
        out.append(pd.DataFrame({
            "station_uuid": g["station_uuid"].to_numpy(),
            "pollutant": pollutant,
            "window_start_utc": window,
            "observed": values,
            "predicted": pred,
            "n_sources_available": n - 1,
            "n_used": n_used,
            "nearest_source_m": nearest,
            "mean_source_dist_m": mean_dist,
            "reference_fraction_used": ref_frac,
        }))
    if not out:
        return pd.DataFrame(columns=["station_uuid", "pollutant", "window_start_utc", "observed", "predicted"])
    result = pd.concat(out, ignore_index=True)
    meta = df.drop_duplicates("station_uuid")[STATION_META]
    split = df[["station_uuid", "pollutant", "window_start_utc", "split"]]
    return result.merge(meta, on="station_uuid", how="left").merge(
        split, on=["station_uuid", "pollutant", "window_start_utc"], how="left"
    )


def sweep(
    df: pd.DataFrame,
    powers: list[float],
    neighbors: list[int],
    min_sources: int,
) -> pd.DataFrame:
    """Metrik agregat per polutan untuk setiap kombinasi power × neighbors."""
    rows = []
    for p in powers:
        for k in neighbors:
            pred = loso_predict(df, p, k, min_sources)
            for pollutant, g in pred.groupby("pollutant"):
                rows.append({"pollutant": pollutant, "power": p, "neighbors": k}
                            | compute_metrics(g["observed"], g["predicted"]))
    return pd.DataFrame(rows)


def select_from_sweep(table: pd.DataFrame, metric: str = "rmse") -> dict[str, dict]:
    """Konfigurasi terbaik per polutan; RMSE/MAE diminimalkan, R² dimaksimalkan."""
    best = {}
    ascending = metric != "r2"
    for pollutant, g in table.groupby("pollutant"):
        row = g.sort_values([metric, "neighbors", "power"], ascending=[ascending, True, True]).iloc[0]
        best[pollutant] = {"power": float(row["power"]), "neighbors": int(row["neighbors"]), metric: float(row[metric])}
    return best
