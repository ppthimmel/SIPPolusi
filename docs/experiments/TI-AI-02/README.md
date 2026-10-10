# TI-AI-02: FeatureMatrix awal

Bukti verifikasi isu #13. Kode dan aturan penyelarasan:
[`src/data_worker/feature_matrix`](../../../src/data_worker/feature_matrix/README.md). Data dictionary:
[`feature_spec.json`](../../../src/data_worker/feature_matrix/feature_spec.json) (45 kolom). Kedua run dibangun dari
commit `476a18d`, working tree bersih.

| Berkas | Isi |
|---|---|
| [`stations_2026-09-13_30/`](stations_2026-09-13_30/quality_report.md) | FeatureMatrix sel stasiun SPKU (124 sel × 432 time window, 53.568 baris), mode operasional ketat: fitur statis diperbarui harian dan tidak pernah melewati waktu inferensi |
| [`grid_2026-09-28T04/`](grid_2026-09-28T04/quality_report.md) | FeatureMatrix seluruh grid kanonik (198.025 sel) untuk satu time window. `features.parquet` (6,7 MB) tidak di-commit; hash isi tercatat di `manifest.json` |
| [`sample_features_200.csv`](sample_features_200.csv) | Sampel 200 baris dari run sel stasiun. Hanya `grid_id`, tanpa identitas stasiun |
| [`feature_maps_2026-09-28T04.png`](feature_maps_2026-09-28T04.png) | Peta enam fitur pada seluruh grid; area kosong berarti tidak tersedia |

Setiap folder run memuat `features.parquet`, `audit.parquet` (data yang dipakai per time window beserta
waktu tersedianya), `quality_report.{json,md}`, dan `manifest.json` (SHA-256 seluruh 33–37 berkas masukan
dari `dataset_processed`, argumen, scene Landsat per batas waktu, hash isi, dan commit).

## Skema FeatureMatrix

Satu baris per (sel grid kanonik, time window).

| Kelompok | Kolom | Sumber dan penyelarasan |
|---|---|---|
| Indeks | `grid_id`, `time_window_start`, `inference_time`, `x_utm`, `y_utm`, `in_study_bbox` | Grid EPSG:32748 100 m, 445 × 445 (sama dengan grid sumber TI-AI-01) |
| Xmacro | `geoscf_pm25_ugm3`, `geoscf_no2_ugm3`, `geoscf_age_h`, `s5p_no2_umol_m2`, `s5p_no2_precision_umol_m2`, `s5p_age_h` | GEOS-CF dan Sentinel-5P, *as-of* terhadap waktu inferensi |
| Xmet | `met_*` (suhu, kelembapan, kecepatan dan komponen u/v angin, tinggi lapisan batas, presipitasi, tekanan) | Open-Meteo pada jam t |
| Xroad | kerapatan jalan total dan per kelas, kerapatan jalur pejalan kaki, kerapatan persimpangan, jarak ke jalan arteri dan tol | Graf OSM `jakarta-20261005` |
| Xland | `land_ndvi`, `land_ndbi`, `land_lst_c` dan jumlah scene | Median Landsat 90 hari sebelum batas waktu |
| Xactivity | `act_ntl_nw_cm2_sr`, `act_ntl_n_obs` | Median VIIRS 90 hari sebelum batas waktu |
| Xsensor | `dist_nearest_sensor_m` | Dari himpunan stasiun yang diberikan |
| Ketersediaan | `geoscf_available`, `s5p_available`, `met_available`, `xroad_available`, `land_available`, `act_available`, `sensor_available` | Satu penanda per sumber |

## Hasil pemeriksaan kualitas

Seluruh pemeriksaan lolos pada kedua run: skema sesuai data dictionary, tanpa kunci ganda, cakupan
sel × time window lengkap, nilai kosong selalu konsisten dengan penanda ketersediaan, dan tidak ada data yang
dipakai sebelum waktu tersedianya.

**Ketersediaan sumber:**

| Penanda | Sel stasiun, 13–30 Sep | Seluruh grid, 28 Sep 04.00 UTC | Grid di bbox studi |
|---|---|---|---|
| GEOS-CF | 100% | 100% | 100% |
| Sentinel-5P | 74,9% | 66,2% | 68,6% |
| Open-Meteo | 100% | 100% | 100% |
| Xroad (OSM) | 100% | 37,6% | 67,0% |
| Landsat | 85,2% | 74,2% | 86,0% |
| VIIRS | 100% | 97,4% | 100% |

**Umur data dinamis pada sel stasiun:**

| Sumber | Min | Median | Maks | Penjelasan |
|---|---|---|---|---|
| GEOS-CF | 7,0 jam | 17,5 jam | 28,0 jam | Mencerminkan latensi publikasi `ana` |
| Sentinel-5P | −0,2 jam | 24,4 jam | 71,9 jam | Nilai negatif berarti pengamatan di dalam time window yang sudah diproduksi sebelum akhir window |

**Determinisme:** run sel stasiun menghasilkan hash isi yang sama (`fa066cf4…`) pada dua pembangunan
terpisah.

## Pemenuhan acceptance criteria #13

| Kriteria | Bukti |
|---|---|
| Setiap baris dapat ditelusuri ke sel grid dan `TimeWindow` | Kunci (`grid_id`, `time_window_start`) unik; `x_utm`/`y_utm` = pusat sel `grid_id`; `audit.parquet` mencatat valid time GEOS-CF, `produced_at` Sentinel-5P, dan batas waktu statis per time window (diuji) |
| Kolom mengikuti data dictionary TI-AI-01 dengan tipe/satuan konsisten | `feature_spec.json` merinci grup, satuan, tipe, sumber, produk, resolusi asli, penyelarasan, dan aturan ketersediaan untuk 45 kolom; pemeriksaan skema dan tipe lolos |
| Missingness tercatat eksplisit, tanpa nilai implisit | Nilai tidak tersedia = NaN dengan `*_available = false`; tanpa imputasi; konsistensi diperiksa (0 pelanggaran) |
| Pemeriksaan melaporkan cakupan, duplikasi, dan proporsi nilai hilang | `quality_report.{json,md}`: cakupan waktu/ruang, duplikasi, nilai hilang per fitur, ketersediaan per sumber dan per time window, rentang nilai, kebocoran waktu |

## Keterbatasan dan temuan untuk TI-AI-01

- **LST Landsat hilang di 96,8% sel stasiun**, karena preprocessing `landsat-xland-2` membatasi
  ketidakpastian LST ≤ 2 K. Fitur ini praktis belum dapat dipakai; NDVI dan NDBI tersedia di 84% sel.
- **Kolom `longitude`/`latitude` VIIRS adalah sudut barat laut sel, bukan pusatnya.** Hal ini terverifikasi
  terhadap indeks tile VNP46A2. Spesifikasi VIIRS menyebutnya "koordinat sel"; pemakaian sebagai pusat
  menggeser sel sejauh setengah piksel (±230 m). FeatureMatrix memakai indeks tile.
- **Open-Meteo hanya satu titik**, sehingga Xmet seragam di seluruh sel, dan waktu tersedianya diasumsikan.
- **Geometri kanyon jalan belum ada**, karena memerlukan data bangunan.
- **Ramalan GEOS-CF (`fcst`) belum diarsipkan**, sehingga suku latar berumur ≥ 7 jam.
- **Normalisasi GEOS-CF terhadap SPKU** (PM2.5 ±4,7×) dilakukan pada TI-AI-03 dan hanya di-fit pada data
  pelatihan.
