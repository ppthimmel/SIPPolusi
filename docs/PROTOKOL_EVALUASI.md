# Protokol evaluasi Spatial Downscaling Model

Protokol evaluasi model *spatial downscaling* PM2.5 dan NO2 (Dokumen Desain subbab 4.4, EAI-01, EAI-02, ABD-01).
Dataset versi **ds-v0.3.0** adalah basis evaluasi untuk seluruh baseline dan model berikut sampai versi baru
dibekukan.

## Dataset basis

| Butir | Nilai |
|---|---|
| Versi | `ds-v0.3.0`, [`docs/experiments/ds-v0.3.0`](experiments/ds-v0.3.0/README.md) |
| Manifest | `manifest.json` (SHA-256 `a8b1f372a5a6e2f5…`), keutuhan diperiksa dengan `CHECKSUMS.sha256` |
| Ground truth | Ekspor PostgreSQL `sippolusi_ground_truth_20261006`, salinan di `source/` |
| Rentang | Time window 13 September 00.00 – 30 September 22.00 UTC (batas data pengamatan satelit dan Open-Meteo) |
| Wilayah | Grid 100 m EPSG:32748; 105 stasiun sensor darat di dalam bbox studi, 103 grup stasiun |
| Target | PM2.5 (38.211 label stasiun-jam) dan NO2 (5.372), dilatih dan dievaluasi terpisah. Satuan µg/m³, terverifikasi terhadap ISPU portal (`scripts/check_ispu_units.py`) |
| Fitur | FeatureMatrix ST-GNN TI-AI-02 (`stgnn/`, versi fitur `63aa324632a35665`), L = 24 time window; termasuk prakiraan GEOS-CF (`geoscf_fcst_*`) dan kalender (jam/hari WIB) |

Snapshot ini immutable. Perubahan aturan, rentang, atau data menghasilkan versi baru. Data operasional di
PostgreSQL (`ground_truth.*`, `pollution.*`) terus berubah dan tidak dipakai langsung untuk evaluasi.

### Riwayat versi

| Versi | Isi | Status |
|---|---|---|
| ds-v0.1.0 | Snapshot awal TI-AI-03 | **Digantikan.** 12 sensor DKI_PM25_* berjarak hingga 24 km melaporkan deret PM2.5 yang praktis sama (korelasi per jam 0,999, rasio 1,000); 45 dari 66 pasangannya melintasi train/val/test. IDW LOSO pada 12 stasiun ini RMSE 15,03 µg/m³, sedangkan pada stasiun lain 23,88 µg/m³, sehingga angka agregat tampak lebih baik daripada kenyataannya |
| ds-v0.2.0 | ds-v0.1.0 + prakiraan GEOS-CF dan kalender | **Digantikan** (label dan split sama dengan ds-v0.1.0) |
| ds-v0.3.0 | Fitur ds-v0.2.0 + aturan eksklusi `shared_signal` (kluster deret praktis sama dibuang) | **Basis** |

Split provisional TI-AI-03 v1 pada baseline TI-AI-04 (`sh-a7e5db9bb33f`) juga digantikan.

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
- **Split stasiun:** 73/15/15 grup (train/val/test), acak berstrata menurut tipe stasiun dan kota administrasi,
  seed 42.

## Protokol per target

| Target | Protokol utama | Protokol tambahan |
|---|---|---|
| PM2.5 | *Leave-one-station-out* per grup (103 fold) pada blok test; konfigurasi dipilih pada blok val | Split tetap blind ganda: pelatihan pada stasiun train × blok train, pemilihan pada stasiun val × blok val, pelaporan pada stasiun test × blok test |
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

## Model yang dievaluasi pada ds-v0.3.0

| Model | Cara memakai dataset | Status |
|---|---|---|
| Baseline IDW (TI-AI-04) | `python -m spatial_model.baseline run --manifest docs/experiments/ds-v0.3.0/manifest.json` | Run [`20261013T101200Z-idw-ds-v0.3.0-f6f15b8f`](experiments/TI-AI-04/runs/20261013T101200Z-idw-ds-v0.3.0-f6f15b8f/report.md) |
| ST-GNN | `read_stgnn_batch(docs/experiments/ds-v0.3.0/stgnn, t)`; split dari `splits/` atau kolom `split` pada `stgnn/labels.parquet` (PM2.5 blind ganda, `none` di luar split); fold dari `splits/loso_folds.parquet` | IDW-residual ST-GNN pada ds-v0.3.0 tidak lebih baik dari IDW ([perbandingan model](experiments/model-comparison-ds-v0.3.0/README.md)) |
| Perbandingan interpolator dan model terlatih | [`experiments/model-comparison-ds-v0.3.0`](experiments/model-comparison-ds-v0.3.0/README.md): LOSO blok val untuk pemilihan, LOSO blok test untuk pelaporan | PM2.5: IDW p = 1 seluruh stasiun terpilih (22,18); NO2: LightGBM seluruh fitur, target langsung (27,43), bergantung pada fitur geometri sensor dan perlu pemeriksaan sensor LCS-* |
| Algoritma pencarian rute A\* | Tidak memakai label secara langsung. Bobot polusi ruas (EdgeWeight) berasal dari Spatial Downscaling Model yang kinerjanya dilaporkan pada ds-v0.3.0, sehingga laporan rute menyebut versi dataset model tersebut | Mengikuti model yang dipakai |

## Hasil baseline IDW pada ds-v0.3.0

IDW power = 2, neighbors = 8, jarak meter pada EPSG:32748 (Tabel 3.8).

| Protokol | Target | n stasiun-jam | Stasiun | MAE | RMSE | R² | Bias | Rerata ground truth |
|---|---|---|---|---|---|---|---|---|
| LOSO per grup, blok test | PM2.5 | 4.358 | 97 | 17,32 | 24,00 | −0,04 | −1,57 | 31,72 |
| LOSO per grup, blok test | NO2 | 604 | 13 | 38,81 | 46,99 | 0,14 | +22,10 | 54,65 |
| Split tetap blind ganda | PM2.5 | 689 | 15 | 13,95 | 18,74 | 0,14 | −8,27 | 36,30 |

- **Konfigurasi terpilih pada blok val:**
  - PM2.5: power = 1 dengan seluruh stasiun (RMSE test 22,18 µg/m³, R² 0,11).
  - NO2: power = 2 dengan seluruh stasiun (RMSE test 44,66 µg/m³, R² 0,22).
- **Dibanding ds-v0.1.0:**
  - LOSO PM2.5 naik dari 23,06 menjadi 24,00 µg/m³, karena 12 stasiun yang "mudah" (kembarannya ada di antara
    sumber) tidak lagi dihitung.
  - Pada stasiun dan jam yang sama di luar kluster, RMSE praktis tidak berubah (23,88 → 24,00 µg/m³).
  - NO2 tidak berubah, karena kluster hanya melaporkan PM2.5.
- **Split tetap memakai 15 stasiun uji yang berbeda dari ds-v0.1.0,** sehingga angkanya tidak dapat dibandingkan
  langsung dengan versi sebelumnya. Model berikutnya dibandingkan dengan baseline pada protokol dan versi yang sama.
