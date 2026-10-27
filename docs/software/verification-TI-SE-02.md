# Bukti verifikasi TI-SE-02: konektor dan pemrosesan Data Worker

Isu #2. Panduan: [`src/data_worker/feature_store/README.md`](../../src/data_worker/feature_store/README.md).

## Lingkungan

- **Lokal:** macOS, Python 3.12, pandas 3.0.2, h5py 3.16, rasterio 1.5.2.
- **Basis data:** PostgreSQL 16 + PostGIS 3.4 di Docker (`postgis/postgis:16-3.4`, hanya `127.0.0.1`), basis
  data sementara `tise02` dan `tise02live`.
- **Tidak disentuh:** PostgreSQL Railway, service Railway, dan portal SPKU asli. Siklus langsung memakai portal
  tiruan `scripts/spku_stub_portal.py`.
- **Sumber sekunder:** dipanggil langsung, hanya baca:
  - Open-Meteo;
  - NCCS (GEOS-CF);
  - Microsoft Planetary Computer (Landsat, Sentinel-5P);
  - NASA CMR dan LAADS DAAC (VIIRS, dengan `EARTHDATA_TOKEN` dari lingkungan).
- **Waktu verifikasi:** 27 Oktober 2026.

## Acceptance criteria

| Kriteria | Bukti |
|---|---|
| Mengambil data dari sumber yang dikonfigurasi tanpa kredensial tertanam di kode | Lima sumber diambil langsung (bagian [Siklus langsung](#siklus-langsung)). Satu-satunya kredensial, `EARTHDATA_TOKEN`, dibaca dari lingkungan. Token SAS Planetary Computer diminta per siklus. Keluaran siklus diperiksa: 0 kemunculan token, `Bearer`, atau parameter SAS. `test_timeout_permanen_menjadi_source_error_dan_tercatat` memeriksa bahwa query URL tidak masuk log |
| Rekaman keluaran memiliki stempel waktu, identitas sumber, status kualitas, dan nilai/penanda data hilang | Setiap baris skema `features` membawa `fetched_utc` dan `fetch_id`. Produk satelit membawa `product_id`, `observed_at`, `produced_at`, `processing_version`, dan ringkasan QA (`quality`). Nilai hilang adalah NULL, dan FeatureMatrix membawa `<nilai>_available`. Kualitas per pengambilan tercatat di `fetch_log`, ketersediaan per sumber pada τ di `feature_window.availability` |
| Kegagalan sumber eksternal tidak merusak batch lain dan menghasilkan log yang dapat ditelusuri | Skenario C: kelima sumber tidak terjangkau. Setiap sumber dicoba tiga kali, tercatat `failed` di `fetch_log` dengan URL tanpa query, dan siklus tetap membentuk FeatureMatrix dari data sebelumnya. Uji `test_sumber_gagal_tercatat_dan_sumber_lain_tetap_berjalan`, `test_galat_tak_terduga_tidak_menghentikan_...` |
| Uji unit: respons valid, respons kosong, format berubah, timeout, data duplikat | `tests/test_connectors.py` (48 uji, respons rekaman) dan `tests/test_feature_store.py` (12 uji, PostgreSQL); pemetaan pada tabel berikut |

| Kasus | Uji |
|---|---|
| Respons valid | `test_open_meteo_respons_valid`, `test_geoscf_ana_subset_valid`, `test_geoscf_fetch_ana_dari_rekaman`, `test_geoscf_fcst_run_dari_rekaman`, `test_stac_respons_valid_landsat_dan_filter_lc08_lc09`, `test_viirs_cmr_valid_hanya_tile_h28v09`, `test_viirs_granule_qa_dan_sel_data_latih`, `test_sentinel5p_qa_dan_median_per_sel`, `test_langkah_meteo_dengan_respons_rekaman_dan_timeout` |
| Respons kosong | `test_open_meteo_respons_kosong`, `test_stac_respons_kosong`, `test_viirs_cmr_kosong_dan_format_berubah`, `test_sentinel5p_lintasan_di_luar_bbox_kosong`, `test_geoscf_fetch_ana_tanpa_jam_baru_tidak_meminta_subset` |
| Format berubah | `test_open_meteo_format_berubah` (5 varian: variabel, satuan, zona waktu, panjang larik, objek hilang), `test_geoscf_ana_format_berubah`, `test_stac_format_berubah` (3 varian), `test_token_sas_tanpa_kunci_token`, `test_sentinel5p_format_berubah`, `test_viirs_koordinat_berubah_ditolak`, `test_viirs_nama_granule_tidak_dikenal`, `test_landsat_item_tanpa_aset_mtl_format_berubah` |
| Timeout dan gagal permanen | `test_timeout_permanen_menjadi_source_error_dan_tercatat`, `test_open_meteo_timeout_dicoba_tiga_kali`, `test_geoscf_timeout_portal_menjadi_source_error`, `test_http_404_tidak_dicoba_ulang`, `test_viirs_token_ditolak_menjadi_source_error`, `test_langkah_meteo_dengan_respons_rekaman_dan_timeout` |
| Respons Range pendek atau kosong | `test_respons_range_pendek_menjadi_source_error_bukan_putaran_tanpa_akhir` (dengan batas waktu 5 detik), `test_respons_range_lengkap_tetap_terbaca` |
| URL bertanda tangan pada galat dan log pustaka | `test_galat_tak_terduga_dengan_url_bertanda_tangan_tidak_bocor`, `test_log_gdal_lewat_rasterio_tanpa_token_sas`, `test_scrub_membuang_query_url` |
| Gagal sementara | `test_gagal_sementara_lalu_berhasil_dihitung_sebagai_coba_ulang`, `test_http_429_mematuhi_retry_after` |
| Data duplikat | `test_open_meteo_jam_ganda_dan_nilai_di_luar_rentang`, `test_cuaca_idempoten_dan_revisi_berubah`, `test_geoscf_dan_produk_satelit_duplikat_diabaikan`, `test_write_features_dan_bangun_ulang_feature_version` (penulisan ulang manifest idempoten) |

## Uji otomatis

`pytest` di `src/data_worker`:

| Lingkungan | Hasil |
|---|---|
| Dengan PostGIS (`TEST_DATABASE_URL`) | 291 lulus (122 detik) |
| Tanpa basis data | 222 lulus, 69 dilewati (28 detik) |

- **Tanpa jaringan:** uji tidak menghubungi sumber sekunder (`FEATURES_SOURCES=none` pada `conftest.py`).
- **Respons rekaman:** `tests/fixtures/sources/`:
  - JSON Open-Meteo, STAC Landsat dan Sentinel-5P, CMR VIIRS;
  - ASCII OPeNDAP GEOS-CF `ana` (3 jam) dan run `fcst` 2026-09-30 09z.
- **Granule sintetis:** Sentinel-5P dan VIIRS dibuat dengan `h5py` di dalam uji.

## Kesamaan dengan data latih

Produk arsip diambil ulang lewat konektor, lalu dibandingkan dengan `dataset_processed`:

| Sumber | Produk | Hasil |
|---|---|---|
| Sentinel-5P | `S5P_L2_NO2____20260930T070407_20260930T070907_46448` | 52 sel identik: `no2_mol_m2`, presisi, jumlah piksel valid, `available`, ketiga cap waktu. Dibaca 6,3 MB dari granule 65 MB |
| VIIRS | `VNP46A2.A2026263.h28v09.002.2026273184043.h5` | 9.216 sel identik pada 12 kolom (radiansi, gap-filled, umur retrieval, iradiansi bulan, tiga flag QA, dua penanda tersedia, koordinat, `produced_at`); 817 sel valid pada keduanya |
| GEOS-CF `ana` | 2026-09-30 20.00-23.00Z | PM2.5 dan NO2 ppb identik, `available_at_utc` (Last-Modified) identik. NO2 µg/m³ berbeda paling besar 1,5 × 10⁻⁵ (pembulatan float32) |
| GEOS-CF `fcst` | Run 2026-09-30 09z | 1.080 baris identik: nilai, lead, `available_at_utc` |
| Landsat | `LC09_L2SP_122065_20260922_02_T1` | Identik (selisih ≤ 4,5 × 10⁻⁸, float32) |
| Landsat | `LC09_L2SP_122064_20260922_02_T1` | 8.961 sel NDVI valid. 7 dari 195.782 sel (0,004%) berbeda status valid di ambang fraksi 0,5; NDVI pada sel lain selisih ≤ 8 × 10⁻⁸ |

Selisih Landsat setara dengan yang dicatat bundle pemrosesan data latih untuk pembangunan ulangnya sendiri
(±0,005% sel-scene di ambang 0,5).

**Pembaca PostgreSQL** (`StoreSourceReader`): seluruh arsip Januari-September 2026 di-seed, lalu keluaran
dibandingkan dengan `SourceReader` Parquet TI-AI-02:

- seluruh 195.782 sel pada τ = 2026-09-29 12.00Z: identik pada seluruh kolom, 0 kolom berbeda;
- τ = 2026-07-15 03.00Z, setiap sel ketujuh: identik;
- uji `test_reader_postgresql_identik_dengan_reader_data_latih`: 3 jam, setiap sel ke-150, identik.

## Siklus langsung

Basis data lokal baru `tise02live`. Langkahnya:

1. `python main.py features seed` dengan retensi bawaan: 3,7 detik, 12 MB.
2. `python main.py cycle` dengan seluruh sumber aktif dan `SPKU_BASE_URL` portal tiruan.

| Skenario | Durasi, memori | Hasil |
|---|---|---|
| A. Siklus terjadwal pertama (time window 2026-10-26 23.00Z) | 227 detik, 515 MB | Kode 0. Keenam sumber `ok`: GEOS-CF `ana` 63 jam baru (567 baris), `fcst` 2 run (2.160 baris), Landsat 1 scene (3 antre), VIIRS 3 granule (10 antre), Sentinel-5P 2 granule (78 sel), Open-Meteo 96 jam. FeatureMatrix 195.782 × 89, `feature_version a99c23a9…` |
| B. Eksekusi ulang time window yang sama (`--window-start`) | 136 detik, 602 MB | Kode 0. GEOS-CF, Sentinel-5P, Open-Meteo: 0 baris baru (duplikat diabaikan). Antrean berlanjut: Landsat +1, VIIRS +3. `feature_version` baru `252cb1d9…` karena produk tambahan terbit sebelum τ |
| C. Seluruh sumber sekunder tidak terjangkau (proksi mati) | 38 detik | Kode 1. Setiap sumber dicoba tiga kali (jeda 2 dan 4 detik) lalu `failed`, dan langkah berikutnya tetap berjalan. `run_quality_control` `ok` karena data sebelumnya masih segar (fallback). FeatureMatrix tetap dibentuk (`4c5f3254…`). `fetch_ground_truth` dari portal tiruan tidak terpengaruh |
| D. `features rebuild --window-start 2026-10-26T23:00Z` | 6 detik, 537 MB | `verified: true`; matriks dibangun ulang as-of `built_utc` sama dengan `feature_version 252cb1d9…` |

- **Status siklus:** A dan B berstatus `degraded` hanya karena portal tiruan menyajikan halaman rekaman
  September, sehingga `fetch_ground_truth` `unavailable` untuk time window Oktober.
- **Antrean:** Landsat dan VIIRS memproses produk terlama dulu dan tetap diperiksa setiap siklus selama
  antrean belum habis.

**Cuplikan log skenario C** (JSON, satu baris per kejadian; URL tanpa query):

```
{"level": "warn", "logger": "data_worker.connectors", "message": "open_meteo: GET https://api.open-meteo.com/v1/forecast gagal pada percobaan 1/3 (ProxyError); diulang dalam 2 detik"}
{"level": "warn", "logger": "data_worker.connectors", "message": "open_meteo: GET https://api.open-meteo.com/v1/forecast gagal pada percobaan 2/3 (ProxyError); diulang dalam 4 detik"}
{"level": "error", "logger": "data_worker.connectors", "message": "open_meteo: GET https://api.open-meteo.com/v1/forecast tetap gagal (ProxyError)"}
{"level": "error", "logger": "data_worker.features", "message": "sumber open_meteo: failed (baru 0, baris 0, permintaan 1, diulang 0, gagal 1): open_meteo: GET https://api.open-meteo.com/v1/forecast gagal: ProxyError"}
{"level": "warn", "logger": "data_worker.acquisition", "message": "siklus 2026-10-27T00:00Z/01:00Z: fetch_meteo -> failed (open_meteo: GET https://api.open-meteo.com/v1/forecast gagal: ProxyError)"}
{"level": "error", "logger": "data_worker", "message": "siklus 2026-10-27T00:00Z/01:00Z (run 20261027T010424Z-d6fb7b) selesai: failed"}
```

**`features.fetch_log` skenario C** (cuplikan):

| source | status | requests | failed_requests | note |
|---|---|---|---|---|
| geoscf_ana | failed | 1 | 1 | `geoscf_ana: HEAD https://portal.nccs.nasa.gov/datashare/.../GEOS.cf.ana....20261026_0930z.R0.nc4 gagal: ProxyError` |
| geoscf_fcst | ok | 0 | 0 | Run terbaru sudah tersimpan; tidak ada permintaan |
| landsat | failed | 1 | 1 | `landsat: POST https://planetarycomputer.microsoft.com/api/stac/v1/search gagal: ProxyError` |
| viirs | failed | 1 | 1 | `viirs: GET https://cmr.earthdata.nasa.gov/search/granules.json gagal: ProxyError` |
| sentinel5p | failed | 1 | 1 | `sentinel5p: POST https://planetarycomputer.microsoft.com/api/stac/v1/search gagal: ProxyError` |
| open_meteo | failed | 1 | 1 | `open_meteo: GET https://api.open-meteo.com/v1/forecast gagal: ProxyError` |

**`python main.py features status --hours 6`** (setelah A dan B):

```
Ketersediaan pada τ = 2026-10-27 01:00Z:
  open_meteo   tersedia        terbaru 2026-10-26T17:00:00+00:00 (umur 8.0 jam)
  geoscf_ana   tersedia        terbaru 2026-10-26T08:00:00+00:00 (umur 16.0 jam)
  geoscf_fcst  tersedia        terbaru 2026-10-26T09:00:00+00:00 (umur 15.0 jam)
  sentinel5p   tersedia        terbaru 2026-10-26T07:17:49.721000+00:00 (umur 17.7 jam)
  viirs        tersedia        terbaru 2026-10-10T23:59:59+00:00 (umur 385.0 jam)
  landsat      tersedia        terbaru 2026-09-30T03:00:29.861141+00:00 (umur 646.0 jam)
Ukuran tabel (MB): {'feature_window': 0.05, 'fetch_log': 0.05, 'geoscf_ana_hourly': 0.15, 'geoscf_fcst_hourly': 0.44, 'satellite_layer': 12.52, 'satellite_product': 0.09, 'sentinel5p_observation': 0.13, 'weather_hourly': 0.16}
```

## Contoh batch keluaran

**FeatureMatrix τ = 2026-10-27 00.00Z** (skenario A):

- 195.782 baris × 89 kolom; kolom dan tipe `features.parquet` TI-AI-02;
- Parquet 6,7 MB bila `FEATURE_STORE_DIR` diatur.

Fraksi sel tersedia:

| Fitur | Tersedia |
|---|---|
| NDVI/NDBI | 83,9% |
| NO2 kolom | 51,9% |
| NTL | 98,0% |
| Cuaca, GEOS-CF `ana`, GEOS-CF `fcst` | 100% |

Satu baris (sebagian kolom):

| Kolom | Nilai |
|---|---|
| `grid_id` | `r0001_c0030` |
| `time_utc` | 2026-10-27 00:00:00+00:00 |
| `ndvi` | −0,544 (`ndvi_age_hours` 5.445; scene `LC09_L2SP_122064_20260314_02_T1`) |
| `no2_mol_m2` | NaN (`no2_mol_m2_available` False) |
| `ntl` | NaN (`ntl_available` False) |
| `temperature_2m` | 27,5 (`weather_age_hours` 7,0) |
| `geoscf_pm25_ugm3` | 58,44 (`geoscf_pm25_age_hours` 15,0) |
| `geoscf_fcst_pm25_ugm3` | 167,0 (`geoscf_fcst_pm25_lead_hours` 14,0) |
| `dist_nearest_sensor_m` | NaN (portal tiruan tanpa pengukuran pada time window ini) |

**Manifest (`feature_window`, dipangkas):**

```json
{"time_window_start": "2026-10-26 23:00:00+00:00", "time_utc": "2026-10-27 00:00:00+00:00",
 "feature_version": "a99c23a9299628d03ded95e6e096aab51cb6db8a358ba14af84a774b9fea3900",
 "built_utc": "2026-10-27 01:00:45.807107+00:00", "run_id": "20261027T005707Z-5013a8", "cells": 195782,
 "availability": {"open_meteo": {"latest": "2026-10-26T17:00:00+00:00", "age_hours": 7.0, "available": true},
                  "landsat": {"latest": "2026-09-30T03:00:05.936213+00:00", "age_hours": 645.0, "available": true}, "...": "..."},
 "sources": {"ntl": ["VNP46A2.A2026244.h28v09.002.2026252113130.h5", "..."],
             "geoscf_fcst_pm25_init_utc": ["2026-10-26T09:00:00+00:00"], "sensor_stations": [], "...": "..."}}
```

## Image Docker

`docker build --platform linux/amd64` dari `src/data_worker` (konteks build Railway) berhasil:

- **Dependensi:** `h5py 3.16.0` (HDF5 2.0.0) dan `rasterio 1.4.4` (GDAL 3.10.3) dari wheel `manylinux`, Python
  3.11; image 2,77 GB.
- **Pemeriksaan impor build:** `preflight.import_problems()` lolos, termasuk `feature_store.steps`,
  `feature_store.cli`, `h5py`, dan `rasterio`.

Di dalam image:

| Pemeriksaan | Hasil |
|---|---|
| `python main.py check --skip-db` | Konfigurasi dan impor modul `ok` |
| Sentinel-5P arsip `…20260930T070407…_46448` lewat HTTP Range + h5py | 52 sel, 20 valid (sama dengan data latih); 6,3 MB dibaca |
| VIIRS arsip `VNP46A2.A2026263…2026273184043.h5` dengan `EARTHDATA_TOKEN` | 9.216 sel, 817 valid (sama dengan data latih) |
| Agregasi Landsat 30 m → 100 m (rasterio, raster sintetis) | Nilai rata-rata tepat |
| `pytest` `test_connectors`, `test_feature_store`, `test_feature_matrix`, `test_data_worker`, `test_worker_schedule`, `test_preflight` (PostGIS lewat `host.docker.internal`) | 129 lulus |

Seluruh `tests/` di dalam image:

- 260 lulus, 11 dilewati;
- 9 galat di `test_snapshot.py` dan 2 gagal di `test_spatial_baseline.py`, semuanya karena lingkungan, bukan
  karena perubahan ini:
  - image tidak memuat `git`, yang dipanggil untuk mencatat commit;
  - satu uji sengaja memakai locale non-UTF-8, yang di kontainer Linux gagal saat menyandikan baris perintah.

Folder `tests/` memang dikecualikan dari image (`.dockerignore`).

## Belum diverifikasi

- **Siklus di Railway.**
- **`seed` ke PostgreSQL Railway:** belum dijalankan; perlu persetujuan karena menulis ke basis data
  operasional.
- **Siklus dengan portal SPKU asli** bersama konektor ini, termasuk durasi total: SPKU ±130 detik pada TI-DO-07,
  ditambah ±1-4 menit konektor saat antrean.
- **Pengisian mundur:** jangkauan pemeriksaan konektor pendek:
  - Sentinel-5P 48 jam, GEOS-CF `ana` 72 jam;
  - VIIRS 21 hari, Landsat 32 hari.

  Selang antara akhir arsip (30 September) dan deploy tidak diisi mundur oleh konektor. Fitur langsung hanya
  memakai nilai valid terakhir, sehingga selang tersebut tidak memengaruhi inferensi.
