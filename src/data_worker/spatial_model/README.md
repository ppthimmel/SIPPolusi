# Spatial Downscaling Model

Dataset: **ds-v0.3.0**. Grid 100 m → agregasi ruas berbobot panjang → `EdgeWeight`.

[Spesifikasi aktif](../../../docs/SPATIAL_MODEL_SPEC.md) menetapkan GEOS-CF sebagai fitur
pelatihan dan operasi, tanpa pergantian AOD/MERRA-2 atau suku latar aditif setelah prediksi.

| Polutan | Model | Keluaran |
|---|---|---|
| PM2.5 | IDW + koreksi ST-GNN | `max(0, IDW + target_scale × correction)` |
| NO₂ | LightGBM dengan seluruh 62 fitur | Konsentrasi langsung, minimal nol |

Pemilihan ini adalah keputusan implementasi. Hasil PM2.5 belum menunjukkan keuntungan konsisten
terhadap interpolator yang disetel. Keuntungan NO₂ sensitif terhadap geometri dan bias jaringan sensor;
hasil pada stasiun tidak membuktikan akurasi pada seluruh grid.

## Organisasi

```text
spatial_model/
├── models/       # ST-GNN, LightGBM, dan IDW
├── data/         # grid, observasi/QC, fitur, validasi dan snapshot/
├── evaluation/   # metrics.py bersama; idw/, stgnn/, lightgbm/
├── inference/    # pipeline, Worker, serving, agregasi, confidence, cache
├── artifacts/    # bundle model dan registry MLflow/MinIO
├── configs/      # konfigurasi ST-GNN, LightGBM, IDW, snapshot
└── training/     # perintah train, register, promote
```

`models/stgnn/` dan `models/lightgbm/` memuat implementasi dan training masing-masing model.
API orkestrasi tetap tersedia melalui `spatial_model.inference`; perintah training tetap
`python -m spatial_model.training`. Package root hanya memuat entry point dan dokumentasi.

Perintah evaluasi IDW: `python -m spatial_model.evaluation.idw`.
Perintah snapshot: `python -m spatial_model.data.snapshot`.
Implementasi IDW bersama tetap berada di `models/idw.py`; `evaluation/idw/` menjalankan eksperimen.

Kedua model terlatih memakai nama modul yang sama: `model.py` untuk model/prediksi,
`data.py` untuk masukan spesifik model, dan `train.py` untuk fitting. ST-GNN juga memerlukan
`preprocessing.py` dan `sensors.py`. IDW cukup satu modul karena tidak memiliki proses fitting.
Normalisasi observasi dan fallback grid berada di `inference/fallback.py`; indeks paparan
berada di `inference/exposure.py`. Pembacaan sensor/QC berada di `data/observations.py`.
`inference/runtime.py` tidak mengimpor pipeline; pipeline mengorkestrasi runtime.
Helper kriging dan bootstrap eksperimen berada di `evaluation/comparison.py`.

Konfigurasi yang dipelihara hanya berada di `configs/`: `stgnn.json`, `lightgbm.json`,
`idw_baseline.toml`, dan `ds-v0.3.0.toml`. Training memakai JSON model; evaluasi IDW dan
runtime memakai TOML IDW yang sama. Argumen CLI mengatur run (epoch, fold, perangkat, seed);
`THREADS` dapat mengganti jumlah thread LightGBM. Bundle baru merekam konfigurasi dan hash
berkas konfigurasi. Salinan konfigurasi dalam snapshot dan hasil eksperimen adalah provenance beku.

[Verifikasi standardisasi](../../../docs/experiments/TI-AI-05/standardization_verification.json)
mencatat prediksi dan metrik yang identik sebelum/sesudah pemindahan modul dan konfigurasi
(PM2.5 fixed split satu epoch; NO₂ LOSO fold 0).

- [Notebook training](../../../notebooks/spatial_model): satu notebook per model, memanggil Python yang sama.

## Training

Dari `src/data_worker`, dengan dependensi `requirements.txt`:

```bash
python -m spatial_model.training train pm25 --snapshot ../../docs/experiments/ds-v0.3.0 --out ../../.cache/training/pm25 --device cpu
python -m spatial_model.training train no2 --snapshot ../../docs/experiments/ds-v0.3.0 --out ../../.cache/training/no2
```

Gunakan folder baru per run. `--resume` melanjutkan ST-GNN dari `progress.pt`, termasuk optimizer,
scheduler dan RNG. `--fold N` memakai grup LOSO pada dataset. Checkpoint PM2.5 dipilih pada validation,
termasuk epoch nol (IDW). Satu epoch hanya smoke test. Test tidak dipakai memilih checkpoint.
ST-GNN memuat 24 jam fitur termasuk prakiraan GEOS-CF dan kalender; target-group readings dikeluarkan
dari baseline label dan receptive field saat training. NO₂ memakai parameter full-feature terpilih
pada validation dari eksperimen yang tersimpan, dengan target langsung.

Bundle memuat bobot, preprocessing, fitur berurutan, satuan, stasiun sumber, parameter, versi pustaka,
hash dataset/kode dan metrik. ST-GNN memakai `idw_residual_v1`, LightGBM format native `model.txt`.

## Evaluasi checkpoint

Evaluasi terpisah tidak melatih ulang atau mem-fit preprocessing. Training memanggil evaluator yang
sama setelah pemilihan checkpoint; validation untuk early stopping tetap berada dalam training.
Folder `evaluation/` memuat metrik bersama dan evaluator tiap model.

```bash
python -m spatial_model.evaluation stgnn --snapshot ../../docs/experiments/ds-v0.3.0 --checkpoint ../../.cache/training/pm25/best.pt --out ../../.cache/evaluation/pm25
python -m spatial_model.evaluation lightgbm --snapshot ../../docs/experiments/ds-v0.3.0 --checkpoint ../../.cache/training/no2-fold0 --out ../../.cache/evaluation/no2-fold0
python -m spatial_model.evaluation idw run --manifest ../../docs/experiments/ds-v0.3.0/manifest.json --out ../../.cache/evaluation/idw
```

Gunakan `--split val`, `test`, atau `both` (bawaan). ST-GNN juga menerima `--device`,
`--batch-size`, dan `--threads`. Gunakan folder keluaran baru. Evaluator memverifikasi hash snapshot
dan kebijakan sensor checkpoint. Laporan mencakup prediksi Parquet, metrik pooled/per stasiun,
serta `evaluation.json` berisi hash checkpoint, snapshot, protokol, dan split.
Checkpoint dengan hash snapshot berbeda ditolak; metadata checkpoint lama tidak ditulis ulang
agar tampak cocok. Gunakan snapshot yang identik atau latih ulang pada snapshot saat ini.

LightGBM memerlukan checkpoint `--fold N` dari training LOSO. Serving fit tanpa fold memakai semua
stasiun dan tidak menghasilkan klaim evaluasi LOSO; `metrics.json` mencatat status `not_evaluated`.
ST-GNN mempertahankan protokol checkpoint (fixed split atau LOSO) dan perbandingan IDW berpasangan.

[Verifikasi pemisahan evaluator](../../../docs/experiments/TI-AI-05/evaluation_verification.json):
prediksi dan metrik identik antara keluaran training dan evaluator terpisah untuk PM2.5
(satu epoch, fixed split) dan NO₂ (LOSO fold 0). Ini verifikasi workflow, bukan benchmark baru.

## MLflow dan MinIO

Metadata MLflow disimpan di PostgreSQL; payload di bucket MinIO `mlflow`.
Atur `MLFLOW_TRACKING_URI`, `MLFLOW_S3_ENDPOINT_URL`, `AWS_ACCESS_KEY_ID`, dan
`AWS_SECRET_ACCESS_KEY` lewat environment. Gunakan endpoint localhost untuk training pada host,
hostname service untuk kontainer. Kredensial tidak disimpan dalam model atau notebook.

```bash
python -m spatial_model.training register ../../.cache/training/pm25/bundle
python -m spatial_model.training register ../../.cache/training/no2/bundle
python -m spatial_model.training promote pm25 1
python -m spatial_model.training promote no2 1
```

Nama registry: `spatial-downscaling-pm25` dan `spatial-downscaling-no2`. Registrasi membuat versi
dan alias `candidate`. Promotion memverifikasi bundle sebelum menetapkan `production`.
Worker menyelesaikan alias sekali per run, merekam versi konkret, mengunduh lewat MLflow,
dan memverifikasi hash pada cache lokal `SPATIAL_MODEL_CACHE`.

## Inferensi dan cache

Worker membaca 24 jam sensor dengan QC/hourly aggregation yang sama dengan baseline.
`SPATIAL_FEATURE_ROOT` menunjuk keluaran preprocessing yang dikonsumsi `SourceReader`.
Fitur mengikuti aturan waktu tersedia dataset; input masa depan ditolak. ST-GNN memakai tile
dengan halo satu hop, LightGBM diproses dalam batch. IDW fallback diterapkan per polutan secara mandiri.
Nilai hilang berbeda dari nol.

Ruas dipotong pada batas sel. Konsentrasi ruas adalah `Σ(length × concentration) / Σ(length)` atas
potongan dengan nilai tersedia. Indeks paparan dihitung sesudah agregasi. `model_metadata` merekam
sumber, versi dan alasan fallback per polutan; identitas cache mengikat input, model, grid dan graf.
Confidence bersifat provisional dan dibatasi 0,39 sampai kalibrasi tersedia.

Setiap penulisan cache wajib menyimpan trace di PostgreSQL: `inference_run`,
`grid_prediction`, dan `edge_cell`, dengan tautan `EdgeWeight.trace_run_id`. Writer menolak
trace yang hilang atau tidak merekonstruksi konsentrasi, indeks paparan, dan confidence ruas.
Cache dan trace di-commit dalam satu transaksi; kegagalan membatalkan keduanya. Run ID
immutable mengikat versi model, fingerprint input, graf/grid, versi format trace, dan kebijakan
confidence. Retensi menghapus window dan trace bersamaan. Parquet lokal tetap opsional.

Backend mengembalikan `trace_run_id` bersama bobot dan menyediakan
`GET /v1/pollution/edge-weights/{edge_id}/trace?time_window_start=latest` untuk sel, panjang,
nilai sumber, metadata model, waktu, dan checksum. Baris historis tidak diberi trace sintetis:
endpoint mengembalikan `legacy_unavailable`; pipeline menghitung ulang jika window tersebut
diproses kembali. Penelusuran dijamin selama window cache masih dipertahankan.

Sebelum memperbarui backend/cache API:

```bash
psql "$DATABASE_URL" -f database/spatial_migration.sql
```

Worker menerapkan migrasi idempoten saat `init_db`. Cache writing tetap opt-in:
`DOWNSCALE_WRITE_CACHE=1`; retensi `DOWNSCALE_KEEP_WINDOWS` (bawaan 6). Penulisan atomik, backend
hanya membaca window `complete`. Metadata bertambah tanpa mengganti kolom konsentrasi yang ada.

## Verifikasi

Dari root proyek, stack terisolasi:

```bash
docker compose -p sippolusi-spatial-verify -f docker-compose.spatial-verification.yml up -d --build
```

Test utama: `tests/test_registered_spatial.py`, `tests/test_spatial_edgeweight.py`.
[Hasil eksperimen](../../../docs/experiments/model-comparison-ds-v0.3.0/README.md) dan
[laporan TI-AI-05](../../../docs/experiments/TI-AI-05/README.md) membedakan hasil historis dan verifikasi integrasi.
Replay historis bukan bukti feed operasional terbaru; Worker pada waktu sekarang tetap memerlukan
pembaruan data sumber. Deployment Railway terpisah dari verifikasi lokal.

## Runtime terverifikasi

LightGBM dipatok 4.6.0: versi 4.7.0 gagal pada native label construction Windows untuk dataset ini,
sementara 4.6.0 berhasil. Versi pustaka per model dicatat dalam bundle. Pada environment Conda Windows
`base2` di mesin pengembangan, PyTorch perlu dimuat sebelum pustaka native lain; `scripts/verify_spatial_models.py`
melakukannya. Gunakan environment ML terpisah untuk menghindari konflik DLL/OpenMP.

Perintah verifikasi lengkap dari root, setelah environment menunjuk stack lokal terisolasi:

```bash
python scripts/verify_spatial_models.py --pm25 .cache/training/pm25 --no2 .cache/training/no2
```

`TEST_DATABASE_URL` wajib menunjuk database lokal `spatial_test`. Script menolak host/database lain.
Laporan ringkas disimpan pada `docs/experiments/TI-AI-05/registered_verification.json`.
