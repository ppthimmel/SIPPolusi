# Spesifikasi fitur Open-Meteo

Tabel utama: `src/data_worker/dataset_processed/open_meteo/hourly.parquet`. Setiap baris mewakili **satu jam UTC untuk satu titik Jakarta**. Periode: 1 Januari–30 September 2026, sebanyak 6.552 baris. Tujuh fitur ini adalah masukan meteorologi (`Xmet`) model konsentrasi polutan.

| Kolom | Arti | Satuan |
|---|---|---|
| `temperature_2m` | Suhu udara pada ketinggian 2 m | °C |
| `relative_humidity_2m` | Kelembapan relatif pada 2 m | % |
| `wind_speed_10m` | Kecepatan angin pada 10 m | km/jam |
| `wind_direction_10m` | Arah asal angin pada 10 m | derajat |
| `boundary_layer_height` | Tinggi lapisan batas atmosfer | m |
| `precipitation` | Curah hujan untuk interval jam sumber | mm |
| `surface_pressure` | Tekanan udara permukaan | hPa |

`time_utc` adalah waktu kondisi cuaca (valid time); waktu terbit run tidak tercatat. **Aturan yang berlaku di FeatureMatrix: nilai valid time t dianggap tersedia pada `floor(t, 6 jam) + 8 jam`** (`WEATHER_DELAY_HOURS` di `feature_matrix/sources.py`, opsi `--weather-delay-hours`). Ini asumsi konservatif, bukan waktu publikasi yang terverifikasi. Setiap fitur memiliki kolom `<fitur>_available` untuk menandai nilai valid. Nilai kosong tetap `NaN`; saat ini ketujuh variabel tidak mempunyai nilai kosong. Kolom lama `feature_available_at` dalam Parquet sumber masih kosong dan tidak dipakai dalam penggabungan; tidak dibuat timestamp produksi cuaca.

**Pengolahan:** mengambil tujuh variabel dari 184 variabel di `datasets/2026/jakarta/open-meteo weather/hourly.json` (di luar repo; hash tercatat di manifest), menyusun tabel per jam, dan menambahkan mask ketersediaan. Satuan dipertahankan; tidak ada agregasi, interpolasi, atau imputasi. Arah angin bersifat melingkar, sehingga transformasi menjadi komponen angin dapat dipertimbangkan saat menyiapkan model.

**Asal:** dataset ini dibangun dari Historical Forecast API, model `best_match`, titik permintaan −6,2° lintang dan 106,85° bujur. Ini arsip keluaran model cuaca; datanya tidak mewakili pengukuran independen pada setiap sel Jakarta. Rincian permintaan, satuan, jumlah kosong, dan hash sumber ada di `src/data_worker/dataset_processed/open_meteo/manifest.json`. Kode pengolahan batch tidak ada di repo. Pada operasi, `src/data_worker/connectors/open_meteo.py` memakai Forecast API (`/v1/forecast`, dengan `past_days` dan `forecast_days`).

**Penggabungan:** FeatureMatrix (`src/data_worker/feature_matrix/`) memilih valid time terbaru yang sudah tersedia pada waktu inferensi τ. Nilai satu titik dipakai bersama pada semua sel; tidak menjadi pengukuran cuaca 100 m. `weather_time_utc`, `weather_available_at_utc`, dan `weather_age_hours` mencatat jam sumber yang dipakai.

**Batas penggunaan:** masukan meteorologi, bukan target polutan. Asumsi waktu tersedia tidak membuktikan ketersediaan operasional saat itu. Normalisasi dan imputasi dipelajari hanya dari data pelatihan.

## Lihat juga

- [`src/data_worker/feature_matrix/README.md`](../feature_matrix/README.md): aturan waktu tersedia cuaca yang berlaku (menggantikan asumsi di atas)
- [`src/data_worker/feature_store/README.md`](../feature_store/README.md): konektor dan tabel feature store
