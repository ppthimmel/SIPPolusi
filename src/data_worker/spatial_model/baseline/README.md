# Baseline IDW Spatial Downscaling Model (TI-AI-04)

Alur pemrosesan baseline *spatial downscaling* berbasis interpolasi Inverse
Distance Weighting (IDW) sesuai Dokumen Desain subbab 4.4.1 dan Tabel 3.8.
Isinya: pembentukan dataset stasiun-jam yang dibekukan beserta manifest,
evaluasi *leave-one-station-out*, analisis galat prediksi, dan inferensi
grid 100 m. Hasil run resmi ada di
[`docs/experiments/TI-AI-04`](../../../../docs/experiments/TI-AI-04).

## Menjalankan

Dari `src/data_worker`:

```bash
# 1. Bekukan snapshot dataset dan split (sekali). Sesi basis data read-only;
#    DSN dibaca dari DATABASE_URL dan tidak pernah dicetak atau dicatat.
python -m spatial_model.baseline build-dataset --from-db --out ../../docs/experiments/TI-AI-04/dataset

# 2. Jalankan baseline dari manifest yang sama, kapan pun dan berapa kali pun.
python -m spatial_model.baseline run \
    --manifest ../../docs/experiments/TI-AI-04/dataset/manifest.json \
    --out ../../docs/experiments/TI-AI-04/runs
```

`build-dataset --export-dir <dir>` membaca ekspor Parquet skema
`ground_truth` sebagai pengganti basis data. Ekspor pukul 06.22Z dan basis
data pukul 07.53Z pada 6 Oktober menghasilkan hash isi yang sama
(`sh-a7e5db9bb33f`).

**Yang dibekukan adalah berkas, bukan kueri.** Membaca ulang basis data
dengan batas waktu yang sama belum tentu memberi hasil yang sama. Pada pukul
09.28Z di hari yang sama, hasilnya `sh-ffc8dbea5533` (+6 baris), karena
bendera `S` (sensor macet) enam bacaan DKI_PM25_85 bernilai 34,0 berubah
menjadi bersih. Bendera ini dihitung ulang dari riwayat 48 jam di portal,
sehingga ujung deret macet kehilangan benderanya ketika awal deret keluar
dari jendela tersebut. Evaluasi selalu dijalankan dari `station_hour.parquet`
dan manifest yang di-commit; untuk snapshot baru, pilih batas waktu yang
sudah lewat lebih dari 48 jam.
`--split-from <manifest lama>` memakai ulang batas split yang sudah
dibekukan ketika snapshot diperbarui.

## Susunan

| Berkas | Isi |
|---|---|
| `../grid.py` | `GridSpec`: grid reguler 100 m pada EPSG:32748 |
| `../idw.py` | `idw_interpolate()` dan `run_idw_fallback()`; satu implementasi untuk baseline dan fallback |
| `configs/idw_baseline.toml` | Seluruh parameter: kendali mutu, split, IDW, sensitivitas, analisis |
| `dataset.py` | Snapshot, kendali mutu, agregasi per time window, split temporal, manifest dan verifikasi hash |
| `evaluate.py` | Prediksi *leave-one-station-out*, metrik, sensitivitas power × neighbors |
| `analysis.py` | Galat menurut stasiun, wilayah, waktu, dan ketersediaan data pendukung; temuan otomatis |
| `report.py` | Visualisasi galat dan `report.md` |
| `run.py` | Satu run: menulis konfigurasi, manifest run, metrik, tabel, gambar |

## Protokol

**Ground truth.** Pengukuran PM2.5 dan NO2 sensor darat SPKU pada skema
`ground_truth`, sampai batas `snapshot_end_utc` (eksklusif). Satuan
diasumsikan µg/m³ (lihat `docs/DATA.md`).

**Kendali mutu** (urutan penerapan, jumlah baris terbuang per aturan
tercatat pada manifest):

1. stasiun di luar `JAKARTA_BBOX` (Kepulauan Seribu);
2. `qc` tidak kosong (`Z`, `N`, `R`, `S`);
3. nilai ≤ 0, karena portal memakai 0 sebagai penanda kosong;
4. nilai ≥ 999,99 (kode sentinel Kelapa Gading yang lolos bendera `qc`);
5. nilai > 2000 µg/m³;
6. DKI_PM25_40 seluruhnya (macet 32,00) dan PM2.5 DKI_PM25_33 sejak
   2026-09-19T10:00Z (macet 14,00).

Stasiun `DKI_PM25_*` bertipe Sensor dengan rerata PM2.5 < 10 µg/m³ pada
split pelatihan ditandai `suspected_low_bias`, tidak dibuang, dan
dianalisis sebagai kelompok tersendiri. Rerata dihitung pada split
pelatihan saja agar penandaan tidak memakai data validasi/pengujian.

**Time window.** Rata-rata bacaan bersih pada [HH:00, HH+1:00) UTC,
berlabel awal jam, minimal satu bacaan.

**Split (provisional TI-AI-03 v1).** Blok temporal pada hari UTC penuh,
70/15/15: pelatihan 13–28 September, validasi 29 September–1 Oktober,
pengujian 2–5 Oktober 2026. Batas dibekukan di manifest dan diperiksa
ulang setiap kali dataset dimuat. Split ini harus disepakati tim sebelum
dipakai sebagai protokol final.

**Evaluasi.** Untuk setiap polutan, time window, dan stasiun uji, estimasi
pada koordinat stasiun dihitung dari stasiun lain pada time window yang
sama (*leave-one-station-out*); pengukuran stasiun uji tidak pernah dipakai.
IDW tidak memiliki parameter terlatih, sehingga:

- split pelatihan tidak dipakai;
- split validasi dipakai untuk analisis sensitivitas power ∈ {0, 1, 2, 3}
  × neighbors ∈ {4, 8, 16, semua};
- split pengujian dilaporkan dengan konfigurasi Tabel 3.8 (power = 2,
  neighbors = 8, jarak Euclides dalam meter pada EPSG:32748) agar sama
  dengan fallback; konfigurasi terbaik validasi dilaporkan sebagai
  pembanding.

Varian `no2_reference_only` membatasi stasiun sumber dan uji NO2 pada tipe
Reference, karena tiga sensor LCS berlevel 125–148 µg/m³ terhadap
13–110 µg/m³ pada stasiun Reference.

**Metrik** (µg/m³ kecuali R²): MAE, RMSE, bias (ŷ − y), R² = 1 − SSres/SStot,
dan n stasiun-jam. Dilaporkan *pooled* atas seluruh stasiun-jam dan sebagai
median per stasiun, per polutan.

**Analisis galat.** Per stasiun (peta), kota administrasi, tipe stasiun,
dugaan bias rendah, jam WIB, hari kerja/akhir pekan, tanggal, jarak ke
stasiun sumber terdekat, jumlah stasiun sumber, dan porsi stasiun Reference
di antara sumber. Kelompok dengan RMSE > 1,5 × agregat ditandai galat
besar; n < 24 ditandai data terbatas. Pasangan stasiun berjarak < 100 m
dilaporkan sebagai berimpit.

## Reprodusibilitas

- `dataset/manifest.json`: sumber dan batas kueri, aturan kendali mutu,
  jumlah baris terbuang, batas split, ringkasan per split, SHA-256 berkas
  dan SHA-256 isi (tidak bergantung versi pyarrow).
- Setiap run menyimpan `config.toml`, salinan manifest dataset, dan
  `run_manifest.json` berisi seed 42, parameter, SHA-256 konfigurasi, commit
  git, versi pustaka, perangkat keras, durasi, serta SHA-256 prediksi.
  Dua run dari manifest dan konfigurasi yang sama menghasilkan SHA-256
  prediksi yang sama (diuji pada `tests/test_spatial_baseline.py`).
- `run` menolak berjalan bila isi dataset tidak cocok dengan manifest.
- Pencatatan ke MLflow belum dilakukan; isi direktori run sudah memuat
  semua yang dibutuhkan dan dapat dicatat ke Model Registry pada TI-AI-11.

## Perbedaan dengan Dokumen Desain

| Butir | Dokumen Desain | Implementasi | Alasan |
|---|---|---|---|
| Metrik baseline | R² dan RMSE (4.3.2, EAI-01), MAE dan RMSE (4.4.1) | MAE, RMSE, R², bias | Gabungan keduanya |
| Fitur makro | Prediksi dari sumber makro (isu TI-AI-04) | Hanya ground truth | Konektor makro belum ada; IDW per 4.4.1 hanya memakai sensor darat |
| Split | Dari TI-AI-03 | Provisional v1 dibuat di sini | TI-AI-03 belum ada |
| Masker daratan | Sel grid wilayah studi (±66 ribu sel) | Seluruh bbox (≈ 110 ribu sel) | Batas daratan belum tersedia |
| Integrasi siklus | `run_idw_fallback` saat ST-GNN gagal | Fungsi tersedia, belum dipanggil `acquisition.py` | Penulisan `pollution.*` dan agregasi ke ruas adalah lingkup TI-AI-05 |
