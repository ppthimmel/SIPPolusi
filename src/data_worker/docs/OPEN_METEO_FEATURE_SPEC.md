# Spesifikasi fitur Open-Meteo

Tabel utama: `datasets/processed/open_meteo/hourly.parquet`. Setiap baris mewakili **satu jam UTC untuk satu titik Jakarta**. Periode: 1 Januari–30 September 2026, sebanyak 6.552 baris. Tujuh fitur ini adalah kandidat meteorologi (`Xmet`) untuk model konsentrasi polutan.

| Kolom | Arti | Satuan |
|---|---|---|
| `temperature_2m` | Suhu udara pada ketinggian 2 m | °C |
| `relative_humidity_2m` | Kelembapan relatif pada 2 m | % |
| `wind_speed_10m` | Kecepatan angin pada 10 m | km/jam |
| `wind_direction_10m` | Arah asal angin pada 10 m | derajat |
| `boundary_layer_height` | Tinggi lapisan batas atmosfer | m |
| `precipitation` | Curah hujan untuk interval jam sumber | mm |
| `surface_pressure` | Tekanan udara permukaan | hPa |

`time_utc` adalah waktu kondisi cuaca. **Asumsi penelitian: data dianggap sudah tersedia pada `time_utc`**, bukan waktu publikasi yang terverifikasi. Setiap fitur memiliki kolom `<fitur>_available` untuk menandai nilai valid. Nilai kosong tetap `NaN`; saat ini ketujuh variabel tidak mempunyai nilai kosong. Kolom lama `feature_available_at` dalam Parquet sumber masih kosong dan tidak dipakai dalam penggabungan; tidak dibuat timestamp produksi cuaca.

**Pengolahan:** mengambil tujuh variabel dari 184 variabel di `datasets/2026/jakarta/open-meteo weather/hourly.json`, menyusun tabel per jam, dan menambahkan mask ketersediaan. Satuan dipertahankan; tidak ada agregasi, interpolasi, atau imputasi. Arah angin bersifat melingkar, sehingga transformasi menjadi komponen angin dapat dipertimbangkan saat menyiapkan model.

**Asal:** Historical Forecast API, model `best_match`, titik permintaan −6,2° lintang dan 106,85° bujur. Ini arsip keluaran model cuaca; datanya tidak mewakili pengukuran independen pada setiap sel Jakarta. Rincian permintaan, satuan, jumlah kosong, dan hash sumber ada di `datasets/processed/open_meteo/manifest.json`. Kode pengolahan: `code/preprocess_open_meteo.py`.

**Penggabungan:** notebook `datasets/datasets_preprocessing.ipynb` mencocokkan tepat jam UTC dengan batch satelit. Nilai satu titik dipakai bersama pada sel-sel yang diminta; tidak menjadi pengukuran cuaca 100 m. Jam tanpa pasangan tetap NaN; Boolean ketersediaan tidak disertakan dalam tabel gabungan; `weather_time_utc` mencatat jam sumber yang cocok. Hanya batch pilihan diekspor, bukan seluruh grid × periode.

**Batas penggunaan:** masukan meteorologi, bukan target polutan. Asumsi waktu tersedia tidak membuktikan ketersediaan operasional saat itu. Normalisasi dan imputasi dipelajari hanya dari data pelatihan. Contoh terbaru lima sumber: `processed/combined/examples/five_sources_hourly.parquet`; aturan lengkap di [SATELLITE_COMBINED.md](SATELLITE_COMBINED.md).
