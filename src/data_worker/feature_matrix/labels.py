"""Label stasiun-jam SPKU dari ekspor Parquet ``ground_truth``.

Label PM2.5 dan NO2 adalah rata-rata pembacaan valid dalam [t − 1 jam, t),
dicap pada akhir jam t (= ``time_utc`` fitur, waktu inferensi). Tidak ada
interpolasi spasial; stasiun yang berbagi sel tetap memiliki baris sendiri.

Pembacaan valid ditentukan ``LabelRules``. Bawaan ``BUNDLE_RULES`` (README
ekspor ground truth, "Known data-quality issues"; sama dengan bundle ST-GNN):
- ``qc == ""``, stasiun ``in_jakarta_bbox``;
- tanpa nilai 0 (umumnya "tidak ada data") dan tanpa sentinel ``>= 999.99``;
- tanpa stasiun ``DKI_PM25_40`` (PM2.5 macet di 32,00);
- tanpa PM2.5 ``DKI_PM25_33`` sejak 2026-09-19 10:00Z (macet di 14,00).
Snapshot dataset (TI-AI-03) menambah deret macet dan jam tidak lengkap.
"""

from __future__ import annotations

import dataclasses
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


@dataclasses.dataclass(frozen=True)
class LabelRules:
    """Aturan pembacaan valid; setiap pembacaan yang dibuang memperoleh satu alasan (aturan pertama)."""

    sentinel: float = SENTINEL
    excluded_stations: tuple[str, ...] = tuple(sorted(EXCLUDED_STATIONS))
    excluded_from: tuple[tuple[str, str, str], ...] = tuple(
        (code, metric, since.isoformat()) for (code, metric), since in EXCLUDED_FROM.items())
    #: Panjang minimum deret nilai identik berturut-turut yang dianggap macet, dihitung pada deret penuh
    #: (semua bendera qc) per stasiun dan parameter. None = hanya bendera ``S`` dari portal.
    stuck_run_min: int | None = None
    #: Buang jam yang jumlah pembacaan validnya kurang dari kadensi stasiun (modus pembacaan per jam).
    require_complete_hour: bool = False


BUNDLE_RULES = LabelRules()

REASONS = ("outside_study_area", "excluded_station", "qc_flag", "zero_value", "sentinel", "excluded_since",
           "stuck_run", "incomplete_hour")


def annotate_readings(ground_truth_dir: Path | str, rules: LabelRules = BUNDLE_RULES,
                      until=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pembacaan PM2.5/NO2 dengan kolom ``exclusion_reason``, tabel stasiun lengkap).

    ``exclusion_reason`` kosong (NA) berarti valid. ``until`` (eksklusif) membatasi
    pembacaan yang dibaca, sehingga aturan deret tidak bergantung pada data sesudahnya.
    """
    root = Path(ground_truth_dir)
    stations = pd.read_parquet(root / "station.parquet")
    obs = pd.read_parquet(root / "observation.parquet", columns=["station_uuid", "metric", "ts_utc", "value", "qc"])
    obs = obs[obs.metric.isin(METRICS)]
    if until is not None:
        obs = obs[obs.ts_utc.lt(pd.Timestamp(until))]
    obs = obs.sort_values(["station_uuid", "metric", "ts_utc"]).reset_index(drop=True)
    meta = stations.set_index("uuid")
    kode = obs.station_uuid.map(meta.kode)
    reason = pd.Series(pd.NA, index=obs.index, dtype="string")

    def mark(mask, name):
        reason[mask & reason.isna()] = name

    mark(~obs.station_uuid.map(meta.in_jakarta_bbox).fillna(False).astype(bool), "outside_study_area")
    mark(kode.isin(rules.excluded_stations), "excluded_station")
    mark(obs.qc.ne(""), "qc_flag")
    mark(obs.value.eq(0), "zero_value")
    mark(obs.value.ge(rules.sentinel), "sentinel")
    for code, metric, since in rules.excluded_from:
        mark(kode.eq(code) & obs.metric.eq(metric) & obs.ts_utc.ge(pd.Timestamp(since)), "excluded_since")
    if rules.stuck_run_min:
        key = [obs.station_uuid, obs.metric]
        run = obs.value.ne(obs.groupby(key).value.shift()).groupby(key).cumsum()
        length = obs.groupby([obs.station_uuid, obs.metric, run]).value.transform("size")
        mark(length.ge(rules.stuck_run_min), "stuck_run")
    if rules.require_complete_hour:
        hour = obs.ts_utc.dt.floor("h")
        raw = obs.groupby([obs.station_uuid, obs.metric, hour]).value.transform("size")
        expected = (obs.assign(_h=hour, _n=raw).drop_duplicates(["station_uuid", "metric", "_h"])
                    .groupby(["station_uuid", "metric"])._n.agg(lambda s: s.mode().min()))
        valid = reason.isna()
        n_valid = valid.groupby([obs.station_uuid, obs.metric, hour]).transform("sum")
        need = pd.Series(list(zip(obs.station_uuid, obs.metric)), index=obs.index).map(expected)
        mark(valid & n_valid.lt(need), "incomplete_hour")
    obs["exclusion_reason"] = reason
    return obs, stations


def valid_readings(ground_truth_dir: Path | str, rules: LabelRules = BUNDLE_RULES,
                   until=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(pembacaan PM2.5/NO2 valid, stasiun di bbox studi yang tidak dikecualikan) dari folder ekspor."""
    obs, stations = annotate_readings(ground_truth_dir, rules, until)
    stations = stations[stations.in_jakarta_bbox & ~stations.kode.isin(rules.excluded_stations)]
    return obs[obs.exclusion_reason.isna()].drop(columns="exclusion_reason"), stations


def station_hour_targets(ground_truth_dir: Path | str, start, end, rules: LabelRules = BUNDLE_RULES,
                         until=None) -> pd.DataFrame:
    """Label stasiun-jam dengan ``start <= time_utc < end`` (``time_utc`` = akhir jam)."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    obs, stations = valid_readings(ground_truth_dir, rules, until)
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
