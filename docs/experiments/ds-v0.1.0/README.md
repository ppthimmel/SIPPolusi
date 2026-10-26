# Snapshot dataset ds-v0.1.0

Snapshot awal yang dibekukan untuk pelatihan dan evaluasi Spatial Downscaling Model (TI-AI-03, isu #14). Folder ini immutable: perubahan aturan atau data menghasilkan versi baru, bukan menimpa versi ini. Data operasional di PostgreSQL (`ground_truth.*`, `pollution.*`) terus berubah dan tidak dipakai langsung.

Dibangun dengan:

```bash
cd src/data_worker
python -m spatial_model.snapshot build --ground-truth <ekspor sippolusi_ground_truth_20261006> --out <folder baru>
python -m spatial_model.snapshot verify ../../docs/experiments/ds-v0.1.0
```

## Ringkasan

- **Rentang:** time window 2026-09-13T00:00:00Z s.d. 2026-09-30T23:00:00Z (eksklusif), UTC.
- **Wilayah:** grid 100 m EPSG:32748; 117 stasiun pada 116 sel; graf 1.044 node.
- **Target:** PM2.5 (43.207 label stasiun-jam) dan NO2 (5.372), µg/m³.
- **Split PM2.5 blind ganda:** train 18.748, val 1.008, test 756 stasiun-jam; 115 grup stasiun (81/17/17).
- **LOSO per grup:** 115 fold PM2.5, 14 fold NO2.
- **Laporan kebocoran:** lolos (8/8).

## Berkas

| Berkas | Isi |
|---|---|
| `manifest.json` | Rentang waktu, wilayah, fitur dan target beserta satuan, jumlah per split, seed, aturan split dan eksklusi, versi sumber, SHA-256 setiap berkas, commit, versi pustaka |
| `CHECKSUMS.sha256` | SHA-256 seluruh berkas (`shasum -a 256 -c CHECKSUMS.sha256`) |
| `labels.parquet` | Label stasiun-jam: nilai, jumlah bacaan, alasan eksklusi, blok, split per target |
| `stations.parquet` | Stasiun, sel, node, grup, split stasiun, penanda bias rendah |
| `splits/{train,val,test}.parquet` | Kunci label per split dan target, beserta protokolnya |
| `splits/loso_folds.parquet` | Fold leave-one-station-out per grup dan target |
| `splits/loso_sensor_distance.parquet` | `dist_nearest_sensor_m` per node tanpa grup yang ditahan |
| `split_spec.json`, `split_stats.md` | Aturan split, penugasan grup, statistik tiap split |
| `exclusion_rules.md` | Aturan eksklusi, alasan, dan jumlah bacaan terbuang |
| `normalization.json` | Statistik normalisasi fitur, target, dan rasio GEOS-CF/SPKU dari data train |
| `leakage_report.{json,md}` | Delapan pemeriksaan kebocoran |
| `stgnn/` | FeatureMatrix ST-GNN TI-AI-02 (`read_stgnn_batch`-ready); `labels.split` = split PM2.5 blind ganda (`none` di luar split), sensor = stasiun train |
| `source/` | Salinan ekspor ground truth yang dibekukan |
| `config.toml` | Konfigurasi pembangunan |

## Pemakaian

- **PM2.5, split tetap:** latih pada `splits/train.parquet`, pilih model pada `val`, laporkan pada `test`. Fitur `dist_nearest_sensor_m` di `stgnn/features.parquet` sudah memakai stasiun train saja.
- **PM2.5 dan NO2, LOSO:** untuk fold k, tahan seluruh stasiun grup `group_id` fold tersebut dari pelatihan dan dari fitur sensor (`read_stgnn_batch(..., sensors=...)` atau `splits/loso_sensor_distance.parquet`).
- **Normalisasi:** pakai `normalization.json`, atau fit ulang hanya pada baris train.
