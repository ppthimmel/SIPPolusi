# Snapshot dataset terversi (TI-AI-03)

Membekukan dataset yang dapat direproduksi untuk pelatihan dan evaluasi Spatial Downscaling Model (isu #14).
Masukannya adalah ekspor Parquet skema `ground_truth` dan `dataset_processed`. Keluarannya adalah folder snapshot
immutable dengan label bersih, split, fold *leave-one-station-out*, FeatureMatrix ST-GNN (TI-AI-02), laporan
kebocoran, manifest, dan checksum. Snapshot resmi: [`docs/experiments/ds-v0.1.0`](../../../../docs/experiments/ds-v0.1.0/README.md);
protokol evaluasi: [`docs/PROTOKOL_EVALUASI.md`](../../../../docs/PROTOKOL_EVALUASI.md).

## Menjalankan

Dari `src/data_worker`:

```bash
python -m spatial_model.snapshot build --ground-truth <ekspor ground_truth> --out <folder baru>
python -m spatial_model.snapshot verify <folder snapshot>
python -m spatial_model.baseline run --manifest <folder snapshot>/manifest.json --out <folder run>
```

- `build` menolak folder yang tidak kosong, sehingga snapshot tidak pernah ditimpa.
- Dua pembangunan dari commit dan masukan yang sama menghasilkan `CHECKSUMS.sha256` identik, karena tidak ada
  cap waktu pembangunan di dalam berkas.
- Seluruh aturan ada di `configs/<versi>.toml`; aturan baru berarti versi baru.

## Susunan

| Berkas | Isi |
|---|---|
| `configs/ds-v0.1.0.toml` | Rentang, aturan eksklusi, grup, split stasiun, blok temporal, protokol per target |
| `build.py` | Label stasiun-jam, stasiun dan grup, FeatureMatrix, split, normalisasi, manifest, checksum |
| `split.py` | Grup stasiun, split berstrata sistematis, blok temporal, fold LOSO, jarak sensor per fold |
| `leakage.py` | Delapan pemeriksaan kebocoran |
| `docs.py` | `exclusion_rules.md`, `split_stats.md`, `README.md` snapshot |

Aturan pembacaan valid ada di `feature_matrix.labels.LabelRules`. Snapshot memakai aturan bundle ST-GNN, ditambah
deret macet yang dihitung ulang pada deret penuh dan jam tidak lengkap.

## Keputusan ds-v0.1.0

- **Label:**
  - Sumbernya ekspor PostgreSQL `sippolusi_ground_truth_20261006` (cap waktu sudah dikoreksi −7 jam).
  - Label adalah rata-rata pembacaan valid per time window satu jam, berlabel awal jam.
  - Tidak ada nilai interpolasi.
- **Grup stasiun:**
  - Stasiun pada sel 100 m yang sama atau berjarak < 300 m (transitif), atau duplikat penyedia, masuk satu grup.
  - Grup adalah satuan split dan satuan fold.
- **Split stasiun 70/15/15 per grup:**
  - Grup diurutkan per strata (tipe stasiun, kota administrasi) dan diacak di dalam strata dengan seed 42.
  - Label train/val/test diberikan menurut pola posisi sistematis.
- **Blok temporal:**
  - train 13–23 Sep, val 25–27 Sep, test 29 Sep 00.00 – 30 Sep 22.00 UTC;
  - jeda 25 jam ≥ max(L = 24, 24 jam).
- **PM2.5:** split tetap blind ganda (stasiun split s × blok s), ditambah LOSO per grup sebagai protokol utama.
- **NO2:** hanya 14 stasiun, sehingga dievaluasi dengan LOSO per grup penuh tanpa stasiun validasi terpisah.
- **Fitur:**
  - `dist_nearest_sensor_m` hanya dari stasiun train; per fold LOSO tersedia di
    `splits/loso_sensor_distance.parquet`.
  - Landsat dan VIIRS diambil *as-of* waktu inferensi; tidak ada komposit statis yang di-fit.
  - GEOS-CF memakai `available_at_utc <= waktu inferensi`. Cuaca Open-Meteo memakai valid time terbaru dengan waktu
    tersedia `floor(t, 6 jam) + 8 jam <= waktu inferensi`.
- **Satuan target:** µg/m³, terverifikasi terhadap ISPU portal (`scripts/check_ispu_units.py`).
- **Normalisasi:**
  - Statistik fitur di-fit pada jam masukan blok train di node grup train.
  - Target PM2.5 dan rasio GEOS-CF/SPKU dari split train.
  - Target NO2 di-fit per fold LOSO.
