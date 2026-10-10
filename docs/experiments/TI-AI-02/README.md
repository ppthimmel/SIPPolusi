# TI-AI-02: FeatureMatrix ST-GNN

Bukti verifikasi isu #13. Kode dan aturan penyelarasan:
[`src/data_worker/feature_matrix`](../../../src/data_worker/feature_matrix/README.md). Basisnya adalah bundle
ST-GNN `jakarta_processed_bundle_20261007` (FedrianzD), ditambah jarak sensor, penanda ketersediaan, data
dictionary, dan laporan kualitas.

## Run `stgnn_2026-09-13_30`

```bash
cd src/data_worker
python -m feature_matrix export --ground-truth <sippolusi_ground_truth_20261006> --out <folder>
```

**Masukan dan lingkungan:**
- Dibangun dari commit `52b816b` dengan working tree bersih.
- Masukannya adalah `dataset_processed` repo dan ekspor `sippolusi_ground_truth_20261006`.
- SHA-256 seluruh 53 berkas masukan tercatat di `manifest.json`: 50 dari `dataset_processed` dan 3 dari ekspor
  ground truth. Hash 49 berkas identik dengan provenans bundle; yang berbeda hanya `manifest.json` Landsat,
  Sentinel-5P, VIIRS, dan Open-Meteo.

**Keluaran:**
- `features.parquet` (3,0 MB) dan tabel lain tidak di-commit; hash-nya ada di `manifest.json`.
- Dua run terpisah menghasilkan hash keluaran yang identik.

| Berkas | Isi |
|---|---|
| [`quality_report.md`](stgnn_2026-09-13_30/quality_report.md) (`.json`) | Hasil pemeriksaan |
| [`manifest.json`](stgnn_2026-09-13_30/manifest.json) | Hash masukan/keluaran, kebijakan, asumsi, argumen, commit |
| [`bundle_comparison.json`](stgnn_2026-09-13_30/bundle_comparison.json) | Perbandingan dengan keluaran bundle |
| [`sample_features_200.csv`](stgnn_2026-09-13_30/sample_features_200.csv) | Sampel 200 baris fitur (tanpa identitas stasiun) |

**Ukuran:**
- 1.044 node (116 sel berlabel + 928 sel konteks), 4.654 edge;
- 454 jam (2026-09-12 02:00 – 2026-09-30 23:00 UTC, termasuk 23 jam pemanasan), 473.976 baris × 69 kolom;
- 43.690 label stasiun-jam dari 117 stasiun: train 29.244, validation 7.008, test 7.438.

`read_stgnn_batch` untuk satu jam target menghasilkan:
- `temporal` [1044, 24, 15];
- `land_static` [1044, 4];
- `sensor_static` [1044, 1];
- `edge_index` [2, 4654].

## Reproduksi bundle

Reader dan pembentuk label di bundle berada di notebook yang tidak ikut dibundel, sehingga keduanya ditulis
ulang dan diverifikasi terhadap keluaran bundle:

| Tabel | `--viirs-cell-rule nearest_listed` (aturan bundle) | `tile` (bawaan repo) |
|---|---|---|
| `nodes`, `edges`, `labels` | Identik | Identik |
| `features`, 53 kolom bersama | Identik; `latitude` berbeda ≤ 8,9 × 10⁻¹⁶° (1 ULP) | Identik, kecuali `ntl` (72,6% baris) dan jam/umur/scene `ntl` (3,4%) |

**Mengapa `ntl` berbeda dengan aturan `tile`:**
- Kolom `longitude`/`latitude` di `viirs/daily.parquet` adalah sudut barat laut sel VIIRS, bukan pusatnya.
- Bundle memilih sel dengan koordinat terdekat, sehingga setiap sel 100 m mengambil sel VIIRS yang bergeser
  setengah piksel (±230 m) ke tenggara.
- Repo memakai sel yang memuat pusat sel 100 m menurut indeks tile; uji `test_viirs_aturan_tile_memakai_sudut_barat_laut`.

## Pemenuhan acceptance criteria #13

| Kriteria | Bukti |
|---|---|
| Setiap baris dapat ditelusuri ke sel grid dan TimeWindow | Kunci (`grid_id`, `time_utc`) unik dan lengkap untuk 1.044 × 454; `node_index` ↔ `grid_id` di `nodes.parquet`; jam dan scene sumber setiap nilai tercatat (`*_observed_at`, `*_produced_at`, `*_scene_id`, `geoscf_*_time_window_*`, `geoscf_*_available_at_utc`, `weather_time_utc`) |
| Kolom mengikuti data dictionary dengan tipe/satuan konsisten | [`feature_spec.json`](../../../src/data_worker/feature_matrix/feature_spec.json) merinci tipe, satuan, peran, sumber, dan penyelarasan 69 kolom fitur dan 16 kolom label; pemeriksaan skema lolos |
| Missingness tercatat eksplisit | 15 penanda `<nilai>_available`, nilai hilang tetap NaN tanpa imputasi; 0 pelanggaran konsistensi |
| Pemeriksaan melaporkan cakupan, duplikasi, dan proporsi nilai hilang | `quality_report.{json,md}`: cakupan sel × jam, kunci ganda, nilai hilang dan ketersediaan per fitur (total dan minimum per jam), kebocoran waktu (0), label per split |

## Temuan

- **Ketersediaan fitur:**
  - nilai temporal tersedia 100%, karena nilai satelit terakhir terbawa tanpa batas umur;
  - NDVI dan NDBI 97,4%;
  - LST 3,6%, sehingga tidak dipakai model.
- **Umur median data:**
  - GEOS-CF 18 jam, karena latensi publikasi `ana`;
  - Sentinel-5P 40 jam;
  - VIIRS 212 jam;
  - Landsat 335 jam.
- **Jarak sensor:** dengan seluruh stasiun berlabel, sel berlabel berjarak ≤ 66 m dan sel konteks 51–208 m.
  - Pada graf transduktif ini, fitur tersebut nyaris hanya menandai sel berstasiun.
  - Untuk evaluasi lokasi tak terlihat (TI-AI-03), fitur ini dihitung ulang per fold tanpa stasiun uji
    (`read_stgnn_batch(..., sensors=...)`).
- **Split bundle kronologis dan transduktif:** stasiun yang sama muncul di ketiga split. Protokol evaluasi
  lokasi tak terlihat dikerjakan pada TI-AI-03.
