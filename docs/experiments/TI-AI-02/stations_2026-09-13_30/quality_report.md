# Laporan kualitas FeatureMatrix (stations)

- Baris: 53,568 (124 sel × 432 time window)
- Sel di bbox wilayah studi: 124
- Rentang: 2026-09-13 00:00:00+00:00 sampai 2026-09-30 23:00:00+00:00
- Batas waktu fitur statis: 2026-09-13 00:00:00+00:00, 2026-09-14 00:00:00+00:00, 2026-09-15 00:00:00+00:00 …
- Seluruh pemeriksaan: **lolos**

## Pemeriksaan

| Pemeriksaan | Status | Rincian |
|---|---|---|
| kolom sesuai data dictionary | lolos | `{'hilang': [], 'tidak terdaftar': []}` |
| tipe data sesuai data dictionary | lolos | `{}` |
| tidak ada kunci (grid_id, time_window_start) ganda | lolos | `{'duplikat': 0}` |
| time_window_start tepat di awal jam UTC | lolos | `{'pelanggaran': 0}` |
| setiap sel memiliki setiap time window | lolos | `{'baris': 53568, 'sel × time window': 53568}` |
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
| s5p_available | 74.9% | 74.9% |
| met_available | 100.0% | 100.0% |
| xroad_available | 100.0% | 100.0% |
| land_available | 85.2% | 85.2% |
| act_available | 100.0% | 100.0% |
| sensor_available | 100.0% | 100.0% |

## Proporsi nilai hilang

| Fitur | Hilang |
|---|---|
| geoscf_pm25_ugm3 | 0.0% |
| geoscf_no2_ugm3 | 0.0% |
| geoscf_age_h | 0.0% |
| s5p_no2_umol_m2 | 25.1% |
| s5p_no2_precision_umol_m2 | 25.1% |
| s5p_age_h | 25.1% |
| met_temperature_2m_c | 0.0% |
| met_relative_humidity_2m_pct | 0.0% |
| met_wind_speed_10m_ms | 0.0% |
| met_wind_u_10m_ms | 0.0% |
| met_wind_v_10m_ms | 0.0% |
| met_boundary_layer_height_m | 0.0% |
| met_precipitation_mm | 0.0% |
| met_surface_pressure_hpa | 0.0% |
| road_density_km_km2 | 0.0% |
| road_major_density_km_km2 | 0.0% |
| road_secondary_density_km_km2 | 0.0% |
| road_local_density_km_km2 | 0.0% |
| road_active_density_km_km2 | 0.0% |
| road_walk_density_km_km2 | 0.0% |
| intersection_density_per_km2 | 0.0% |
| dist_arterial_m | 0.0% |
| dist_motorway_m | 0.0% |
| land_ndvi | 15.6% |
| land_ndbi | 15.6% |
| land_lst_c | 96.8% |
| land_ndvi_n_obs | 0.0% |
| land_ndbi_n_obs | 0.0% |
| land_lst_n_obs | 0.0% |
| act_ntl_nw_cm2_sr | 0.0% |
| act_ntl_n_obs | 0.0% |
| dist_nearest_sensor_m | 0.0% |

## Rentang nilai

| Fitur | min | p01 | p50 | p99 | max |
|---|---|---|---|---|---|
| geoscf_pm25_ugm3 | 26.75 | 27.125 | 73.5 | 174 | 225 |
| geoscf_no2_ugm3 | 5.9927 | 6.7726 | 48.1604 | 141.198 | 165.278 |
| geoscf_age_h | 7 | 7 | 17.5 | 28 | 28 |
| s5p_no2_umol_m2 | 24.7399 | 34.4075 | 138.253 | 791.354 | 1070.47 |
| s5p_no2_precision_umol_m2 | 12.9306 | 14.8718 | 43.1199 | 319.702 | 398.163 |
| s5p_age_h | -0.2342 | 0.2495 | 24.3503 | 71.0374 | 71.9362 |
| met_temperature_2m_c | 22.7 | 23.8 | 28.9 | 35.8 | 36.7 |
| met_relative_humidity_2m_pct | 24 | 28 | 67.5 | 97 | 100 |
| met_wind_speed_10m_ms | 0 | 0.1944 | 1.3056 | 4.4444 | 5.0556 |
| met_wind_u_10m_ms | -4.5045 | -3.7713 | -0.523 | 2.1377 | 2.4636 |
| met_wind_v_10m_ms | -4.0057 | -3.741 | 0.1457 | 1.6112 | 2.1665 |
| met_boundary_layer_height_m | 25 | 30 | 657.5 | 2340 | 2685 |
| met_precipitation_mm | 0 | 0 | 0 | 3.7 | 8.9 |
| met_surface_pressure_hpa | 1006.4 | 1007 | 1010.7 | 1013.1 | 1013.7 |
| road_density_km_km2 | 0 | 0 | 26.0916 | 117.699 | 134.005 |
| road_major_density_km_km2 | 0 | 0 | 0 | 62.9269 | 79.2875 |
| road_secondary_density_km_km2 | 0 | 0 | 0 | 20.1346 | 24.4808 |
| road_local_density_km_km2 | 0 | 0 | 18.7463 | 41.8321 | 46.8325 |
| road_active_density_km_km2 | 0 | 0 | 0 | 51.6886 | 57.3874 |
| road_walk_density_km_km2 | 0 | 0 | 22.2267 | 78.9847 | 85.0163 |
| intersection_density_per_km2 | 0 | 0 | 200 | 2000 | 2000 |
| dist_arterial_m | 0.8935 | 3.274 | 275.954 | 3206.06 | 3378.51 |
| dist_motorway_m | 2.1699 | 24.7778 | 1435.71 | 3501.04 | 3895.4 |
| land_ndvi | 0.0987 | 0.111 | 0.2507 | 0.5496 | 0.6379 |
| land_ndbi | -0.2246 | -0.1817 | 0.014 | 0.1551 | 0.1642 |
| land_lst_c | 40.4912 | 40.4912 | 43.0575 | 45.1896 | 45.1896 |
| act_ntl_nw_cm2_sr | 21.4595 | 29.4722 | 49.6724 | 99.5845 | 116.957 |
| dist_nearest_sensor_m | 6.4948 | 7.6875 | 40.1555 | 66.3592 | 66.5482 |
