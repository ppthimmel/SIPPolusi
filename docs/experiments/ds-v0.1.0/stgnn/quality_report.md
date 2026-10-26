# Laporan kualitas FeatureMatrix ST-GNN

473.976 baris × 71 kolom: 1.044 sel (116 berlabel) × 454 jam, 2026-09-12T02:00:00+00:00 s.d. 2026-10-01T00:00:00+00:00 (eksklusif).

| Pemeriksaan | Status | Rincian |
|---|---|---|
| manifest lengkap dan hash keluaran cocok | lolos | status complete; hash wajib 4: hilang [], tak dikenal [], tidak cocok [] |
| kolom dan tipe fitur sesuai data dictionary | lolos | 71 kolom |
| kolom dan tipe label sesuai data dictionary | lolos | 16 kolom |
| cakupan sel × jam lengkap | lolos | 1.044 sel × 454 jam = 473.976; tersedia 473.976 baris |
| tanpa kunci ganda | lolos | fitur (grid_id, time_utc): 0; label (station_uuid, time_utc): 0 |
| penanda ketersediaan konsisten dengan NaN | lolos | 0 pelanggaran pada 15 penanda |
| tanpa kebocoran waktu (sumber tersedia <= time_utc, tanpa label di fitur) | lolos | 0 pelanggaran |
| setiap label memiliki baris fitur | lolos | 0 label tanpa fitur |

## Ketersediaan per fitur

| Penanda | Tersedia | Minimum per jam |
|---|---|---|
| `no2_mol_m2_available` | 100,0% | 100,0% |
| `ntl_available` | 100,0% | 100,0% |
| `temperature_2m_available` | 100,0% | 100,0% |
| `relative_humidity_2m_available` | 100,0% | 100,0% |
| `wind_speed_10m_available` | 100,0% | 100,0% |
| `wind_direction_10m_available` | 100,0% | 100,0% |
| `boundary_layer_height_available` | 100,0% | 100,0% |
| `precipitation_available` | 100,0% | 100,0% |
| `surface_pressure_available` | 100,0% | 100,0% |
| `geoscf_pm25_ugm3_available` | 100,0% | 100,0% |
| `geoscf_no2_ugm3_available` | 100,0% | 100,0% |
| `ndvi_available` | 97,4% | 97,1% |
| `ndbi_available` | 97,4% | 97,1% |
| `lst_c_available` | 3,6% | 3,6% |
| `dist_nearest_sensor_m_available` | 100,0% | 100,0% |

## Umur data (jam)

| Sumber | Min | Median | Maks |
|---|---|---|---|
| Landsat NDVI | 47,0 | 335,0 | 4820,0 |
| Sentinel-5P NO2 | 0,8 | 39,9 | 256,1 |
| VIIRS NTL | 178,0 | 212,0 | 399,0 |
| GEOS-CF PM2.5 | 7,0 | 18,0 | 28,0 |
| GEOS-CF NO2 | 7,0 | 18,0 | 28,0 |
| Open-Meteo | 3,0 | 5,0 | 8,0 |

## Label

43.319 baris stasiun-jam dari 117 stasiun; PM2.5 finite 43.207, NO2 finite 5.372.

| Split | Baris | Stasiun | Awal | Akhir |
|---|---|---|---|---|
| train | 18.748 | 77 | 2026-09-13 01:00:00+00:00 | 2026-09-24 00:00:00+00:00 |
| validation | 1.008 | 14 | 2026-09-25 01:00:00+00:00 | 2026-09-28 00:00:00+00:00 |
| test | 756 | 17 | 2026-09-29 01:00:00+00:00 | 2026-09-30 23:00:00+00:00 |
| none | 22.807 | 112 | 2026-09-13 01:00:00+00:00 | 2026-09-30 23:00:00+00:00 |
