# Laporan kualitas FeatureMatrix (all)

- Baris: 198,025 (198,025 sel × 1 time window)
- Sel di bbox wilayah studi: 110,158
- Rentang: 2026-09-28 04:00:00+00:00 sampai 2026-09-28 04:00:00+00:00
- Batas waktu fitur statis: 2026-09-28 00:00:00+00:00
- Seluruh pemeriksaan: **lolos**

## Pemeriksaan

| Pemeriksaan | Status | Rincian |
|---|---|---|
| kolom sesuai data dictionary | lolos | `{'hilang': [], 'tidak terdaftar': []}` |
| tipe data sesuai data dictionary | lolos | `{}` |
| tidak ada kunci (grid_id, time_window_start) ganda | lolos | `{'duplikat': 0}` |
| time_window_start tepat di awal jam UTC | lolos | `{'pelanggaran': 0}` |
| setiap sel memiliki setiap time window | lolos | `{'baris': 198025, 'sel × time window': 198025}` |
| nilai kosong konsisten dengan penanda ketersediaan | lolos | `{'geoscf_available': {'tersedia_tanpa_nilai': 0, 'tidak_tersedia_tetapi_bernilai': 0}, 's5p_available': {'tersedia_tanpa_nilai': 0, 'tidak_tersedia_tetapi_bernilai': 0}, 'met_available': {'tersedia_tanpa_nilai': 0, 'tidak_tersedia_tetapi_bernilai': 0}, 'xroad_available': {'tersedia_tanpa_nilai': 0, 'tidak_tersedia_tetapi_bernilai': 0}, 'land_available': {'tersedia_tanpa_nilai': 0, 'tidak_tersedia_tetapi_bernilai': 0}, 'act_available': {'tersedia_tanpa_nilai': 0, 'tidak_tersedia_tetapi_bernilai': 0}, 'sensor_available': {'tersedia_tanpa_nilai': 0, 'tidak_tersedia_tetapi_bernilai': 0}}` |
| GEOS-CF: available_at_utc <= waktu inferensi | lolos | `{'pelanggaran': 0}` |
| GEOS-CF: valid time <= time window | lolos | `{'pelanggaran': 0}` |
| Sentinel-5P: produced_at <= waktu inferensi | lolos | `{'pelanggaran': 0}` |
| Open-Meteo: jam sama dengan time window | lolos | `{'pelanggaran': 0}` |
| fitur statis dari batas waktu sesudah waktu inferensi | lolos | `{'time_window': 0, 'catatan': 'hanya diizinkan dengan strict_static=False (kebijakan snapshot TI-AI-03)'}` |

## Ketersediaan sumber

| Penanda | Seluruh sel | Sel di bbox studi |
|---|---|---|
| geoscf_available | 100.0% | 100.0% |
| s5p_available | 66.2% | 68.6% |
| met_available | 100.0% | 100.0% |
| xroad_available | 37.6% | 67.0% |
| land_available | 74.2% | 86.0% |
| act_available | 97.4% | 100.0% |
| sensor_available | 100.0% | 100.0% |

## Proporsi nilai hilang

| Fitur | Hilang |
|---|---|
| geoscf_pm25_ugm3 | 0.0% |
| geoscf_no2_ugm3 | 0.0% |
| geoscf_age_h | 0.0% |
| s5p_no2_umol_m2 | 33.8% |
| s5p_no2_precision_umol_m2 | 33.8% |
| s5p_age_h | 33.8% |
| met_temperature_2m_c | 0.0% |
| met_relative_humidity_2m_pct | 0.0% |
| met_wind_speed_10m_ms | 0.0% |
| met_wind_u_10m_ms | 0.0% |
| met_wind_v_10m_ms | 0.0% |
| met_boundary_layer_height_m | 0.0% |
| met_precipitation_mm | 0.0% |
| met_surface_pressure_hpa | 0.0% |
| road_density_km_km2 | 62.4% |
| road_major_density_km_km2 | 62.4% |
| road_secondary_density_km_km2 | 62.4% |
| road_local_density_km_km2 | 62.4% |
| road_active_density_km_km2 | 62.4% |
| road_walk_density_km_km2 | 62.4% |
| intersection_density_per_km2 | 62.4% |
| dist_arterial_m | 62.4% |
| dist_motorway_m | 62.4% |
| land_ndvi | 26.0% |
| land_ndbi | 26.0% |
| land_lst_c | 96.8% |
| land_ndvi_n_obs | 0.0% |
| land_ndbi_n_obs | 0.0% |
| land_lst_n_obs | 0.0% |
| act_ntl_nw_cm2_sr | 2.6% |
| act_ntl_n_obs | 0.0% |
| dist_nearest_sensor_m | 0.0% |

## Rentang nilai

| Fitur | min | p01 | p50 | p99 | max |
|---|---|---|---|---|---|
| geoscf_pm25_ugm3 | 54.4375 | 54.4375 | 104 | 179 | 179 |
| geoscf_no2_ugm3 | 6.4989 | 6.4989 | 45.2051 | 59.6532 | 59.6532 |
| geoscf_age_h | 20 | 20 | 20 | 20 | 20 |
| s5p_no2_umol_m2 | 24.5587 | 24.5587 | 72.271 | 284.643 | 284.643 |
| s5p_no2_precision_umol_m2 | 13.5373 | 13.5373 | 32.0106 | 96.032 | 96.032 |
| s5p_age_h | 21.6605 | 21.6605 | 45.3501 | 69.0379 | 69.0379 |
| met_temperature_2m_c | 33.6 | 33.6 | 33.6 | 33.6 | 33.6 |
| met_relative_humidity_2m_pct | 48 | 48 | 48 | 48 | 48 |
| met_wind_speed_10m_ms | 1.3889 | 1.3889 | 1.3889 | 1.3889 | 1.3889 |
| met_wind_u_10m_ms | -1.0482 | -1.0482 | -1.0482 | -1.0482 | -1.0482 |
| met_wind_v_10m_ms | -0.9112 | -0.9112 | -0.9112 | -0.9112 | -0.9112 |
| met_boundary_layer_height_m | 1380 | 1380 | 1380 | 1380 | 1380 |
| met_precipitation_mm | 0 | 0 | 0 | 0 | 0 |
| met_surface_pressure_hpa | 1011.1 | 1011.1 | 1011.1 | 1011.1 | 1011.1 |
| road_density_km_km2 | 0 | 0 | 23.6323 | 72.0371 | 201.374 |
| road_major_density_km_km2 | 0 | 0 | 0 | 46.477 | 151.354 |
| road_secondary_density_km_km2 | 0 | 0 | 0 | 22.534 | 67.5235 |
| road_local_density_km_km2 | 0 | 0 | 18.5718 | 48.4031 | 88.2044 |
| road_active_density_km_km2 | 0 | 0 | 0 | 19.2607 | 97.8899 |
| road_walk_density_km_km2 | 0 | 0 | 18.8652 | 55.6082 | 149.431 |
| intersection_density_per_km2 | 0 | 0 | 200 | 1100 | 5500 |
| dist_arterial_m | 0.0014 | 2.8529 | 358.591 | 3361.09 | 10828.6 |
| dist_motorway_m | 0.0088 | 7.0879 | 1170.2 | 4243.45 | 11806.3 |
| land_ndvi | -0.4594 | -0.1787 | 0.2577 | 0.655 | 0.821 |
| land_ndbi | -0.6704 | -0.3241 | 0.0165 | 0.1632 | 0.4734 |
| land_lst_c | 32.9629 | 35.5634 | 42.9657 | 71.7988 | 78.236 |
| act_ntl_nw_cm2_sr | 1.2678 | 2.2122 | 36.9485 | 86.6663 | 171.215 |
| dist_nearest_sensor_m | 6.4948 | 226.501 | 4154.77 | 18176.1 | 22073.4 |
