"""Normalize observations and interpolate missing model outputs on a grid."""
from typing import Iterable
import numpy as np
import pandas as pd
from spatial_model.data.grid import GridSpec, to_grid_xy
from spatial_model.models.idw import DEFAULT_POWER, DEFAULT_NEIGHBORS, NoGroundTruthError, idw_interpolate
from .exposure import exposure_index

POLLUTANTS = ("pm25", "no2")


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
