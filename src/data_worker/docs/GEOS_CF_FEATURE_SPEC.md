# Spesifikasi fitur GEOS-CF

Sumber: **GEOS-CF v2 analysis**, `aqc_tavg_1hr_glo_L1440x721_slv`, NASA NCCS. Tabel utama `processed/geos-cf/geoscf_cell_hourly.parquet`: **9 sel sumber × 6.552 jam UTC**, Januari–September 2026. Grid asli 0,25° (sekitar 25 km); `grid100m_geoscf_lookup.parquet` memetakan ke grid target 100 m melalui koordinat UTM EPSG:32748.

**Peran:** estimasi model sebagai kandidat suku latar (*background term*) untuk model konsentrasi polutan. Bukan target sensor darat. NO₂ permukaan ini berbeda dari kolom atmosfer Sentinel-5P. Jangan menganggap pemetaan ke 100 m menghasilkan pengukuran baru 100 m.

| Kolom sumber → atribut gabungan | Arti / satuan |
|---|---|
| `pm25_ugm3` → `geoscf_pm25_ugm3` | PM2.5 pada RH 35%, termasuk air / µg/m³ |
| `no2_ppb` → `geoscf_no2_ppb` | Fraksi mol NO₂ sumber × 10⁹ / ppb |
| `no2_ugm3` → `geoscf_no2_ugm3` | Konversi NO₂ memakai 298,15 K dan 101.325 Pa / µg/m³ |
| `available_pm25`, `available_no2` | Mask nilai valid masing-masing variabel |
| `time_window_start` | Awal interval kondisi rata-rata satu jam; timestamp sumber menit ke-30 digeser −30 menit menurut manifest |
| `available_at_utc` | Header server `Last-Modified`, dipakai sebagai **proksi ketersediaan** |
| `latency_hours` | Proksi tersedia − akhir interval kondisi, dalam jam |

NO₂ dalam ppb dan µg/m³ adalah dua representasi variabel yang sama, bukan dua prediktor independen. Konversi acuan dipertahankan; belum memakai suhu/tekanan Open-Meteo. Tidak dilakukan imputasi nilai sumber.

**Penggabungan:** pilih interval valid paling baru yang sudah selesai dan `available_at_utc <= time_utc`. Nilai dibawa maju; pengamatan lama yang terlambat datang tidak mengganti kondisi lebih baru. PM2.5 dan NO₂ dipilih independen berdasarkan mask. Metadata gabungan memakai awalan `geoscf_pm25_` / `geoscf_no2_`: `time_window_start`, `time_window_end`, `available_at_utc`, `latency_hours`, `age_hours`. Umur dihitung dari akhir interval. Sebelum ada sumber yang memenuhi syarat, nilai kosong dan mask `False`; batas umur opsional melalui `GEOS_CF_MAX_AGE_HOURS` di notebook.

**Asal dan risiko:** manifest mencatat produk, hash, QC, dan konversi. `Last-Modified` bukan bukti publikasi pertama atau waktu produksi; tidak dibuat `produced_at`. CSV bukti ketersediaan dan JSON mentah yang disebut manifest belum tersedia dalam folder proyek, sehingga provenance belum bisa diperiksa sepenuhnya. Akses melalui portal publik NASA; ketentuan lisensi spesifik belum dicatat dalam metadata lokal.
