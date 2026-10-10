"""Label stasiun-jam SPKU dari ekspor Parquet ``ground_truth``.

Label PM2.5 dan NO2 adalah rata-rata pembacaan valid dalam [t − 1 jam, t),
dicap pada akhir jam t (= ``time_utc`` fitur, waktu inferensi). Tidak ada
interpolasi spasial; stasiun yang berbagi sel tetap memiliki baris sendiri.

Pembacaan valid (README ekspor ground truth, "Known data-quality issues"):
- ``qc == ""``, stasiun ``in_jakarta_bbox``;
- tanpa nilai 0 (umumnya "tidak ada data") dan tanpa sentinel ``>= 999.99``;
- tanpa stasiun ``DKI_PM25_40`` (PM2.5 macet di 32,00);
- tanpa PM2.5 ``DKI_PM25_33`` sejak 2026-09-19 10:00Z (macet di 14,00).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .grid import GRID_N, GRID_RES, GRID_X_MIN, GRID_Y_TOP, active_cells, grid_id, row_col, to_grid_xy, to_lonlat

EARTH_RADIUS_M = 6_371_008.8
METRICS = {"PM25": "target_pm25", "NO2": "target_no2"}
SENTINEL = 999.99
EXCLUDED_STATIONS = {"DKI_PM25_40"}
EXCLUDED_FROM = {("DKI_PM25_33", "PM25"): pd.Timestamp("2026-09-19T10:00Z")}
INPUT_FILES = ("station.parquet", "observation.parquet", "manifest.json")


def haversine_m(lon1, lat1, lon2, lat2) -> np.ndarray:
    lon1, lat1, lon2, lat2 = (np.radians(np.asarray(v, dtype=float)) for v in (lon1, lat1, lon2, lat2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(h))


def station_cells(stations: pd.DataFrame) -> pd.DataFrame:
    """Sel 100 m aktif yang memuat setiap stasiun, dan jarak stasiun ke pusat sel."""
    out = stations.reset_index(drop=True)
    x, y = to_grid_xy(out.lng, out.lat)
    rows, cols = row_col(x, y)
    out["grid_id"] = grid_id(rows, cols)
    lon, lat = to_lonlat(GRID_X_MIN + GRID_RES * cols + GRID_RES / 2, GRID_Y_TOP - GRID_RES * rows - GRID_RES / 2)
    out["station_grid_distance_m"] = haversine_m(out.lng, out.lat, lon, lat)
    inside = (rows >= 0) & (rows < GRID_N) & (cols >= 0) & (cols < GRID_N)
    return out[inside & out.grid_id.isin(set(active_cells().grid_id))].reset_index(drop=True)


def valid_readings(ground_truth_dir: Path | str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pembacaan PM2.5/NO2 valid, tabel stasiun) dari folder ekspor."""
    root = Path(ground_truth_dir)
    stations = pd.read_parquet(root / "station.parquet")
    obs = pd.read_parquet(root / "observation.parquet", columns=["station_uuid", "metric", "ts_utc", "value", "qc"])
    stations = stations[stations.in_jakarta_bbox & ~stations.kode.isin(EXCLUDED_STATIONS)]
    obs = obs[obs.metric.isin(METRICS) & obs.qc.eq("") & obs.station_uuid.isin(set(stations.uuid))
              & obs.value.ne(0) & obs.value.lt(SENTINEL)].copy()
    kode = obs.station_uuid.map(stations.set_index("uuid").kode)
    for (code, metric), since in EXCLUDED_FROM.items():
        obs = obs[~(kode.eq(code) & obs.metric.eq(metric) & obs.ts_utc.ge(since))]
    return obs, stations


def station_hour_targets(ground_truth_dir: Path | str, start, end) -> pd.DataFrame:
    """Label stasiun-jam dengan ``start <= time_utc < end`` (``time_utc`` = akhir jam)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    obs, stations = valid_readings(ground_truth_dir)
    obs["time_utc"] = obs.ts_utc.dt.floor("h") + pd.Timedelta(hours=1)
    obs = obs[obs.time_utc.ge(start) & obs.time_utc.lt(end)]
    agg = obs.groupby(["station_uuid", "time_utc", "metric"]).value.agg(["mean", "size"]).unstack("metric")
    targets = pd.DataFrame(index=agg.index)
    for metric, column in METRICS.items():
        targets[column] = agg["mean"].reindex(columns=[metric])[metric]
        targets[f"{column}_n_readings"] = agg["size"].reindex(columns=[metric])[metric].fillna(0).astype("int64")
    targets = targets.reset_index()
    targets["time_utc"] = targets.time_utc.astype("datetime64[ns, UTC]")
    targets["target_window_start_utc"] = targets.time_utc - pd.Timedelta(hours=1)

    located = station_cells(stations).rename(columns={
        "uuid": "station_uuid", "name": "station_name", "kode": "station_code", "type": "station_type",
        "lat": "station_latitude", "lng": "station_longitude"})
    targets = targets.merge(located[["station_uuid", "station_name", "station_code", "station_type", "grid_id",
                                     "station_latitude", "station_longitude", "station_grid_distance_m"]],
                            on="station_uuid", how="inner", validate="many_to_one")
    columns = ["station_uuid", "station_name", "station_code", "station_type", "grid_id", "time_utc",
               "station_latitude", "station_longitude", "station_grid_distance_m", "target_window_start_utc",
               "target_pm25", "target_no2", "target_pm25_n_readings", "target_no2_n_readings"]
    return targets[columns].sort_values(["time_utc", "station_uuid"]).reset_index(drop=True)
