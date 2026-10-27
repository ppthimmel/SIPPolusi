"""Exposure scoring after concentration estimation or road aggregation."""
from typing import Mapping
import numpy as np

EXPOSURE_NORMALIZER = {"pm25": 15.0, "no2": 25.0}


def exposure_index(concentrations: Mapping[str, np.ndarray | float | None]) -> np.ndarray:
    """Indeks paparan gabungan: rata-rata c / ambang dari polutan yang tersedia.

    Polutan yang kosong (None/NaN) tidak ikut dihitung, sehingga sel yang
    hanya memiliki PM2.5 bernilai PM2.5 / 15, bukan setengahnya (UT-SDM-07c).
    Sel tanpa polutan sama sekali bernilai kosong (NaN), bukan nol.
    """
    parts = []
    for pollutant, norm in EXPOSURE_NORMALIZER.items():
        value = concentrations.get(pollutant)
        if value is None:
            continue
        parts.append(np.asarray(value, dtype=float) / norm)
    if not parts:
        raise ValueError("tidak ada polutan untuk indeks paparan")
    stacked = np.vstack([np.atleast_1d(p) for p in parts])
    available = np.isfinite(stacked)
    count = available.sum(axis=0)
    total = np.where(available, stacked, 0.0).sum(axis=0)
    return np.divide(total, count, out=np.full(total.shape, np.nan), where=count > 0)

