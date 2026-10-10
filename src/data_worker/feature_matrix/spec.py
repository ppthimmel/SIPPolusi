"""Spesifikasi FeatureMatrix (TI-AI-02): grid kanonik dan data dictionary setiap kolom.

FeatureMatrix adalah tabel sel × fitur untuk satu time window (Dokumen Desain
Tabel 3.3, ABD-02): Xmacro, Xmet, Xroad, Xland, Xactivity, jarak ke sensor
darat terdekat, dan penanda ketersediaan setiap sumber. Daftar ``FEATURES`` di
modul ini adalah satu-satunya sumber kebenaran; ``feature_spec.json``
dibangkitkan darinya dan setiap kolom keluaran ``build_feature_matrix`` wajib
terdaftar di sini (diuji).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

from spatial_model.grid import GridSpec

#: Grid kanonik 100 m EPSG:32748, sama dengan grid sumber TI-AI-01
#: (``dataset_processed/geos-cf/grid100m_geoscf_lookup.parquet`` dan Landsat):
#: ``grid_id = gy * 445 + gx`` dihitung dari sisi selatan, identik dengan
#: ``GridSpec.cell_index``. Grid TI-AI-04/05 (JAKARTA_BBOX) termuat di dalamnya.
CANONICAL_GRID = GridSpec(x0=676900.0, y0=9292100.0, nx=445, ny=445, cell_m=100.0)
CANONICAL_BBOX_LONLAT = (106.6, -6.4, 107.0, -6.0)

#: Waktu inferensi untuk time window [t, t + 1 jam): akhir time window. Fitur
#: dinamis hanya boleh memakai data dengan waktu tersedia <= waktu inferensi.
INFERENCE_LAG_HOURS = 1.0

SPEC_VERSION = "fm-0.1.0"


@dataclasses.dataclass(frozen=True)
class Feature:
    name: str
    group: str            # index, Xmacro, Xmet, Xroad, Xland, Xactivity, Xsensor, availability
    unit: str
    dtype: str
    kind: str             # dynamic, static, index
    source: str
    product: str
    native_resolution: str
    alignment: str
    availability_rule: str
    description: str


def _f(name, group, unit, dtype, kind, source, product, native, alignment, availability, description):
    return Feature(name, group, unit, dtype, kind, source, product, native, alignment, availability, description)


_GEOS = ("GEOS-CF v2", "aqc_tavg_1hr_glo_L1440x721_slv (ana)", "0,25° (±27 km), per jam")
_GEOS_ALIGN = ("Sel 100 m → sel GEOS-CF yang memuat pusat sel (grid100m_geoscf_lookup); as-of: valid time "
               "terbaru dengan available_at_utc <= waktu inferensi")
_GEOS_RULE = "geoscf_available = false bila tidak ada valid time tersedia atau umur > 72 jam"
_S5P = ("Sentinel-5P TROPOMI", "L2 NO2 NRTI, QA >= 0,75, median per sel 5 km per scene", "5 km (agregat dari L2 5,5 × 3,5 km)")
_S5P_ALIGN = "Sel 100 m → sel 5 km yang memuat pusat sel; as-of: pengamatan terbaru dengan produced_at <= waktu inferensi"
_S5P_RULE = "s5p_available = false bila tidak ada pengamatan tersedia atau umur > 72 jam"
_OM = ("Open-Meteo", "Historical Forecast API, best_match, satu titik (−6,2; 106,85)", "titik tunggal, per jam")
_OM_ALIGN = "Nilai satu titik disebar seragam ke seluruh sel; nilai pada time_utc = time_window_start"
_OM_RULE = "met_available = false bila salah satu variabel kosong; diasumsikan tersedia pada waktu inferensi"
_OSM = ("OpenStreetMap", "graf road network jakarta-20261005 (dataset_processed/OSM)", "vektor (ruas)")
_OSM_ALIGN = "Ruas dipotong tepat pada batas sel 100 m (spatial_model.aggregate.edge_cell_pieces)"
_OSM_RULE = ("xroad_available = false bila simpul road network terdekat > 500 m (di luar cakupan graf DKI, "
             "termasuk laut); nilai 0 pada sel tersedia berarti memang tidak ada ruas")
_LS = ("Landsat 8/9 Collection 2 Level-2", "landsat-xland-2 per scene (masker QA, skala-offset)", "30 m, ±8 hari")
_LS_ALIGN = ("Median nilai per scene pada sel 100 m untuk scene dengan observed_at dalam [cutoff − 90 hari, "
             "cutoff) dan produced_at <= cutoff")
_VI = ("VIIRS Black Marble", "VNP46A2 Collection 2, QA ketat", "15 detik busur (±460 m), harian")
_VI_ALIGN = ("Sel 100 m → sel VIIRS yang memuat pusat sel (indeks tile h28v09); median harian untuk tanggal "
             "dalam [cutoff − 90 hari, cutoff) dengan produced_at <= cutoff")

FEATURES: list[Feature] = [
    # ------------------------------------------------------------------ indeks
    _f("grid_id", "index", "-", "int64", "index", "grid kanonik", "EPSG:32748 100 m, 445×445", "100 m",
       "grid_id = gy × 445 + gx dari sisi selatan", "-", "Indeks sel grid kanonik"),
    _f("time_window_start", "index", "UTC", "datetime64[ns, UTC]", "index", "-", "-", "1 jam",
       "Awal jam, ISO 8601 UTC", "-", "Awal time window satu jam"),
    _f("inference_time", "index", "UTC", "datetime64[ns, UTC]", "index", "-", "-", "-",
       "time_window_start + 1 jam", "-", "Batas waktu tersedia data untuk time window ini"),
    _f("x_utm", "index", "m", "float64", "index", "grid kanonik", "-", "-", "Pusat sel", "-", "Easting pusat sel"),
    _f("y_utm", "index", "m", "float64", "index", "grid kanonik", "-", "-", "Pusat sel", "-", "Northing pusat sel"),
    _f("in_study_bbox", "index", "-", "bool", "index", "contracts.JAKARTA_BBOX", "-", "-",
       "Pusat sel di dalam JAKARTA_BBOX", "-", "Sel berada di wilayah studi (bbox daratan DKI)"),
    # ------------------------------------------------------------------ Xmacro
    _f("geoscf_pm25_ugm3", "Xmacro", "µg/m³", "float32", "dynamic", *_GEOS, _GEOS_ALIGN, _GEOS_RULE,
       "PM2.5 GEOS-CF pada RH 35% (mentah, belum dinormalisasi terhadap SPKU)"),
    _f("geoscf_no2_ugm3", "Xmacro", "µg/m³", "float32", "dynamic", *_GEOS, _GEOS_ALIGN, _GEOS_RULE,
       "NO2 permukaan GEOS-CF (konversi ppb → µg/m³ pada 298,15 K, 1 atm)"),
    _f("geoscf_age_h", "Xmacro", "jam", "float32", "dynamic", *_GEOS, _GEOS_ALIGN, _GEOS_RULE,
       "time_window_start − valid time GEOS-CF yang dipakai"),
    _f("s5p_no2_umol_m2", "Xmacro", "µmol/m²", "float32", "dynamic", *_S5P, _S5P_ALIGN, _S5P_RULE,
       "Kolom troposferik NO2 Sentinel-5P"),
    _f("s5p_no2_precision_umol_m2", "Xmacro", "µmol/m²", "float32", "dynamic", *_S5P, _S5P_ALIGN, _S5P_RULE,
       "Presisi kolom troposferik NO2"),
    _f("s5p_age_h", "Xmacro", "jam", "float32", "dynamic", *_S5P, _S5P_ALIGN, _S5P_RULE,
       "time_window_start − waktu pengamatan Sentinel-5P yang dipakai"),
    # ------------------------------------------------------------------ Xmet
    _f("met_temperature_2m_c", "Xmet", "°C", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE, "Suhu udara 2 m"),
    _f("met_relative_humidity_2m_pct", "Xmet", "%", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE,
       "Kelembapan relatif 2 m"),
    _f("met_wind_speed_10m_ms", "Xmet", "m/s", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE,
       "Kecepatan angin 10 m (dikonversi dari km/h)"),
    _f("met_wind_u_10m_ms", "Xmet", "m/s", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE,
       "Komponen angin ke arah timur (u), dari kecepatan dan arah datang angin"),
    _f("met_wind_v_10m_ms", "Xmet", "m/s", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE,
       "Komponen angin ke arah utara (v)"),
    _f("met_boundary_layer_height_m", "Xmet", "m", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE,
       "Tinggi lapisan batas atmosfer"),
    _f("met_precipitation_mm", "Xmet", "mm", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE, "Presipitasi per jam"),
    _f("met_surface_pressure_hpa", "Xmet", "hPa", "float32", "dynamic", *_OM, _OM_ALIGN, _OM_RULE,
       "Tekanan permukaan"),
    # ------------------------------------------------------------------ Xroad
    _f("road_density_km_km2", "Xroad", "km/km²", "float32", "static", *_OSM, _OSM_ALIGN, _OSM_RULE,
       "Panjang seluruh ruas di dalam sel per luas sel"),
    _f("road_major_density_km_km2", "Xroad", "km/km²", "float32", "static", *_OSM, _OSM_ALIGN, _OSM_RULE,
       "Kerapatan motorway, trunk, primary (termasuk link) dan busway"),
    _f("road_secondary_density_km_km2", "Xroad", "km/km²", "float32", "static", *_OSM, _OSM_ALIGN, _OSM_RULE,
       "Kerapatan secondary dan tertiary (termasuk link)"),
    _f("road_local_density_km_km2", "Xroad", "km/km²", "float32", "static", *_OSM, _OSM_ALIGN, _OSM_RULE,
       "Kerapatan residential, living_street, unclassified, service"),
    _f("road_active_density_km_km2", "Xroad", "km/km²", "float32", "static", *_OSM, _OSM_ALIGN, _OSM_RULE,
       "Kerapatan footway, path, pedestrian, cycleway, steps, corridor, track"),
    _f("road_walk_density_km_km2", "Xroad", "km/km²", "float32", "static", *_OSM, _OSM_ALIGN, _OSM_RULE,
       "Kerapatan ruas dengan walk_allowed"),
    _f("intersection_density_per_km2", "Xroad", "1/km²", "float32", "static", *_OSM,
       "Simpul dengan ≥ 3 tetangga unik yang berada di dalam sel", _OSM_RULE, "Kerapatan persimpangan"),
    _f("dist_arterial_m", "Xroad", "m", "float32", "static", *_OSM,
       "Jarak Euclides pusat sel ke ruas trunk/primary/secondary (termasuk link) terdekat", _OSM_RULE,
       "Jarak ke jalan arteri terdekat"),
    _f("dist_motorway_m", "Xroad", "m", "float32", "static", *_OSM,
       "Jarak Euclides pusat sel ke ruas motorway/motorway_link terdekat", _OSM_RULE, "Jarak ke jalan tol terdekat"),
    # ------------------------------------------------------------------ Xland
    _f("land_ndvi", "Xland", "-", "float32", "static", *_LS, _LS_ALIGN,
       "land_ndvi_n_obs = 0 → nilai kosong", "Median NDVI"),
    _f("land_ndbi", "Xland", "-", "float32", "static", *_LS, _LS_ALIGN,
       "land_ndbi_n_obs = 0 → nilai kosong", "Median indeks lahan terbangun (NDBI)"),
    _f("land_lst_c", "Xland", "°C", "float32", "static", *_LS, _LS_ALIGN,
       "land_lst_n_obs = 0 → nilai kosong", "Median suhu permukaan lahan"),
    _f("land_ndvi_n_obs", "Xland", "scene", "int16", "static", *_LS, _LS_ALIGN, "-", "Jumlah scene valid NDVI"),
    _f("land_ndbi_n_obs", "Xland", "scene", "int16", "static", *_LS, _LS_ALIGN, "-", "Jumlah scene valid NDBI"),
    _f("land_lst_n_obs", "Xland", "scene", "int16", "static", *_LS, _LS_ALIGN, "-", "Jumlah scene valid LST"),
    # ------------------------------------------------------------------ Xactivity
    _f("act_ntl_nw_cm2_sr", "Xactivity", "nW·cm⁻²·sr⁻¹", "float32", "static", *_VI, _VI_ALIGN,
       "act_ntl_n_obs = 0 → nilai kosong", "Median radiansi cahaya malam (proksi aktivitas)"),
    _f("act_ntl_n_obs", "Xactivity", "hari", "int16", "static", *_VI, _VI_ALIGN, "-", "Jumlah hari valid"),
    # ------------------------------------------------------------------ Xsensor
    _f("dist_nearest_sensor_m", "Xsensor", "m", "float32", "static", "SPKU DLH DKI Jakarta",
       "stasiun ground_truth di dalam JAKARTA_BBOX", "titik",
       "Jarak Euclides pusat sel ke stasiun terdekat dari himpunan stasiun yang diberikan (per fold pada TI-AI-03)",
       "Kosong bila himpunan stasiun kosong",
       "Jarak ke sensor darat terdekat; dihitung hanya dari stasiun yang diizinkan"),
    # ------------------------------------------------------------------ ketersediaan
    _f("geoscf_available", "availability", "-", "bool", "dynamic", *_GEOS, _GEOS_ALIGN, _GEOS_RULE,
       "Penanda ketersediaan GEOS-CF"),
    _f("s5p_available", "availability", "-", "bool", "dynamic", *_S5P, _S5P_ALIGN, _S5P_RULE,
       "Penanda ketersediaan Sentinel-5P"),
    _f("met_available", "availability", "-", "bool", "dynamic", *_OM, _OM_ALIGN, _OM_RULE,
       "Penanda ketersediaan Open-Meteo"),
    _f("xroad_available", "availability", "-", "bool", "static", *_OSM, _OSM_ALIGN, _OSM_RULE,
       "Penanda cakupan road network"),
    _f("land_available", "availability", "-", "bool", "static", *_LS, _LS_ALIGN,
       "true bila sekurang-kurangnya satu dari NDVI, NDBI, LST tersedia", "Penanda ketersediaan Landsat"),
    _f("act_available", "availability", "-", "bool", "static", *_VI, _VI_ALIGN, "act_ntl_n_obs > 0",
       "Penanda ketersediaan VIIRS"),
    _f("sensor_available", "availability", "-", "bool", "static", "SPKU DLH DKI Jakarta", "-", "-", "-",
       "false bila himpunan stasiun kosong", "Penanda ketersediaan jarak sensor"),
]

FEATURE_NAMES = [f.name for f in FEATURES]
FEATURES_BY_NAME = {f.name: f for f in FEATURES}


def spec_document() -> dict:
    return {
        "spec_version": SPEC_VERSION,
        "grid": {**CANONICAL_GRID.as_dict(), "bbox_lonlat": list(CANONICAL_BBOX_LONLAT),
                 "grid_id": "gy * nx + gx, gy dihitung dari sisi selatan"},
        "time_window": {"length": "1 jam", "label": "awal jam, UTC, ISO 8601",
                        "inference_time": f"time_window_start + {INFERENCE_LAG_HOURS:g} jam"},
        "row": "satu sel grid × satu time window",
        "missingness": "Nilai kosong (NaN) bila sumber tidak tersedia; tidak ada imputasi implisit. Setiap "
                       "sumber memiliki kolom *_available.",
        "features": [dataclasses.asdict(f) for f in FEATURES],
    }


def write_feature_spec(path: pathlib.Path) -> None:
    path.write_text(json.dumps(spec_document(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
