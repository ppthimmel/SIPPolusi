# Spesifikasi fitur VIIRS

Tabel utama: `datasets/processed/viirs/daily.parquet`. Setiap baris mewakili **satu sel VIIRS × satu tanggal produk**. Sumbernya VNP46A2 Collection 2 (Black Marble), yaitu cahaya malam harian bergrid 15 detik busur, sekitar 500 m. Grid asli dipertahankan dengan koordinat EPSG:4326.

**Peran model:** kandidat proksi aktivitas (`Xactivity`). Cahaya malam menggambarkan pencahayaan dan pola aktivitas wilayah; bukan pengukuran lalu lintas, emisi, atau polusi per jam. Target polutan tetap berasal dari sensor darat. Penggunaan langsung sebagai background term atau komponen reward model rute belum ditetapkan.

## Kolom penting

| Kolom | Arti / kegunaan |
|---|---|
| `viirs_cell_id`, `observation_date_utc` | Identitas sel asli dan tanggal produk; kunci tabel |
| `longitude`, `latitude` | Koordinat sel dari sumber |
| `ntl` | Radiansi cahaya malam terkoreksi BRDF yang lolos QA, satuan nW·cm⁻²·sr⁻¹; fitur utama |
| `ntl_available` | Menandai nilai `ntl` tersedia |
| `ntl_gap_filled`, `ntl_gap_filled_available` | Radiansi hasil pengisian celah oleh NASA dan mask-nya; disimpan terpisah, tidak menggantikan `ntl` |
| `latest_high_quality_age_days` | Umur retrieval berkualitas yang dicatat produk, dalam hari; membantu memeriksa kesegaran data pengisian celah |
| `mandatory_quality_flag`, `cloud_mask`, `snow_flag` | Penanda kualitas sumber untuk audit |
| `lunar_irradiance` | Iradiansi bulan, nW·cm⁻²; informasi pendukung, bukan fitur aktivitas utama |
| `observed_start_at`, `observed_end_at` | Rentang tanggal produk harian; bukan waktu lintasan tepat |
| `produced_at` | Waktu produksi versi sumber, terpisah dari waktu publikasi |

## Pengolahan dan cakupan

Nilai kosong dan faktor skala dibaca dari metadata. Untuk Collection 2 ini radiansi berskala **1**, bukan otomatis dikalikan 0,1 seperti beberapa versi lama. Fitur utama menerima radiansi nonnegatif dengan quality flag `0`, kondisi malam, mask awan berkualitas menengah/tinggi, confidently clear, serta tanpa bayangan, cirrus, atau salju. Nilai yang ditolak menjadi `NaN`; tidak dilakukan interpolasi atau imputasi lokal.

Hasil berisi **2.239.488 baris**, dari 243 tanggal produk antara 1 Januari–20 September 2026. Terdapat **695.831 nilai `ntl` valid** pada 141 tanggal. Dalam periode Januari–September, 30 tanggal tidak mempunyai berkas produk; daftar tanggalnya dicatat pada manifest. Jumlah piksel valid berbeda dari jumlah tanggal valid.

## Asal dan penggunaan

- Masukan: `datasets/2026/jakarta/viirs/*.all.h5` dan `.all.json`.
- Kode: `code/preprocess_viirs.py`.
- Konfigurasi, hash sumber, dan cakupan: `datasets/processed/viirs/manifest.json`.
- Acuan kualitas: [NASA LAADS VNP46A2 Collection 2](https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/VNP46A2).

**Untuk TCN:** sel VIIRS perlu dipasangkan secara spasial ke node model. Pengamatan harian dapat dipertahankan pada jam berikutnya setelah diketahui tersedia, disertai mask dan umur data. Tanggal produk bukan waktu lintasan tepat atau waktu publikasi, sehingga penggabungan per jam belum dilakukan. Hasil pengisian celah NASA tidak boleh dianggap sebagai pengamatan baru pada hari tersebut.

**Risiko:** cakupan pengamatan berkualitas tidak merata, kotak ekstrak bukan batas administratif DKI, dan cahaya malam belum terbukti menjelaskan perubahan aktivitas per jam. Normalisasi dan imputasi selanjutnya hanya dipelajari dari data pelatihan.

**Asumsi waktu:** `produced_at` dipakai sebagai waktu tersedia. Gabungkan berdasarkan pengamatan dan filter `produced_at <= waktu inferensi`. Panduan: [SATELLITE_TIMES.md](SATELLITE_TIMES.md).

Penyimpanan gabungan dan pembacaan batch per jam: [SATELLITE_COMBINED.md](SATELLITE_COMBINED.md).
