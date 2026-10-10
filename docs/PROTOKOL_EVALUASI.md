# Protokol evaluasi Spatial Downscaling Model

Protokol evaluasi model *spatial downscaling* PM2.5 dan NO2 (Dokumen Desain subbab 4.4, EAI-01, EAI-02, ABD-01).
Dataset versi **ds-v0.1.0** adalah basis evaluasi untuk seluruh baseline dan model berikut sampai versi baru
dibekukan.

## Dataset basis

| Butir | Nilai |
|---|---|
| Versi | `ds-v0.1.0`, [`docs/experiments/ds-v0.1.0`](experiments/ds-v0.1.0/README.md) |
| Manifest | `manifest.json` (SHA-256 `f094a4b061c0c26e…`), keutuhan diperiksa dengan `CHECKSUMS.sha256` |
| Ground truth | Ekspor PostgreSQL `sippolusi_ground_truth_20261006`, salinan di `source/` |
| Rentang | Time window 13 September 00.00 – 30 September 22.00 UTC (batas data pengamatan satelit dan Open-Meteo) |
| Wilayah | Grid 100 m EPSG:32748; 117 stasiun sensor darat di dalam bbox studi, 115 grup stasiun |
| Target | PM2.5 (43.207 label stasiun-jam) dan NO2 (5.372), dilatih dan dievaluasi terpisah. Satuan µg/m³, terverifikasi terhadap ISPU portal (`scripts/check_ispu_units.py`) |
| Fitur | FeatureMatrix ST-GNN TI-AI-02 (`stgnn/`, versi fitur `26ff0f6a4e266fd9`), L = 24 time window |

Snapshot ini immutable. Perubahan aturan, rentang, atau data menghasilkan versi baru (`ds-v0.2.0`, …). Data
operasional di PostgreSQL (`ground_truth.*`, `pollution.*`) terus berubah dan tidak dipakai langsung untuk evaluasi.
Split provisional TI-AI-03 v1 pada baseline TI-AI-04 (`sh-a7e5db9bb33f`) digantikan oleh versi ini.

## Pembagian data

- **Grup stasiun:**
  - Stasiun pada sel 100 m yang sama, berjarak < 300 m, atau duplikat penyedia membentuk satu grup.
  - Grup adalah satuan split dan satuan fold, sehingga stasiun berimpit (mis. DKJ37 dan DKI98, 1 m) tidak pernah
    berada di sisi yang berbeda.
- **Blok temporal:**
  - train: 13–23 September;
  - val: 25–27 September;
  - test: 29 September 00.00 – 30 September 22.00 UTC.
  - Jeda antarblok 25 jam ≥ max(L, 24 jam), sehingga jendela masukan sampel val/test tidak memuat jam blok
    sebelumnya.
- **Split stasiun:** 81/17/17 grup (train/val/test), acak berstrata menurut tipe stasiun dan kota administrasi,
  seed 42.

## Protokol per target

| Target | Protokol utama | Protokol tambahan |
|---|---|---|
| PM2.5 | *Leave-one-station-out* per grup (115 fold) pada blok test; konfigurasi dipilih pada blok val | Split tetap blind ganda: pelatihan pada stasiun train × blok train, pemilihan pada stasiun val × blok val, pelaporan pada stasiun test × blok test |
| NO2 | *Leave-one-station-out* per grup (14 fold) pada blok test; konfigurasi dipilih pada blok val | Tidak ada; 14 stasiun tidak cukup untuk stasiun validasi terpisah |

**Aturan yang wajib diikuti setiap model:**

1. **Stasiun yang ditahan tidak dipakai.** Pada fold k, stasiun grup yang ditahan tidak dipakai untuk pelatihan,
   pemilihan konfigurasi, normalisasi target, maupun fitur berbasis sensor.
   - `dist_nearest_sensor_m` per fold tersedia di `splits/loso_sensor_distance.parquet`.
   - Untuk split tetap, `stgnn/features.parquet` sudah memakai stasiun train saja.
2. **Normalisasi hanya dari data train.**
   - Normalisasi fitur dan target di-fit hanya pada data train, yaitu `normalization.json`.
   - Statistik target NO2 tersedia per fold.
3. **Fitur hanya dari data yang tersedia pada waktu inferensi.**
   - Fitur pada time window t hanya memakai data dengan waktu tersedia ≤ akhir time window:
     - GEOS-CF `available_at_utc`;
     - `produced_at` satelit;
     - waktu terbit run Open-Meteo yang diasumsikan, yaitu awal run 6 jam + 8 jam.
   - Pemeriksaannya ada di `leakage_report.md`.
4. **Ground truth tidak diubah.** Label ground truth tidak diinterpolasi; time window tanpa label tidak dievaluasi.

## Metrik

Dihitung dalam µg/m³ kecuali R², per target, *pooled* atas seluruh stasiun-jam, dan sebagai median per stasiun:

- MAE;
- RMSE;
- bias (ŷ − y);
- R² = 1 − SSres/SStot;
- n stasiun-jam.

Galat prediksi dianalisis menurut stasiun, kota administrasi, jam WIB, jarak ke stasiun sumber terdekat, dan
ketersediaan data pendukung, seperti pada baseline TI-AI-04.

## Model yang dievaluasi pada ds-v0.1.0

| Model | Cara memakai dataset | Status |
|---|---|---|
| Baseline IDW (TI-AI-04) | `python -m spatial_model.baseline run --manifest docs/experiments/ds-v0.1.0/manifest.json` | Run [`20261010T133746Z-idw-ds-v0.1.0-f6f15b8f`](experiments/TI-AI-04/runs/20261010T133746Z-idw-ds-v0.1.0-f6f15b8f/report.md) |
| ST-GNN | `read_stgnn_batch(docs/experiments/ds-v0.1.0/stgnn, t)`; split dari `splits/` atau kolom `split` pada `stgnn/labels.parquet` (PM2.5 blind ganda, `none` di luar split); fold dari `splits/loso_folds.parquet` | Belum dilatih |
| Algoritma pencarian rute A\* | Tidak memakai label secara langsung. Bobot polusi ruas (EdgeWeight) berasal dari Spatial Downscaling Model yang kinerjanya dilaporkan pada ds-v0.1.0, sehingga laporan rute menyebut versi dataset model tersebut | Mengikuti model yang dipakai |

## Hasil baseline IDW pada ds-v0.1.0

IDW power = 2, neighbors = 8, jarak meter pada EPSG:32748 (Tabel 3.8).

| Protokol | Target | n stasiun-jam | Stasiun | MAE | RMSE | R² | Bias | Rerata ground truth |
|---|---|---|---|---|---|---|---|---|
| LOSO per grup, blok test | PM2.5 | 4.905 | 109 | 16,42 | 23,06 | −0,06 | −1,29 | 30,94 |
| LOSO per grup, blok test | NO2 | 604 | 13 | 38,81 | 46,99 | 0,14 | +22,10 | 54,65 |
| Split tetap blind ganda | PM2.5 | 756 | 17 | 11,99 | 16,01 | 0,02 | +3,84 | 24,58 |

- **Konfigurasi terpilih pada blok val:**
  - PM2.5: power = 1 dengan seluruh stasiun (RMSE test 21,37 µg/m³, R² 0,09).
  - NO2: power = 2 dengan seluruh stasiun (RMSE test 44,66 µg/m³, R² 0,22).
- **Split tetap memberi RMSE lebih rendah,** karena 17 stasiun uji memiliki rerata dan simpangan lebih rendah
  (24,6 dan 16,2 µg/m³) daripada seluruh stasiun.
  - Kedua angka tidak dapat dibandingkan langsung.
  - Model berikutnya dibandingkan dengan baseline pada protokol yang sama.
