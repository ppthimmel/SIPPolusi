# Konektor sumber sekunder dan FeatureMatrix (TI-SE-02)

Langkah B2-B3 dan B11-B14 siklus Data Worker (Dokumen Desain Tabel 3.9, Gambar 3.7): mengambil data
satelit, model atmosfer, dan meteorologi setiap jam, memeriksa mutunya, menyelaraskannya ke grid 100 m pada
`TimeWindow` yang sama, lalu mencatat FeatureMatrix beserta `feature_version`. Isu #2.

Pengambilan, transformasi, dan penyimpanan dipisah sehingga setiap tahap dapat diuji mandiri:

| Tahap | Letak | Isi |
|---|---|---|
| Pengambilan | `connectors/http.py`, `connectors/stac.py` | Timeout, coba ulang, dan pencatatan per sumber (Tabel 3.19); katalog STAC dan token SAS Planetary Computer |
| Transformasi | `connectors/<sumber>.py` | Parser respons dan granule → baris berskema data latih; validasi format, rentang fisik, dan duplikat |
| Penyimpanan | `feature_store/store.py` | Skema PostgreSQL `features`; penulisan idempoten, revisi, dan retensi |
| Pembaca fitur | `feature_store/reader.py` | `StoreSourceReader`: aturan as-of `SourceReader` TI-AI-02, sumber dari PostgreSQL |
| Langkah siklus | `feature_store/steps.py` | Enam langkah pada `acquisition.DEFAULT_STEPS` |
| CLI | `feature_store/cli.py` | `python main.py features status/fetch/seed/rebuild` |

## Sumber

Produk, subset, QA, dan satuan sama dengan data latih TI-AI-01 (`dataset_processed`). Kesamaan ini diuji
dengan mengambil ulang produk arsip lewat konektor lalu membandingkannya dengan Parquet data latih (bukti:
[`docs/software/verification-TI-SE-02.md`](../../../docs/software/verification-TI-SE-02.md)).

| Sumber | Langkah | Akses | Kredensial | Frekuensi pemeriksaan | Timeout dan coba ulang |
|---|---|---|---|---|---|
| Open-Meteo `/v1/forecast` | `fetch_meteo` | HTTPS JSON, satu permintaan per siklus (−6,2; 106,85; 2 hari lalu + 2 hari depan) | Tidak ada | Setiap siklus | 30 detik, tiga kali |
| GEOS-CF v2 `ana` | `fetch_satellite_archive` | HEAD datashare NCCS (waktu terbit) + subset OPeNDAP 3 × 3 sel | Tidak ada | Setiap siklus; jam 6-72 jam terakhir yang belum tersimpan | 60 detik (subset 180 detik), tiga kali |
| GEOS-CF v2 `fcst` | `fetch_satellite_archive` | Run 09z, 120 lead, setelah lead terakhir terbit | Tidak ada | Setiap siklus sampai run tersimpan | 60 detik (subset 180 detik), tiga kali |
| Landsat 8/9 C2 L2 | `fetch_satellite_archive` | STAC Planetary Computer, jendela COG wilayah studi | Token SAS anonim | Sekali sehari; satu scene baru per siklus | 120 detik (GDAL), tiga kali |
| VIIRS VNP46A2 | `fetch_satellite_archive` | NASA CMR + unduh LAADS DAAC (±20 MB) | `EARTHDATA_TOKEN` (Bearer) | Sekali sehari; tiga granule per siklus | 120 detik per berkas, tiga kali |
| Sentinel-5P NO2 NRTI | `fetch_tropospheric_column` | STAC Planetary Computer, HTTP Range (±6 MB dari granule 65 MB) | Token SAS anonim | Setiap siklus; enam granule per siklus | 120 detik, tiga kali |

- **Gagal sementara:** galat jaringan, timeout, HTTP 429, dan 5xx dicoba ulang dengan jeda eksponensial
  (429 mematuhi `Retry-After`). Setiap pengulangan dicatat sebagai peringatan.
- **Gagal permanen:** sisa percobaan habis atau HTTP 4xx lain menjadi `SourceError`. Sumber dicatat `failed`,
  sumber lain dan langkah berikutnya tetap berjalan (Tabel 3.12, PF-14).
- **Format berubah:** kunci, variabel, satuan, zona waktu, grid, atau koordinat yang berbeda dari data latih
  menjadi `FormatError` dan dicatat `failed` dengan catatan "format respons berubah". Granule yang formatnya
  berubah dilewati; produk lain tetap diproses (`degraded`).
- **Kredensial:** hanya dari variabel lingkungan, dikirim lewat header atau URL bertanda tangan, dan tidak pernah
  masuk log, catatan, maupun basis data (`safe_url` membuang query). Tanpa `EARTHDATA_TOKEN`, VIIRS dicatat
  `disabled`.
- **Penyimpangan dari Dokumen Desain:** Sentinel-5P dan Landsat diambil dari Microsoft Planetary Computer, bukan
  Copernicus Data Space Ecosystem atau Earth Engine/EarthExplorer (Tabel 3.18). Data latih berasal dari
  Planetary Computer, dan aksesnya tidak memerlukan akun.

## Penyimpanan (skema `features`)

Dibuat otomatis pada siklus pertama (`CREATE ... IF NOT EXISTS`, dikunci advisory lock). Nama skema dapat
diganti dengan `FEATURES_DB_SCHEMA`.

| Tabel | Kunci | Isi |
|---|---|---|
| `fetch_log` | `fetch_id` | Satu baris per pengambilan sumber: status, produk baru, baris ditulis, permintaan, coba ulang, gagal, catatan, rincian |
| `weather_hourly` | `(time_utc, fetched_utc)` | Revisi Open-Meteo per jam; revisi baru hanya bila nilainya berubah; `qc` mencatat variabel yang dibuang karena di luar rentang |
| `geoscf_ana_hourly` | `(time_window_start, cell_id)` | PM2.5, NO2 (ppb dan µg/m³), waktu terbit, latensi |
| `geoscf_fcst_hourly` | `(init_utc, time_window_start, cell_id)` | Prakiraan per run, lead, waktu terbit lead pertama dan terakhir |
| `satellite_product` | `(source, product_id)` | Satu baris per scene/granule: waktu observasi, produksi, katalog, versi pemrosesan, URL tanpa query, ringkasan QA |
| `satellite_layer` | `(source, product_id, layer)` | Layer per sel Landsat dan VIIRS sebagai larik terkompresi zlib, urutan sel tetap |
| `sentinel5p_observation` | `(product_id, grid_id)` | Median NO2 piksel lolos QA per sel 5 km, presisi, jumlah piksel valid |
| `feature_window` | `(time_window_start, feature_version)` | Manifest FeatureMatrix: τ, `built_utc`, kolom, ketersediaan sumber, produk dan stasiun yang dipakai |

- **Penanda data hilang:** NULL, bukan nol. Pembaca membentuk `<nilai>_available` dari NULL.
- **Stempel waktu dan identitas sumber:** setiap baris membawa `fetched_utc` dan `fetch_id`, dan setiap
  produk membawa `observed_at` dan `produced_at` (proksi waktu tersedia).
- **Duplikat:** kunci primer menolak produk dan jam yang sudah ada (`ON CONFLICT DO NOTHING`), sehingga
  eksekusi ulang menulis 0 baris baru.
- **Retensi:** dipangkas pada `write_features`. Retensi melebihi umur as-of maksimum data latih (Landsat ±200
  hari, Sentinel-5P ±10, VIIRS ±17).

  | Sumber | Retensi (hari) |
  |---|---|
  | Open-Meteo | 45 |
  | GEOS-CF `ana` | 14 |
  | GEOS-CF `fcst` | 7 |
  | Sentinel-5P | 30 |
  | VIIRS | 60 |
  | Landsat | 400 |
  | `fetch_log` | 90 |

- **Ukuran:**
  - setelah `seed` dengan retensi bawaan, ±12 MB, terutama riwayat Landsat;
  - seluruh arsip Januari-September 2026, ±45 MB;
  - pertumbuhan per hari dalam keadaan tunak di bawah 1 MB.

## Langkah siklus

| Langkah | Status | Isi |
|---|---|---|
| `fetch_satellite_archive` | Terparah dari GEOS-CF `ana`/`fcst`, Landsat, VIIRS | `sources` memuat status, jumlah permintaan, coba ulang, gagal, dan produk baru setiap sumber |
| `fetch_tropospheric_column` | Sentinel-5P | Sama |
| `fetch_meteo` | Open-Meteo | Sama |
| `run_quality_control` | `ok`; `degraded` bila sebagian sumber `available=false`; `unavailable` bila semuanya | Data terbaru yang boleh dipakai pada τ = akhir time window dan umurnya; terlalu basi berarti `available=false` |
| `align_spatiotemporal` | `ok` | FeatureMatrix 195.782 sel aktif × 89 kolom pada τ (kolom dan tipe `features.parquet` TI-AI-02) |
| `write_features` | `ok` | `feature_version` = SHA-256 isi; manifest ke `feature_window`; Parquet + `manifest.json` ke `FEATURE_STORE_DIR` bila diatur |

- **Status sumber tambahan:** `not_due` (katalog sudah diperiksa dalam 24 jam terakhir dan tidak ada antrean) dan
  `disabled` tidak menurunkan status langkah.
- **Fallback (Tabel 3.19):** sumber yang gagal tidak menghapus data sebelumnya. Pembaca as-of memakai nilai
  terakhir yang tersedia pada τ, dan umurnya tercatat pada `<nilai>_age_hours`. Contohnya prakiraan GEOS-CF
  run sebelumnya untuk jam yang sama, atau cuaca jam sebelumnya.
- **Ambang basi pada `run_quality_control`:**
  - Open-Meteo 12 jam;
  - GEOS-CF `ana` 72 jam;
  - Sentinel-5P 14 hari;
  - VIIRS 30 hari;
  - Landsat 240 hari.
- **Penyelarasan:** sama dengan pembaca data latih. Tidak ada imputasi; nilai hilang tetap NaN dengan
  penandanya. `dist_nearest_sensor_m` memakai stasiun SPKU yang memiliki pengukuran PM2.5/NO2 bersih pada time
  window tersebut.
- **Bangun ulang:** `python main.py features rebuild --window-start ...` membaca sumber as-of `built_utc` dengan
  stasiun yang tercatat, lalu memeriksa bahwa hasilnya sama dengan `feature_version`. Revisi yang masuk
  sesudahnya tidak mengubah hasil; data yang sudah terpangkas retensi terdeteksi sebagai ketidakcocokan.
- **Penyimpangan dari Dokumen Desain (Tabel 3.11):** FeatureMatrix tidak disimpan utuh di object storage setiap
  jam (±196 ribu baris per jam). Yang disimpan adalah manifest dan `feature_version`, karena matriks dapat
  dibangun ulang persis dari tabel sumber. Parquet utuh ditulis bila `FEATURE_STORE_DIR` diatur.

## Perintah

```bash
python main.py features status [--hours 48] [--json]    # pengambilan per sumber, ketersediaan pada τ, ukuran tabel
python main.py features fetch [--window-start ...]     # konektor dan kendali mutu saja (menulis)
python main.py features seed                           # muat riwayat dataset_processed (menulis; sekali)
python main.py features rebuild --window-start 2026-10-27T04:00Z [--out f.parquet]
```

Image Docker tidak memuat `dataset_processed`, sehingga riwayat Landsat sebelum konektor berjalan hanya tersedia
lewat `seed`. Jalankan dari mesin yang memiliki repositori dengan `DATABASE_URL` basis data tujuan. Tanpa seed,
NDVI/NDBI baru terisi setelah scene cerah berikutnya.

## Pengaturan

| Variabel | Bawaan | Fungsi |
|---|---|---|
| `EARTHDATA_TOKEN` | kosong | Token Earthdata Login untuk VIIRS (LAADS DAAC); kosong berarti VIIRS `disabled`. Berlaku 60 hari |
| `FEATURES_SOURCES` | `all` | `all`, `none`, atau daftar dari `open_meteo,geoscf_ana,geoscf_fcst,sentinel5p,landsat,viirs` |
| `FEATURES_DB_SCHEMA` | `features` | Skema tabel penyimpanan |
| `FEATURE_STORE_DIR` | kosong | Bila diatur, Parquet dan `manifest.json` FeatureMatrix per time window ditulis ke sini |
