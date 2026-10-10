"""Data dictionary FeatureMatrix ST-GNN (sumber tunggal ``feature_spec.json``).

Peran kolom:
- ``key``: kunci baris (sel grid, jam).
- ``temporal`` / ``temporal_age``: masukan TCN (``TEMPORAL_VALUES`` / ``TEMPORAL_AGES``).
- ``land`` / ``land_age``: masukan statis Landsat pada cutoff.
- ``sensor``: masukan statis jarak sensor.
- ``availability``: penanda ``<nilai>_available``.
- ``provenance``: jam sumber, ID scene/sel, koordinat; bukan prediktor.
- ``excluded``: diekspor tetapi tidak dipakai model (LST, hilang ±96%).
"""

from __future__ import annotations

from .prepare_stgnn import (AVAILABILITY_FLAGS, FLAGGED_VALUES, LAND_AGES, LAND_VALUES, SENSOR_FEATURES,
                            TEMPORAL_AGES, TEMPORAL_VALUES)

TS = "timestamp[ns, UTC]"

SATELLITE = {
    "ndvi": ("1", "Landsat 8/9 C2 L2 (landsat-xland-2)", "30 m → rata-rata sel 100 m per scene"),
    "ndbi": ("1", "Landsat 8/9 C2 L2 (landsat-xland-2)", "30 m → rata-rata sel 100 m per scene"),
    "lst_c": ("degC", "Landsat 8/9 C2 L2 (landsat-xland-2)", "30 m → sel 100 m; ketidakpastian ≤ 2 K"),
    "no2_mol_m2": ("mol/m2", "Sentinel-5P TROPOMI L2 NO2 (sentinel5p-no2-3)",
                   "sel 5 km EPSG:32748 yang memuat pusat sel 100 m; median piksel QA ≥ 0,75"),
    "ntl": ("nW/cm2/sr", "VIIRS VNP46A2 (viirs-ntl-3)",
            "sel 15″ (±500 m) yang memuat pusat sel 100 m (indeks tile)"),
}
WEATHER_UNITS = {"temperature_2m": "degC", "relative_humidity_2m": "%", "wind_speed_10m": "km/h",
                 "wind_direction_10m": "degree", "boundary_layer_height": "m", "precipitation": "mm",
                 "surface_pressure": "hPa"}
SAT_RULE = "pengamatan valid terakhir dengan produced_at <= time_utc"


def _role(name: str, value_role: str, age_role: str) -> str:
    return age_role if name.endswith("_age_hours") else value_role


def feature_columns() -> list[dict]:
    cols = [
        dict(name="time_utc", dtype=TS, unit="UTC", role="key", source="-",
             alignment="akhir jam label = waktu inferensi τ; time window desain = [τ − 1 jam, τ)"),
        dict(name="grid_id", dtype="string", unit="-", role="key", source="grid TI-AI-01",
             alignment="rRRRR_cCCCC, baris 0 di utara, EPSG:32748 100 m, 445 × 445"),
        *[dict(name=n, dtype="float64", unit=u, role="provenance", source="grid TI-AI-01", alignment="pusat sel")
          for n, u in [("easting", "m"), ("northing", "m"), ("longitude", "deg"), ("latitude", "deg")]],
    ]
    for value, (unit, source, alignment) in SATELLITE.items():
        if value in TEMPORAL_VALUES:
            vrole = "temporal"
        elif value in LAND_VALUES:
            vrole = "land"
        else:
            vrole = "excluded"
        arole = {"temporal": "temporal_age", "land": "land_age"}.get(vrole, "excluded")
        cols += [
            dict(name=value, dtype="float32", unit=unit, role=vrole, source=source,
                 alignment=f"{alignment}; {SAT_RULE}"),
            dict(name=f"{value}_observed_at", dtype=TS, unit="UTC", role="provenance", source=source,
                 alignment="waktu pengamatan nilai terpilih"),
            dict(name=f"{value}_produced_at", dtype=TS, unit="UTC", role="provenance", source=source,
                 alignment="waktu produksi (proksi ketersediaan), <= time_utc"),
            dict(name=f"{value}_scene_id", dtype="string", unit="-", role="provenance", source=source,
                 alignment="scene/berkas sumber"),
            dict(name=f"{value}_age_hours", dtype="float32", unit="h", role=arole, source=source,
                 alignment="time_utc − observed_at"),
        ]
    for name, unit in WEATHER_UNITS.items():
        cols.append(dict(name=name, dtype="float32", unit=unit, role="temporal",
                         source="Open-Meteo Historical Forecast (open-meteo-core-1)",
                         alignment="satu titik (−6,2; 106,85), seragam di semua sel; valid time terbaru dengan "
                                   "available_at <= time_utc"))
    cols.append(dict(name="weather_time_utc", dtype=TS, unit="UTC", role="provenance", source="Open-Meteo",
                     alignment="valid time cuaca yang dipakai, <= time_utc"))
    cols.append(dict(name="weather_available_at_utc", dtype=TS, unit="UTC", role="provenance", source="Open-Meteo",
                     alignment="waktu tersedia yang diasumsikan: awal run 6 jam + jeda terbit (bawaan 8 jam), "
                               "<= time_utc"))
    cols.append(dict(name="weather_age_hours", dtype="float32", unit="h", role="temporal_age", source="Open-Meteo",
                     alignment="time_utc − weather_time_utc"))
    cols.append(dict(name="geoscf_cell_id", dtype="int64", unit="-", role="provenance",
                     source="GEOS-CF v2 ana (geoscf-preprocessed-v1)", alignment="sel 0,25° (lookup grid 100 m)"))
    for var in ("pm25", "no2"):
        src = "GEOS-CF v2 ana (geoscf-preprocessed-v1)"
        cols += [
            dict(name=f"geoscf_{var}_ugm3", dtype="float32", unit="ug/m3", role="temporal", source=src,
                 alignment="valid time terbaru dengan available_at_utc <= time_utc; nilai mentah (belum dikalibrasi)"),
            dict(name=f"geoscf_{var}_time_window_start", dtype=TS, unit="UTC", role="provenance", source=src,
                 alignment="awal jam valid time"),
            dict(name=f"geoscf_{var}_time_window_end", dtype=TS, unit="UTC", role="provenance", source=src,
                 alignment="akhir jam valid time"),
            dict(name=f"geoscf_{var}_available_at_utc", dtype=TS, unit="UTC", role="provenance", source=src,
                 alignment="Last-Modified server (proksi publikasi), <= time_utc"),
            dict(name=f"geoscf_{var}_latency_hours", dtype="float64", unit="h", role="provenance", source=src,
                 alignment="available_at_utc − akhir valid time"),
            dict(name=f"geoscf_{var}_age_hours", dtype="float32", unit="h", role="temporal_age", source=src,
                 alignment="time_utc − akhir valid time"),
        ]
    cols.append(dict(name="node_index", dtype="int64", unit="-", role="key", source="graf ST-GNN",
                     alignment="indeks node di nodes.parquet"))
    cols.append(dict(name="dist_nearest_sensor_m", dtype="float32", unit="m", role="sensor", source="SPKU station",
                     alignment="jarak Euclidean EPSG:32748 pusat sel ke stasiun terdekat di himpunan yang diberikan"))
    for value, flag in zip(FLAGGED_VALUES, AVAILABILITY_FLAGS):
        cols.append(dict(name=flag, dtype="bool", unit="-", role="availability", source="turunan",
                         alignment=f"True tepat bila {value} finite"))
    return cols


LABEL_COLUMNS = [
    dict(name="station_uuid", dtype="string", unit="-", role="key", alignment="station.uuid ekspor ground truth"),
    dict(name="station_name", dtype="string", unit="-", role="provenance", alignment="station.name"),
    dict(name="station_code", dtype="string", unit="-", role="provenance", alignment="station.kode"),
    dict(name="station_type", dtype="string", unit="-", role="provenance", alignment="Reference | Sensor"),
    dict(name="grid_id", dtype="string", unit="-", role="key", alignment="sel 100 m aktif yang memuat stasiun"),
    dict(name="time_utc", dtype=TS, unit="UTC", role="key", alignment="akhir jam pengukuran"),
    dict(name="station_latitude", dtype="float64", unit="deg", role="provenance", alignment="station.lat"),
    dict(name="station_longitude", dtype="float64", unit="deg", role="provenance", alignment="station.lng"),
    dict(name="station_grid_distance_m", dtype="float64", unit="m", role="provenance",
         alignment="haversine stasiun ke pusat sel (R = 6.371.008,8 m)"),
    dict(name="target_window_start_utc", dtype=TS, unit="UTC", role="provenance", alignment="time_utc − 1 jam"),
    dict(name="target_pm25", dtype="float64", unit="ug/m3", role="target",
         alignment="rata-rata pembacaan PM2.5 valid dalam [time_utc − 1 jam, time_utc)"),
    dict(name="target_no2", dtype="float64", unit="ug/m3", role="target",
         alignment="rata-rata pembacaan NO2 valid dalam [time_utc − 1 jam, time_utc)"),
    dict(name="target_pm25_n_readings", dtype="int64", unit="-", role="provenance", alignment="jumlah pembacaan"),
    dict(name="target_no2_n_readings", dtype="int64", unit="-", role="provenance", alignment="jumlah pembacaan"),
    dict(name="node_index", dtype="int64", unit="-", role="key", alignment="node yang memuat stasiun"),
    dict(name="split", dtype="string", unit="-", role="provenance", alignment="train | validation | test (kronologis)"),
]


def spec_document() -> dict:
    return dict(
        description="Data dictionary FeatureMatrix grid-jam ST-GNN (TI-AI-02): satu baris per (sel 100 m, jam).",
        model_inputs=dict(temporal=[*TEMPORAL_VALUES, *TEMPORAL_AGES], land_static=[*LAND_VALUES, *LAND_AGES],
                          sensor_static=SENSOR_FEATURES),
        features=feature_columns(),
        labels=LABEL_COLUMNS,
    )
