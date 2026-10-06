# Spesifikasi fitur Sentinel-5P

Sentinel-5P menyediakan sinyal NO2 berskala makro (`Xmacro`) untuk membantu Spatial Downscaling Model. Target konsentrasi polutan tetap berasal dari sensor darat. Kolom satelit ini belum ditetapkan sebagai background term konsentrasi permukaan atau masukan langsung model rute.

## Fitur yang dipakai

| Kolom | Arti / satuan | Asal dan transformasi | Keputusan |
|---|---|---|---|
| `no2_mol_m2` | Jumlah NO2 sepanjang kolom troposfer / mol/m² | `PRODUCT/nitrogendioxide_tropospheric_column`; seleksi QA → median harian per sel → median bulanan | Kandidat fitur; manfaatnya perlu diuji |
| `no2_precision_mol_m2` | Estimasi presisi retrieval / mol/m² | Presisi piksel sumber diringkas dengan median harian dan bulanan | Informasi kualitas; bukan galat model atau ketidakpastian komposit yang dihitung ulang |

**Sumber:** Sentinel-5P TROPOMI NO2 NRTI Level-2, diakses melalui Planetary Computer. Data tersedia dengan kebijakan akses bebas dan terbuka sesuai [ketentuan Copernicus Sentinel](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice). Pengamatan memiliki footprint orde beberapa kilometer, dengan lintasan sekitar harian; cakupan berkualitas tidak dijamin setiap hari. Hasil saat ini berupa grid **5 km, EPSG:32748**, diperbarui **bulanan**. Nilainya adalah kolom atmosfer, bukan konsentrasi dekat permukaan dalam µg/m³.

## Pengolahan dan kolom pendamping

Nilai kosong ditangani dari metadata sumber. QA dibuka skalanya (`nilai tersimpan × 0,01`), lalu hanya piksel dengan NO2 tersedia dan `qa_value ≥0,75` dipakai. Titik pusat piksel ditempatkan ke sel 5 km. Median dihitung per sel/hari, kemudian antarhari dalam bulan berdasarkan **waktu pengamatan UTC**. Nilai negatif yang lolos QA dipertahankan.

Ada dua bentuk Parquet:

- **Per pengamatan:** `observations.parquet`, satu granule × satu sel; `observed_start_at`–`observed_at` adalah rentang pengamatan dan `produced_at` proksi waktu tersedia. Gunakan untuk penyelarasan waktu.
- **Bulanan:** `monthly/YYYY-MM.parquet`, satu sel × satu bulan untuk ringkasan.

Kolom pendamping tabel bulanan:

| Kolom | Kegunaan |
|---|---|
| `grid_id`, `month`, koordinat | Identitas sel, periode, dan lokasi pusat sel |
| `available` | Menandai sel dengan minimal satu hari valid |
| `n_observation_days`, `n_valid_pixels` | Jumlah hari dan piksel valid yang masuk komposit |
| `qa_mean` | Ringkasan kualitas: rata-rata antarhari dari rata-rata QA piksel valid |
| `last_observed_day` | Tanggal pengamatan valid terakhir |
| `processing_version` | Versi pengolahan, saat ini `sentinel5p-no2-3` |
| `source_produced_at` | Produksi paling akhir sumber komposit; dipakai sebagai proksi ketersediaan setelah akhir jendela |

Nilai kosong dipertahankan sebagai `NaN` bersama mask `available`. Imputasi dan normalisasi untuk model harus dipelajari hanya dari data pelatihan.

## Asal berkas dan batas penggunaan

- Masukan: `datasets/2026/jakarta/sentinel5p/*.all.h5` dan metadata `.all.json`.
- Kode: `code/preprocess_sentinel5p.py`.
- Penyelarasan waktu: `datasets/processed/sentinel5p/observations.parquet`.
- Ringkasan bulanan: `datasets/processed/sentinel5p/monthly/YYYY-MM.parquet`.
- Audit piksel: `YYYY-MM.pixels.parquet`; jejak asal dan hash masukan tersedia di `manifest.json`, daftar granule di JSON bulanan.

**Risiko utama:** seleksi kualitas menyebabkan cakupan tidak merata; grid menggunakan titik pusat, bukan luas irisan footprint. Grid 5 km adalah ringkasan dan tidak menambah ketelitian pengukuran. Sebelum digabung dengan Landsat 100 m, penyelarasan spasial diperlukan; ID sel kedua sumber tidak bisa langsung dicocokkan.

**Aturan waktu:** `produced_at` dianggap waktu tersedia (asumsi penelitian). Gabungkan berdasarkan waktu pengamatan; untuk prediksi, filter produksi dan pengamatan sebelum waktu inferensi. Komposit bulanan juga harus menunggu akhir jendela.

Sentinel: `observations.parquet` menyimpan pengamatan dan produksi per granule/sel. Panduan: [SATELLITE_TIMES.md](SATELLITE_TIMES.md).

Penyimpanan gabungan dan pembacaan batch per jam: [SATELLITE_COMBINED.md](SATELLITE_COMBINED.md).
