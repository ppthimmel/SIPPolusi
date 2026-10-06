# TI-AI-04: Baseline IDW Spatial Downscaling Model

Bukti verifikasi isu #15. Kode dan protokol lengkap:
[`src/data_worker/spatial_model/baseline`](../../../src/data_worker/spatial_model/baseline/README.md).

| Berkas | Isi |
|---|---|
| [`dataset/manifest.json`](dataset/manifest.json) | Manifest dataset `sh-a7e5db9bb33f`: sumber, batas kueri, kendali mutu, split beku, checksum |
| `dataset/station_hour.parquet` | Ground truth stasiun-jam beku (63.110 baris) |
| [`runs/20261006T093030Z-idw-sh-a7e5db9bb33f-f6f15b8f/`](runs/20261006T093030Z-idw-sh-a7e5db9bb33f-f6f15b8f/report.md) | Run resmi: `report.md`, `metrics.json`, `run_manifest.json`, `config.toml`, `tables/`, `figures/` |

## Dataset dan protokol

- **Sumber:** skema `ground_truth` PostgreSQL Railway (dibaca read-only pada 6 Oktober 2026
  pukul 07.53Z), PM2.5 dan NO2 sebelum 2026-10-06T00:00Z. Ekspor Parquet independen pukul
  06.22Z dengan batas yang sama menghasilkan hash isi yang identik.
- **Kendali mutu:** `qc` kosong, nilai > 0, tanpa sentinel 999,99, tanpa DKI_PM25_40 dan
  PM2.5 DKI_PM25_33 sejak 19 September, hanya di dalam `JAKARTA_BBOX`. Enam sensor
  `DKI_PM25_*` dengan rerata pelatihan < 10 µg/m³ ditandai dugaan bias rendah, tidak dibuang.
- **Split temporal provisional (TI-AI-03 v1):** pelatihan 13–28 September, validasi
  29 September–1 Oktober, pengujian 2–5 Oktober 2026 (96 time window).
- **Model:** IDW power = 2, neighbors = 8, jarak meter pada EPSG:32748 (Tabel 3.8), dievaluasi
  *leave-one-station-out* per time window satu jam pada split pengujian.

## Hasil (split pengujian)

| Polutan | n stasiun-jam | Stasiun | MAE (µg/m³) | RMSE (µg/m³) | R² | Bias (µg/m³) | Rerata ground truth (µg/m³) |
|---|---|---|---|---|---|---|---|
| PM2.5 | 9.903 | 108 | 19,21 | 25,22 | −0,05 | −0,77 | 32,94 |
| NO2 | 1.185 | 13 | 34,07 | 43,07 | 0,12 | +16,37 | 50,89 |
| NO2, Reference saja | 672 | 7 | 34,36 | 44,91 | −0,49 | +8,23 | |

Konfigurasi terbaik pada validasi adalah power = 1 dengan seluruh stasiun untuk PM2.5
(RMSE uji 23,92 µg/m³, R² 0,05) dan power = 2 dengan seluruh stasiun untuk NO2 (RMSE uji
41,30 µg/m³, R² 0,19). Selisihnya terhadap konfigurasi Tabel 3.8 kecil (5% untuk PM2.5).

## Interpretasi

1. **IDW hampir tidak menjelaskan variasi antarstasiun.** R² PM2.5 mendekati nol berarti
   IDW tidak lebih baik daripada rerata seluruh ground truth. Galat didominasi selisih level
   yang menetap per stasiun: pada 66% stasiun PM2.5, |bias| lebih dari 0,8 × RMSE stasiun
   tersebut. Variasi lokal ini justru suku yang harus ditangkap model *spatial downscaling*
   dari fitur berskala mikro, sehingga baseline ini memberi batas bawah yang jelas untuk EAI-01.
2. **Batas ketelitian instrumen.** DKJ37 dan DKI98, dua stasiun Reference yang berjarak 1 m,
   berselisih rata-rata 52,7 µg/m³. Selama selisih antarinstrumen sebesar ini ada di ground
   truth, tidak ada model yang dapat mencapai RMSE jauh di bawahnya pada stasiun tersebut.
3. **Area dengan galat besar.** Tiga belas stasiun PM2.5 memiliki RMSE > 1,5 × agregat, lima di
   antaranya di Jakarta Utara.
   - Sepuluh stasiun diestimasi terlalu rendah (bias −29 sampai −66 µg/m³), antara lain Marunda,
     Tanjung Priok, dan Papanggo di kawasan pelabuhan, serta Amir Hamzah, Gedong, Kebun Jeruk,
     Fatmawati, Kota Tua, dan Bundaran HI. Dugaan penyebabnya adalah sumber lokal yang tidak
     terwakili stasiun sekitarnya; dugaan ini belum diverifikasi.
   - Tiga stasiun diestimasi terlalu tinggi: DKI98 (pasangan berimpit pada butir 2), serta
     DKI_PM25_23 dan DKI_PM25_71, yang termasuk kelompok dugaan bias rendah.
4. **Keadaan dengan galat besar.** Galat PM2.5 tertinggi pada pukul 03.00–08.00 WIB dan
   terendah pada 14.00–19.00 WIB; galat NO2 memuncak pada 16.00–19.00 WIB. Galat naik bila
   stasiun sumber terdekat berjarak 3–5 km (PM2.5 RMSE 38,4 µg/m³, NO2 72,4 µg/m³).
   Hari kerja dan akhir pekan tidak berbeda.
5. **Data pendukung terbatas.**
   - NO2 hanya memiliki 13 stasiun pada split pengujian dengan median jarak ke stasiun lain
     terdekat 5,7 km, sehingga estimasi NO2 di sebagian besar grid berasal dari stasiun yang jauh.
   - Membatasi NO2 pada stasiun Reference tidak memperbaiki hasil (R² −0,49). Penyebab galat
     terbesar adalah DKI5 Kebun Jeruk (rerata 12 µg/m³), yang tetap diestimasi +64 µg/m³ terlalu
     tinggi tanpa sensor LCS.
   - Kelompok dugaan bias rendah (576 stasiun-jam, rerata 6,7 µg/m³) diestimasi +29,5 µg/m³
     terlalu tinggi, konsisten dengan dugaan bias kalibrasi.
   - Pada contoh grid 100 m, median jarak sel ke stasiun terdekat 1,7 km dan maksimum 13,6 km
     (sel laut, karena masker daratan belum ada).

## Pemenuhan acceptance criteria

| Kriteria | Bukti |
|---|---|
| Alur pemrosesan dapat dijalankan ulang dari manifest yang sama | `python -m spatial_model.baseline run --manifest dataset/manifest.json`; isi dataset diverifikasi terhadap checksum; dua run menghasilkan SHA-256 prediksi yang sama (`3d20a1fa…`) |
| Metrik dan definisi unitnya untuk split yang tepat | Tabel di atas, `metrics.json` (`definitions`, `split = test`) |
| Konfigurasi, seed, dan parameter disimpan bersama hasil | `config.toml` dan `run_manifest.json` (seed 42, parameter, commit, versi pustaka, perangkat keras, durasi) |
| Area/keadaan dengan galat besar atau data terbatas | Bagian Interpretasi, temuan otomatis pada `report.md`, `tables/*.csv`, `figures/*.png` |

## Keterbatasan

- Split dan protokol ini provisional sampai TI-AI-03 disepakati tim. Periode pengujian hanya
  empat hari, sehingga metrik akan bergeser seiring bertambahnya data.
- Belum ada fitur berskala makro (GEOS-CF, MERRA-2, Sentinel-5P, MAIAC). Baseline hanya
  memakai ground truth sensor darat, sesuai definisi IDW pada subbab 4.4.1.
- *Leave-one-station-out* di pusat kota yang rapat stasiun dapat tampak lebih baik daripada
  kinerja pada area tanpa cakupan sensor.
- Satuan seluruh polutan diasumsikan µg/m³, dan label waktu portal memiliki ketidakpastian ±30 menit.
- **Basis data bukan sumber yang imutabel.** Snapshot ulang pukul 09.28Z dengan batas yang sama
  menghasilkan +6 baris (`sh-ffc8dbea5533`). Bendera `S` pada bacaan macet DKI_PM25_85 (34,0 µg/m³,
  4 Oktober 08.30–13.30Z) berubah menjadi bersih, karena bendera dihitung ulang dari riwayat 48 jam
  portal. Evaluasi ini memakai berkas beku `sh-a7e5db9bb33f`. Perilaku bendera tersebut perlu
  diperbaiki pada konektor SPKU.
