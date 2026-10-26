"""Interpolasi Inverse Distance Weighting (IDW).

Satu implementasi untuk dua pemakaian (Dokumen Desain, subbab 3.2 dan 4.4):

* baseline evaluasi Spatial Downscaling Model (TI-AI-04,
  ``spatial_model.baseline``), dan
* fallback estimasi ketika inferensi ST-GNN gagal (``run_idw_fallback``,
  Tabel 3.8, UT-SDM-08).

Estimasi pada suatu titik adalah rata-rata pengukuran ``neighbors`` stasiun
terdekat yang dibobot 1 / d^p, dengan d jarak Euclides dalam meter pada
EPSG:32748. Titik yang berimpit dengan stasiun (d < ``COINCIDENT_M``)
mengambil nilai stasiun tersebut (UT-SDM-08b). ``power = 0`` menghasilkan
rata-rata sederhana stasiun terdekat.
"""

from __future__ import annotations

from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from spatial_model.grid import GridSpec, to_grid_xy

DEFAULT_POWER = 2.0
DEFAULT_NEIGHBORS = 8
#: Jarak (meter) di bawah ini dianggap berimpit dengan stasiun.
COINCIDENT_M = 1e-6
#: Ambang normalisasi indeks paparan gabungan (UT-SDM-07a): PM2.5 / 15, NO2 / 25.
EXPOSURE_NORMALIZER = {"pm25": 15.0, "no2": 25.0}
POLLUTANTS = ("pm25", "no2")


class NoGroundTruthError(RuntimeError):
    """Tidak ada pengukuran sensor darat untuk diinterpolasi (UT-SDM-08d)."""


def idw_interpolate(
    src_xy: np.ndarray,
    src_values: np.ndarray,
    dst_xy: np.ndarray,
    power: float = DEFAULT_POWER,
    neighbors: int | None = DEFAULT_NEIGHBORS,
    chunk: int = 20_000,
) -> dict[str, np.ndarray]:
    """IDW dari titik sumber ke titik tujuan.

    ``src_xy`` dan ``dst_xy`` berbentuk (n, 2) dalam meter. ``neighbors``
    ``None`` atau ≤ 0 berarti seluruh stasiun dipakai. Nilai sumber NaN
    diabaikan.

    Mengembalikan kamus berisi ``value`` (estimasi), ``n_used`` (jumlah
    stasiun yang dipakai), ``nearest_m`` (jarak stasiun terdekat),
    ``mean_dist_m`` (rerata jarak stasiun yang dipakai), dan ``source_index``
    (indeks baris ``src_xy`` yang dipakai, berbentuk (len(dst_xy), n_used)).
    """
    src_xy = np.asarray(src_xy, dtype=float).reshape(-1, 2)
    src_values = np.asarray(src_values, dtype=float).ravel()
    dst_xy = np.asarray(dst_xy, dtype=float).reshape(-1, 2)
    if len(src_xy) != len(src_values):
        raise ValueError("src_xy dan src_values wajib sama panjang")
    if power < 0:
        raise ValueError("power tidak boleh negatif")

    keep = np.isfinite(src_values) & np.isfinite(src_xy).all(axis=1)
    original_index = np.flatnonzero(keep)
    src_xy, src_values = src_xy[keep], src_values[keep]
    n_src = len(src_values)
    n_dst = len(dst_xy)
    if n_src == 0:
        raise NoGroundTruthError("tidak ada pengukuran sensor darat yang valid")

    k = n_src if not neighbors or neighbors <= 0 else min(int(neighbors), n_src)
    value = np.empty(n_dst)
    n_used = np.full(n_dst, k, dtype=int)
    nearest = np.empty(n_dst)
    mean_dist = np.empty(n_dst)
    used = np.empty((n_dst, k), dtype=int)

    for lo in range(0, n_dst, chunk):
        hi = min(lo + chunk, n_dst)
        d = np.sqrt(((dst_xy[lo:hi, None, :] - src_xy[None, :, :]) ** 2).sum(axis=2))
        if k < n_src:
            idx = np.argpartition(d, k - 1, axis=1)[:, :k]
        else:
            idx = np.broadcast_to(np.arange(n_src), (hi - lo, n_src))
        used[lo:hi] = original_index[idx]
        dk = np.take_along_axis(d, idx, axis=1)
        vk = src_values[idx]
        nearest[lo:hi] = dk.min(axis=1)
        mean_dist[lo:hi] = dk.mean(axis=1)

        coincident = dk < COINCIDENT_M
        with np.errstate(divide="ignore"):
            w = 1.0 / np.power(np.where(coincident, 1.0, dk), power)
        est = (w * vk).sum(axis=1) / w.sum(axis=1)
        hit = coincident.any(axis=1)
        if hit.any():
            # Rata-rata stasiun yang berimpit (umumnya tepat satu).
            cw = coincident[hit].astype(float)
            est[hit] = (cw * vk[hit]).sum(axis=1) / cw.sum(axis=1)
        value[lo:hi] = est

    return {"value": value, "n_used": n_used, "nearest_m": nearest, "mean_dist_m": mean_dist,
            "source_index": used}


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


def _measurements_frame(ground_truth) -> pd.DataFrame:
    """Normalisasi masukan ground truth menjadi kolom station_id, lon, lat, pollutant, value."""
    if isinstance(ground_truth, pd.DataFrame):
        df = ground_truth.copy()
    else:
        measurements = getattr(ground_truth, "measurements", ground_truth)
        rows: Iterable = (
            {
                "station_id": m.station_id,
                "lon": m.lon,
                "lat": m.lat,
                "pollutant": m.pollutant,
                "value": m.value_ugm3,
                "qc": m.qc,
            }
            for m in measurements
        )
        df = pd.DataFrame(list(rows), columns=["station_id", "lon", "lat", "pollutant", "value", "qc"])
    df = df.rename(columns={"value_ugm3": "value", "lng": "lon"})
    if "qc" in df.columns:
        df = df[df["qc"].fillna("") == ""]
    df = df.dropna(subset=["lon", "lat", "value"])
    # Satu nilai per stasiun dan polutan untuk time window berjalan.
    return df.groupby(["station_id", "pollutant"], as_index=False).agg(
        lon=("lon", "first"), lat=("lat", "first"), value=("value", "mean")
    )


def run_idw_fallback(
    ground_truth,
    grid_spec: GridSpec,
    power: float = DEFAULT_POWER,
    neighbors: int = DEFAULT_NEIGHBORS,
    pollutants: Iterable[str] = POLLUTANTS,
) -> pd.DataFrame:
    """Estimasi konsentrasi per sel grid dari interpolasi IDW (Tabel 3.8).

    ``ground_truth`` boleh berupa ``GroundTruthBatch``, daftar
    ``GroundTruthMeasurement``, atau DataFrame dengan kolom ``station_id``,
    ``lon``, ``lat``, ``pollutant``, ``value`` (dan opsional ``qc``).
    Pengukuran dengan ``qc`` tidak kosong diabaikan.

    Mengembalikan DataFrame satu baris per sel: ``cell_id``, ``x``, ``y``,
    ``pm25_ugm3``, ``no2_ugm3``, ``exposure_index``, ``nearest_station_m``,
    dan ``estimation_source`` = "idw". Polutan tanpa pengukuran bernilai
    kosong. Bila tidak ada pengukuran sama sekali, ``NoGroundTruthError``
    dilempar agar siklus ditandai gagal tanpa menulis time window
    (UT-SDM-08d).
    """
    df = _measurements_frame(ground_truth)
    pollutants = tuple(pollutants)
    df = df[df["pollutant"].isin(pollutants)]
    if df.empty:
        raise NoGroundTruthError("tidak ada pengukuran sensor darat pada time window ini")

    cx, cy = grid_spec.cell_centers()
    dst = np.column_stack([cx, cy])
    out = pd.DataFrame({"cell_id": np.arange(grid_spec.n_cells), "x": cx, "y": cy})
    nearest = np.full(grid_spec.n_cells, np.inf)
    concentrations: dict[str, np.ndarray | None] = {}
    for pollutant in pollutants:
        sub = df[df["pollutant"] == pollutant]
        if sub.empty:
            out[f"{pollutant}_ugm3"] = np.nan
            concentrations[pollutant] = None
            continue
        sx, sy = to_grid_xy(sub["lon"].to_numpy(), sub["lat"].to_numpy())
        res = idw_interpolate(np.column_stack([sx, sy]), sub["value"].to_numpy(), dst, power, neighbors)
        out[f"{pollutant}_ugm3"] = res["value"]
        concentrations[pollutant] = res["value"]
        nearest = np.minimum(nearest, res["nearest_m"])
    out["exposure_index"] = exposure_index(concentrations)
    out["nearest_station_m"] = nearest
    out["estimation_source"] = "idw"
    return out
