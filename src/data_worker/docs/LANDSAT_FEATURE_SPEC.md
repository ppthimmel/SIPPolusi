# Spesifikasi fitur Landsat

Landsat digunakan sebagai fitur lingkungan (`Xland`) untuk membantu model mengestimasi konsentrasi PM2.5 dan NO2. Data ini berperan sebagai **masukan model**; target/data rujukan berasal dari sensor darat. Landsat tidak digunakan sebagai background term. Penggunaan langsung untuk model rute belum ditetapkan.

## Fitur yang dipakai

| Kolom | Arti / satuan | Asal dan transformasi | Keputusan |
|---|---|---|---|
| `ndvi` | Indeks vegetasi / tanpa satuan | SR_B5 dan SR_B4; `(NIR − merah)/(NIR + merah)` | Kandidat fitur |
| `ndbi` | Proksi lahan terbangun / tanpa satuan | SR_B6 dan SR_B5; `(SWIR1 − NIR)/(SWIR1 + NIR)` | Kandidat fitur |
| `lst_c` | Suhu permukaan / °C | ST_B10; suhu kelvin dikurangi 273,15 | Kandidat bersyarat karena cakupan jarang |

**Sumber:** Landsat 8/9 Collection 2 Level-2, diakses melalui Planetary Computer. Data domain publik menurut [USGS](https://www.usgs.gov/faqs/are-there-any-restrictions-use-or-redistribution-landsat-data). Grid optik sumber 30 m; informasi termal sekitar 100 m. Hasil diselaraskan ke grid **100 m, EPSG:32748**, dan diperbarui **bulanan**. Lintasan nominal sekitar 8 hari gabungan, tetapi awan dapat menyebabkan data kosong.

## Pengolahan dan kolom pendamping

Piksel yang gagal kualitas dibuang, nilai digital diubah memakai skala Level-2, lalu fitur dihitung. Hasil dirata-ratakan ke sel 100 m dengan minimal 50% luas valid. Komposit bulanan menggunakan median antarhari berdasarkan **tanggal pengamatan UTC**. LST memakai pemeriksaan tambahan: ketidakpastian ≤2 K, jarak awan ≥1 km, suhu −10 hingga 80 °C.

Ada dua bentuk Parquet:

- **Per scene:** `scenes/<scene_id>.parquet`, satu sel × satu pengamatan. Gunakan untuk penyelarasan waktu; `observed_at` adalah waktu pengamatan dan `produced_at` proksi waktu tersedia.
- **Bulanan:** `monthly/YYYY-MM.parquet`, satu sel × satu bulan. Median hari-hari valid untuk ringkasan lingkungan; bukan data harian atau per jam.

Kolom pendamping tabel bulanan:

| Kolom | Kegunaan |
|---|---|
| `grid_id`, `composite_month` | Kunci sel dan bulan untuk penggabungan pada grid yang sama |
| Koordinat | Lokasi pusat sel |
| `<fitur>_available` | Menandai nilai tersedia atau kosong |
| `<fitur>_n_observations`, `_valid_fraction`, `_age_days` | Jumlah hari valid, cakupan luas valid, dan umur pengamatan |
| `processing_version` | Menelusuri run pengolahan |
| `source_produced_at` | Produksi paling akhir sumber komposit; dipakai sebagai proksi ketersediaan setelah akhir jendela |

Nilai kosong dipertahankan sebagai `NaN`. Jika model membutuhkan imputasi/normalisasi, parameternya dipelajari hanya dari data pelatihan.

## Asal berkas dan batas penggunaan

- Masukan: `datasets/2026/jakarta/landsat/<scene_id>/`.
- Kode: `code/preprocess_landsat.py`.
- Penyelarasan waktu: `datasets/processed/landsat/scenes/<scene_id>.parquet`.
- Ringkasan bulanan: `datasets/processed/landsat/monthly/YYYY-MM.parquet`.
- Jejak asal: `manifest.json` mencatat konfigurasi dan hash masukan; JSON bulanan mencatat scene sumber.

**Risiko utama:** cakupan tidak merata, LST sangat jarang, dan kotak studi bukan batas administratif DKI. Manfaat ketiga fitur masih perlu diuji.

**Aturan waktu:** `produced_at` dianggap waktu tersedia (asumsi penelitian). Gabungkan berdasarkan waktu pengamatan; untuk prediksi, filter produksi dan pengamatan sebelum waktu inferensi. Komposit bulanan juga harus menunggu akhir jendela.

Landsat: tabel per scene menyimpan `observed_at` dan `produced_at`. Panduan: [SATELLITE_TIMES.md](SATELLITE_TIMES.md).

Penyimpanan gabungan dan pembacaan batch per jam: [SATELLITE_COMBINED.md](SATELLITE_COMBINED.md).
