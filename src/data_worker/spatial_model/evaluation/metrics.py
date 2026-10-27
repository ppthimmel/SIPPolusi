"""Shared concentration metrics for all spatial models."""
import numpy as np
import pandas as pd

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

