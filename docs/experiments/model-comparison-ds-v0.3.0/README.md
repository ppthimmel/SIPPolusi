# Perbandingan model pada ds-v0.3.0 (PM2.5 dan NO2)

Perbandingan seluruh kandidat Spatial Downscaling Model pada snapshot [`ds-v0.3.0`](../ds-v0.3.0/README.md)
mengikuti [`PROTOKOL_EVALUASI.md`](../../PROTOKOL_EVALUASI.md). Seluruh angka dalam µg/m³. Tabel lengkap ada di
[`results/summary.md`](results/summary.md).

## Ringkasan

- **PM2.5: tidak ada model yang mengalahkan interpolator yang disetel.**
  - Konfigurasi terpilih pada blok val adalah **IDW p = 1 dengan seluruh stasiun: RMSE LOSO test 22,18**.
  - Baseline IDW p = 2, k = 8 mendapat 24,00 (selisih −1,82; IK 95% −3,05 s.d. −0,63).
  - Kriging, kernel Gauss, ridge, MLP, dan ensemble setara secara statistik dengan konfigurasi terpilih.
  - LightGBM, XGBoost, random forest, dan extra trees sedikit tetapi signifikan lebih buruk.
- **NO2: LightGBM dengan seluruh fitur unggul jelas.**
  - Terpilih pada blok val adalah LightGBM seluruh fitur yang memprediksi konsentrasi langsung: RMSE LOSO
    test **27,43** vs 46,99 untuk baseline (IK 95% −29,74 s.d. −10,40), 13 dari 13 stasiun membaik.
  - Model ini setara dengan LightGBM seluruh fitur yang mengoreksi IDW p = 2, k = 8 atau rerata sumber
    (val 28,92, 29,03, dan 29,05; test 27,43, 29,99, dan 28,65).
  - Hampir seluruh keuntungannya berasal dari fitur geometri sensor. Fitur ini memisahkan kluster pusat
    yang membaca tinggi (DKI1 dan sensor murah LCS-23/24, ~1,4x stasiun referensi berjarak 1 km) dari
    stasiun pemerintah di pinggiran.
  - Hasil LOSO-nya nyata, tetapi dipelajari dari 13 stasiun dan sebagian merekam bias jaringan sensor,
    sehingga belum layak dipakai pada grid sebelum pemeriksaan di bagian Rekomendasi.
- **Mengapa PM2.5 tidak dapat diperbaiki dengan masukan yang ada:**
  - 54% variansi PM2.5 adalah offset per stasiun yang menetap. Offset ini stabil terhadap waktu:
    r = 0,92 antara paruh pertama dan kedua blok train, 0,87 antara blok train dan test.
  - Tidak ada kovariat maupun stasiun tetangga yang dapat memprediksinya (R² leave-one-station-out ≤ 0).
  - Hanya tingkat per jam bersama (14% variansi) yang dapat diprediksi dari stasiun tetangga.
- **Koreksi IDW (model mempelajari observasi − IDW, seperti IDW-residual ST-GNN) tidak mengalahkan IDW
  yang dikoreksinya.**
  - Koreksi IDW p = 1 seluruh stasiun dengan LightGBM: RMSE LOSO test 22,29, IDW-nya sendiri 22,18.
  - Koreksi IDW p = 2, k = 8 lebih lemah daripada koreksi IDW p = 1; basis yang lebih baik membantu, tetapi
    koreksinya tidak menambah sinyal.
- **Memprediksi konsentrasi langsung dengan IDW sebagai fitur juga tidak lebih baik.** LightGBM dengan
  kedua hasil IDW sebagai fitur mendapat RMSE LOSO test 22,34 (+0,16 terhadap IDW p = 1, IK −0,03 s.d. +0,34).

## Protokol

| Butir | Pilihan |
|---|---|
| Protokol utama | LOSO per grup stasiun pada blok test: PM2.5 97 stasiun (4.358 stasiun-jam), NO2 13 stasiun (604) |
| Pemilihan model | LOSO pada blok val dengan model dan konfigurasi yang sama; hasil test model pemenang val adalah angka yang dilaporkan |
| Protokol tambahan | Split tetap blind ganda PM2.5 (15 stasiun test) |
| Aturan kebocoran | Grup yang ditahan dikeluarkan dari baris pelatihan **dan** dari seluruh fitur berbasis sensor; sumber adalah stasiun lain pada jam yang sama; fitur mengikuti aturan as-of snapshot |
| Pemilihan fitur dan hyperparameter | CV terblok spasio-temporal pada pool pelatihan (stasiun train × blok train): 5 fold grup stasiun, masing-masing dipasangkan dengan satu potongan waktu, jeda 24 jam, 3 ulangan |
| Ketidakpastian | Bootstrap klaster stasiun 95% untuk selisih RMSE (4.000 resampel) |

**Mengapa CV dibuat terblok spasio-temporal:**
- CV yang hanya mengelompokkan stasiun sempat dicoba lalu ditolak.
- Jam yang sama muncul di sisi pelatihan dan validasi, sehingga model pohon mempelajari pola tiap jam dari
  label stasiun lain. Hasilnya CV 16,7, padahal val 23,8.
- CV terblok konsisten dengan blok val dan test.

## Kandidat

- **Interpolator (tanpa pelatihan):**
  - nearest neighbour;
  - IDW (p = 1/2/3, k = 4/8/semua);
  - kernel Gauss 2/5/10 km;
  - rerata dan median sumber;
  - ordinary kriging (variogram eksponensial gabungan, di-fit per fold);
  - koreksi bias GEOS-CF (aditif dan multiplikatif).
- **Model terlatih:**
  - ridge, random forest, extra trees, LightGBM, XGBoost, MLP;
  - regression kriging (model CV terbaik + IDW residual out-of-fold);
  - ensemble (semua, top 2/3 menurut val).
- **Target yang dibandingkan:** langsung, residual terhadap IDW, residual terhadap rerata sumber, dan log
  rasio. Residual terhadap rerata sumber menang untuk model pohon; target langsung untuk ridge dan MLP.
  Fitur IDW p = 2, k = 8 selalu menjadi masukan setiap model terlatih.
- **Koreksi IDW ([`idw_correction.py`](idw_correction.py)):** model mempelajari observasi − IDW lalu
  menambahkan IDW kembali, dengan dua basis (IDW p = 2, k = 8 dan IDW p = 1 seluruh stasiun) × tiga model
  (ridge dan LightGBM dengan kolom terpilih, LightGBM seluruh fitur). Basis IDW selalu menjadi fitur, dan
  hyperparameter disetel ulang untuk setiap target.
- **LightGBM langsung (`idw_correction.py direct`):** pasangannya yang memprediksi konsentrasi langsung,
  dengan kedua hasil IDW sebagai fitur (kolom terpilih) atau dengan seluruh fitur; hyperparameter disetel
  ulang untuk target langsung.
- **Grup fitur (~70 kolom):**
  - statistik interpolasi dan geometri sensor;
  - lag medan interpolasi;
  - GEOS-CF analisis, GEOS-CF prakiraan (+ varian terkoreksi bias);
  - cuaca;
  - satelit (kolom NO2 Sentinel-5P, cahaya malam);
  - lahan (NDVI, NDBI);
  - kalender dan koordinat.
- **Pengganti `dist_nearest_sensor_m`:** kolom dari FeatureMatrix diganti dengan jarak yang tidak memakai
  grup sendiri. Untuk stasiun train kolom aslinya ~0, sehingga sebarannya berbeda dari stasiun uji.
- **Pemilihan:**
  - seleksi grup maju dengan CV terblok, berhenti bila perbaikan < 0,05;
  - lalu random search 7–30 konfigurasi per model.
- **Uji ketahanan:**
  - LightGBM dan extra trees juga disetel pada seluruh fitur;
  - seleksi diulang dengan LightGBM teregularisasi hasil penyetelan.

## Diagnostik (PM2.5, blok train)

| Komponen | Variansi (µg/m³)² | Porsi |
|---|---|---|
| Tingkat per jam bersama | 80 | 14% |
| Offset stasiun yang menetap | 300 | 54% |
| Sisa per stasiun-jam | 176 | 32% |

- **Struktur spasial:**
  - variogram anomali per jam datar dari 1 sampai 30 km (nugget murni);
  - offset suatu stasiun berkorelasi −0,04 dengan offset stasiun tetangganya.
- **Offset terhadap kovariat:**
  - |ρ Spearman| ≤ 0,26 untuk NDVI, NDBI, cahaya malam, NO2 Sentinel-5P, GEOS-CF, koordinat, dan jarak
    antarsensor;
  - R² LOSO offset ~ kovariat: ridge −0,03, random forest −0,14.
- **Komposisi galat:** pada galat LOSO test model terbaik, dua pertiga kuadrat galat adalah galat rerata
  per stasiun (RMS 18,1) dan sepertiga sebaran antarjam (RMS 13,0).
- **Hasil seleksi fitur:**
  - Untuk LightGBM tidak ada grup yang dipertahankan selain fitur IDW; setiap grup menaikkan galat CV.
  - Ridge hanya mempertahankan statistik interpolasi tambahan.
  - LightGBM yang disetel pada seluruh fitur: CV 24,42, dibanding 23,31 dengan fitur IDW saja.

## PM2.5: LOSO (protokol utama)

| Model | RMSE val (pemilihan) | RMSE test | Bias test | vs IDW p2 k8 [IK 95%] | vs IDW p1 semua [IK 95%] |
|---|---|---|---|---|---|
| **IDW p = 1, seluruh stasiun** (terpilih) | **23,20** | **22,18** | −0,62 | −1,82 [−3,05; −0,63] | – |
| Kernel Gauss 10 km | 23,38 | 22,42 | +0,03 | −1,57 [−3,02; −0,16] | +0,25 [−0,05; +0,57] |
| Ordinary kriging | 23,39 | 22,26 | −0,15 | −1,73 [−2,78; −0,72] | +0,08 [−0,25; +0,43] |
| Ensemble, 6 model terlatih | 23,55 | 22,29 | −0,33 | −1,71 [−3,16; −0,29] | +0,11 [−0,19; +0,41] |
| Ridge | 23,73 | 22,38 | −0,32 | −1,61 [−2,91; −0,35] | +0,20 [−0,07; +0,52] |
| Extra trees | 23,68 | 22,59 | −0,17 | −1,41 [−2,94; +0,07] | +0,41 [+0,01; +0,83] |
| LightGBM | 23,75 | 22,62 | −0,26 | −1,38 [−2,90; +0,11] | +0,44 [+0,04; +0,86] |
| XGBoost | 23,78 | 22,62 | −0,27 | −1,38 [−2,89; +0,10] | +0,44 [+0,05; +0,86] |
| Random forest | 23,82 | 22,66 | −0,29 | −1,34 [−2,86; +0,14] | +0,48 [+0,09; +0,89] |
| MLP | 24,00 | 22,12 | −0,68 | −1,88 [−3,47; −0,32] | −0,06 [−0,90; +0,83] |
| Koreksi IDW p1 semua: LightGBM | 23,42 | 22,29 | −0,34 | −1,70 [−3,13; −0,35] | +0,11 [−0,11; +0,32] |
| Koreksi IDW p1 semua: ridge | 23,64 | 22,30 | −0,50 | −1,70 [−3,00; −0,44] | +0,12 [−0,15; +0,42] |
| LightGBM langsung, fitur IDW p2 k8 + p1 semua | 23,53 | 22,34 | −0,42 | −1,66 [−2,98; −0,39] | +0,16 [−0,03; +0,34] |
| Koreksi IDW p2 k8: ridge | 23,81 | 22,46 | −0,24 | −1,53 [−2,82; −0,28] | +0,28 [+0,03; +0,56] |
| Koreksi IDW p1 semua: LightGBM, seluruh fitur | 24,65 | 22,75 | −0,64 | −1,25 [−2,94; +0,42] | +0,57 [−0,56; +1,77] |
| Koreksi IDW p2 k8: LightGBM | 24,72 | 22,97 | −0,01 | −1,02 [−1,98; −0,09] | +0,79 [+0,40; +1,20] |
| Koreksi IDW p2 k8: LightGBM, seluruh fitur | 24,92 | 23,16 | −0,39 | −0,84 [−2,63; +0,99] | +0,98 [−0,39; +2,42] |
| LightGBM langsung, seluruh fitur | 25,05 | 22,95 | −0,40 | −1,05 [−2,77; +0,62] | +0,77 [−0,35; +1,95] |
| LightGBM, seluruh fitur, disetel | 24,68 | 23,10 | −1,00 | −0,90 [−2,59; +0,81] | +0,92 [−0,18; +2,06] |
| IDW p = 2, k = 8 (baseline) | 25,56 | 24,00 | −1,57 | – | +1,82 [+0,63; +3,05] |
| Regression kriging (ridge) | 26,19 | 24,19 | −1,58 | +0,20 [−0,11; +0,55] | +2,02 [+0,71; +3,36] |
| GEOS-CF × IDW(obs/GEOS-CF) | 25,57 | 24,20 | −1,57 | +0,20 [−0,32; +0,77] | +2,02 [+0,56; +3,52] |
| Nearest neighbour | 31,38 | 28,66 | −0,58 | +4,66 [+2,65; +6,75] | +6,48 [+3,67; +9,35] |

- Konfigurasi terpilih sama dengan IDW tersetel yang sudah dilaporkan TI-AI-04 (power 1, seluruh stasiun,
  22,18).
- Angka MLP 22,12 berada dalam rentang derau. MLP berada di urutan terakhir model terlatih pada CV dan val.
- Koreksi IDW terbaik (LightGBM pada IDW p = 1 seluruh stasiun) setara dengan IDW tersebut tanpa koreksi
  (+0,11; IK −0,11 s.d. +0,32). Koreksi IDW p = 2, k = 8 dengan LightGBM signifikan lebih buruk daripada IDW
  p = 1 tanpa koreksi.
- LightGBM langsung dengan kedua hasil IDW sebagai fitur juga setara dengan IDW p = 1 tanpa model. Untuk
  model linear, target langsung dan koreksi IDW praktis sama; untuk model pohon, koreksi sedikit lebih
  mudah dipelajari, tetapi keduanya tidak menambah sinyal.

## NO2: LOSO (protokol utama)

| Model | RMSE val (pemilihan) | RMSE test | Bias test | vs IDW p2 k8 [IK 95%] | Stasiun membaik |
|---|---|---|---|---|---|
| **LightGBM langsung, seluruh fitur** (terpilih) | **28,92** | **27,43** | +1,66 | −19,56 [−29,74; −10,40] | 13/13 |
| Koreksi IDW p2 k8: LightGBM, seluruh fitur | 29,03 | 29,99 | +3,68 | −17,01 [−25,66; −9,48] | 12/13 |
| LightGBM, seluruh fitur, disetel (koreksi rerata sumber) | 29,05 | 28,65 | +3,49 | −18,34 [−28,93; −8,82] | 12/13 |
| Koreksi IDW p1 semua: LightGBM, seluruh fitur | 32,18 | 31,07 | +5,44 | −15,92 [−27,19; −5,45] | 11/13 |
| Extra trees, seluruh fitur, disetel | 38,78 | 38,43 | +6,85 | −8,56 [−16,81; −3,00] | 10/13 |
| Koreksi IDW p1 semua: ridge | 39,51 | 40,90 | +7,10 | −6,09 [−16,59; +4,42] | 10/13 |
| Koreksi IDW p2 k8: ridge | 39,82 | 41,24 | +7,44 | −5,75 [−16,04; +4,17] | 10/13 |
| Ridge | 39,86 | 41,31 | +6,16 | −5,68 [−14,99; +3,81] | 10/13 |
| Koreksi IDW p2 k8: LightGBM | 41,15 | 42,23 | +3,14 | −4,77 [−13,77; +5,00] | 11/13 |
| LightGBM langsung, fitur IDW p2 k8 + p1 semua | 42,69 | 43,06 | +0,82 | −3,93 [−14,87; +7,03] | 11/13 |
| Koreksi IDW p1 semua: LightGBM | 43,15 | 42,64 | +1,86 | −4,35 [−15,56; +7,05] | 11/13 |
| IDW p = 2, seluruh stasiun | 44,64 | 44,66 | +18,89 | −2,33 [−3,85; −0,68] | 11/13 |
| IDW p = 1, seluruh stasiun | 46,14 | 45,55 | +11,63 | −1,44 [−10,38; +7,71] | 7/13 |
| IDW p = 2, k = 8 (baseline) | 46,46 | 46,99 | +22,10 | – | – |

Konfigurasi NO2 berasal dari seleksi PM2.5 (tanpa penyetelan khusus NO2), sehingga pemilihan di antara
konfigurasi tersebut hanya dilakukan pada blok val. Tiga model teratas berselisih paling banyak 0,13 pada
val dan ketiganya bergantung pada fitur geometri sensor yang sama, sehingga praktis setara; yang berbeda
hanya bentuk targetnya (langsung, koreksi IDW, koreksi rerata sumber).

**Sumber keuntungan NO2** ([`results/no2_ablation.csv`](results/no2_ablation.csv), LightGBM dengan
hyperparameter yang sama):

| Varian | RMSE val | RMSE test |
|---|---|---|
| Fitur IDW saja | 49,60 | 47,04 |
| + geometri sensor saja | 29,19 | 29,82 |
| Seluruh fitur | 29,05 | 28,65 |
| Seluruh fitur − geometri sensor | 49,21 | 47,29 |
| + kalender + cuaca + GEOS-CF (tanpa geometri) | 48,83 | 44,28 |

Importance (gain): jarak ke stasiun NO2 terdekat 34%, kerapatan sensor 2 km 27%, NDBI 8%.

**Dua populasi stasiun NO2 uji:**
- 10 stasiun pemerintah dengan rerata 13–52 µg/m³, tersebar di seluruh kota.
- Kluster pusat dengan jarak 1–3 km satu sama lain di Jakarta Pusat:
  - DKI1 (Bundaran HI, rerata 107);
  - sensor murah LCS-23 (138) dan LCS-24 (145);
  - LCS-21 (125) tidak memiliki jam pada blok test.

**Mekanismenya:**
- IDW menyebarkan kluster pusat ke stasiun pinggiran: bias +85 di Kebun Jeruk dan +68 di Ancol.
- Fitur geometri membuat model mempelajari aturan "jauh dari kluster rapat → lebih rendah".

**Pembacaan sensor murah:**
- Terhadap DKI1, sensor NO2 murah membaca lebih tinggi. LCS-23 pada jarak 0,98 km: rasio median 1,37,
  korelasi per jam 0,56.
- Pasangan stasiun pemerintah berjarak 5–7 km selisihnya hanya 5–11%.

## IDW-residual ST-GNN (Colab, split tetap, seed 42)

Run notebook IDW-residual pada ds-v0.3.0 ([`stgnn_idw_residual/`](stgnn_idw_residual)), dibandingkan dengan
IDW p = 2, k = 8 pada kunci yang sama (689 stasiun-jam test):

| Run | Epoch terbaik | RMSE test | vs IDW [IK 95%] |
|---|---|---|---|
| IDW | – | 18,74 | – |
| Kontrol (tanpa prakiraan) | 1 | 18,45 | −0,29 [−0,49; −0,07] |
| Dengan prakiraan GEOS-CF | 3 | 19,39 | +0,64 [−1,05; +2,26] |

- **Kontrol:**
  - Model praktis tidak terlatih. Koreksinya rata-rata +0,5 µg/m³ dan tidak pernah melebihi 1,4.
  - IDW ditambah konstanta yang sama sudah memberi 18,50.
  - Pada blok val selisihnya tidak signifikan.
- **Dengan prakiraan:** koreksinya bervariasi sekitar ±6 µg/m³ tetapi hanya berkorelasi lemah dengan galat
  IDW.
- **Kedua run overfitting sejak epoch 1–3,** sejalan dengan diagnostik di atas.

## Hasil lain

- **GEOS-CF tidak membantu:**
  - PM2.5 GEOS-CF mentah ~3–4x sensor (bias +97 pada rerata 36);
  - varian terkoreksi bias tidak lebih baik dari IDW (multiplikatif) atau jelas lebih buruk (aditif);
  - grup prakiraan tidak pernah terpilih.
- **Lag, kalender, dan cuaca tidak menambah apa pun** untuk PM2.5 di stasiun yang tidak terlihat. Fitur ini
  menggambarkan tingkat temporal bersama, yang sudah diberikan stasiun sumber pada jam yang sama.
- **Regression kriging tidak menambah apa pun,** karena residual tidak memiliki struktur spasial.

## Rekomendasi

1. **PM2.5:**
   - Pakai IDW p = 1 dengan seluruh stasiun (atau ordinary kriging) sebagai model downscaling dan fallback.
     Konfigurasi ini terpilih pada val dan signifikan lebih baik dari default sekarang (p = 2, k = 8).
     Perubahan default belum dilakukan di kode dan menunggu keputusan tim.
   - Pemodelan lanjutan kecil kemungkinannya berhasil sebelum offset stasiun terjelaskan (pertanyaan
     kalibrasi ke DLH, selisih timestamp).
2. **NO2:**
   - Sebelum memakai model LightGBM, putuskan perlakuan untuk sensor NO2 LCS-*: bandingkan deret yang
     berdekatan, lalu tandai, kalibrasi, atau keluarkan (seperti `suspected_low_bias` untuk PM2.5).
   - Setelah itu jalankan ulang perbandingan ini. Bila keuntungannya bertahan tanpa fitur geometri, model
     tersebut layak dipakai. Bila masih bergantung pada "jarak ke sensor terdekat", model akan memetakan
     kerapatan sensor ke grid.
3. **ST-GNN:** batasan yang sama berlaku. Data tidak memuat informasi yang diperlukan untuk mengalahkan
   interpolasi halus PM2.5 di lokasi tanpa sensor.

## Menjalankan ulang

### Training model sistem

Implementasi bersama berada di `src/data_worker/spatial_model`.
Jalankan `python -m spatial_model.training train pm25 --snapshot <ds-v0.3.0> --out <run-baru> --device cuda` dari `src/data_worker`.
Untuk NO₂, ganti `pm25` dengan `no2`. Notebook interaktif ada di `notebooks/spatial_model/`.
Lihat [panduan model](../../../src/data_worker/spatial_model/README.md).
Artefak dan konfigurasi eksperimen pada laporan di bawah merupakan bukti historis; training baru menghasilkan identitas run baru.

### Model pembanding

Lingkungan Python dengan pandas, scikit-learn, pyproj, scipy, joblib, lightgbm 4.7, xgboost 3.4, dan
tabulate. Jalankan dari folder ini:

```bash
python fixed.py                    # seleksi + split tetap (~32 menit)
python fair_tuning.py              # uji ketahanan (~25 menit)
python idw_correction.py           # koreksi IDW (~30 menit)
python idw_correction.py direct    # LightGBM langsung (~16 menit)
BLOCK=val THREADS=1 JOBS=8 python loso.py pm25
BLOCK=val THREADS=1 JOBS=8 python loso.py no2
THREADS=1 JOBS=8 python loso.py pm25    # ~11 menit
THREADS=1 JOBS=8 python loso.py no2
THREADS=1 python no2_ablation.py
python summarize.py > results/summary.md
```

| Berkas | Isi |
|---|---|
| `common.py` | Panel stasiun-jam, fitur spasial tanpa kebocoran, kriging, metrik, bootstrap |
| `models.py` | Grup fitur, parameterisasi target, model, ruang pencarian |
| `fixed.py`, `fair_tuning.py` | Seleksi fitur, target, dan hyperparameter; split tetap; uji ketahanan |
| `idw_correction.py` | Model koreksi IDW (observasi − IDW) dengan dua basis IDW; mode `direct` untuk LightGBM langsung |
| `loso.py`, `no2_ablation.py` | LOSO blok val/test; ablasi NO2 |
| `summarize.py` | Tabel ringkasan `results/summary.md` |
| `results/` | Metrik per model, jejak seleksi (`fixed_state.json`), ringkasan CSV |

Prediksi per baris (`results/*_predictions.parquet`, ~4 MB) tidak di-commit dan dapat dibangkitkan ulang
dengan perintah di atas.

## XGBoost GPU fixed split

Jalankan `python run_xgboost_fixed.py` di environment `deep` dengan XGBoost 3.0.2
(CUDA 12.5; kompatibel dengan driver lokal). Target PM2.5, seed 42, snapshot ds-v0.3.0.
Runner membandingkan enam kandidat: prediksi langsung/koreksi IDW dengan kedalaman 2/4/6.
Early stopping dan pemilihan kandidat memakai validation; test dievaluasi setelah pemilihan.
Input mencakup 54 fitur tabular, tanpa lag yang berbasis pergeseran baris.
Pembacaan sensor hanya dari stasiun train, selalu mengecualikan seluruh grup target.

Hasil, model, prediksi, source, dan manifest disimpan dalam folder bertanggal di `xgboost_fixed/`.
Run `pm25_gpu_seed42_20261014_202354_090525` memilih koreksi IDW, kedalaman 4, satu pohon.
Test RMSE: IDW p2/k8 **18.7448**, XGBoost **18.4782** ug/m3, pada 689 observasi.
Ini eksperimen eksploratif satu seed pada dataset test yang sudah pernah diperiksa.

## Perbandingan IDW, IDW + XGBoost, dan IDW + ST-GNN

`python compare_idw_hybrids.py` menjalankan XGBoost residual pada seluruh 95 grup LOSO
PM2.5 dan menggabungkannya dengan hasil fixed split serta ST-GNN GPU yang sudah ada.
Untuk melanjutkan run terputus: `python compare_idw_hybrids.py --resume <folder-run>`.
Lokasi run pembanding didefinisikan di bagian atas script.

Setiap fold mengeluarkan seluruh grup target dari label train/validation dan sumber sensor.
Kedalaman 2/4/6, jumlah pohon, serta opsi koreksi nol dipilih pada validation grup lain.
Baris train tanpa sumber IDW dikeluarkan dan dicatat, sesuai ST-GNN;
seluruh baris validation/test harus memiliki prediksi IDW yang valid.
IDW dasar menggunakan p=2/k=8. Runner memverifikasi kecocokan stasiun-jam,
label, dan baseline IDW sebelum menghitung metrik serta bootstrap berpasangan per grup.

Model dan prediksi lengkap disimpan di `graphify-out/xgboost-ds-v0.3.0/`;
tabel ringkas dan laporan di `idw_hybrid_comparison/`.
Ini perbandingan pipeline dengan representasi fitur dan anggaran seleksi yang berbeda,
bukan pengujian jenis arsitektur dengan input identik.

## NO2: model lain dengan seluruh fitur

`THREADS=1 python no2_feature_sets.py` menguji apakah keuntungan LightGBM pada NO2 berasal dari fitur atau dari
algoritmanya. Ridge, MLP, XGBoost, random forest, dan LightGBM dijalankan dengan target langsung dan dua set fitur:
seluruh 62 kolom, serta fitur IDW ditambah tujuh kolom geometri sensor. Setiap model memakai hyperparameter hasil
penyetelan PM2.5 untuk keluarganya (tanpa penyetelan ulang); set fitur per model dipilih pada blok val. Hasil:
[`results/no2_feature_sets.csv`](results/no2_feature_sets.csv).

| Model | Set terpilih (val) | RMSE val | RMSE test | Stasiun membaik vs IDW p2 k8 |
|---|---|---|---|---|
| LightGBM | seluruh fitur | 30,23 | 28,95 | 13/13 |
| Random forest | seluruh fitur | 32,26 | 31,31 | 12/13 |
| XGBoost | seluruh fitur | 37,05 | 34,82 | 12/13 |
| MLP | seluruh fitur | 69,11 | 65,94 | 9/13 |
| Ridge | IDW + geometri sensor | 72,62 | 69,98 | 11/13 |

- Model pohon lain juga membaik tajam dengan seluruh fitur (fitur terpilih: 46,45–48,18), sehingga keuntungan NO2
  terutama berasal dari fitur geometri sensor, bukan dari LightGBM saja. LightGBM pada tabel NO2 utama (27,43) memakai
  hyperparameter yang disetel untuk target langsung.
- Ridge dan MLP jauh lebih buruk daripada IDW (46,99), kemungkinan karena hubungan geometri sensor dengan NO2
  nonlinear dan hanya ada 13 stasiun latih.

## IDW-ST-GNN NO2 LOSO (Colab GPU)

Notebook ST-GNN yang sama dengan run LOSO PM2.5, dijalankan untuk NO2 pada 13 fold (604 stasiun-jam test) dengan
runner yang diberi opsi `--target`, `--data`, `--out`, dan `--device`. Hasil dan salinan runner:
[`stgnn_idw_residual/ds-v0.3.0_loso_no2_gpu_seed42_20261015_0e6b77e4/`](stgnn_idw_residual/ds-v0.3.0_loso_no2_gpu_seed42_20261015_0e6b77e4/report.md).

| Metode | MAE | RMSE | Bias | R² |
|---|---|---|---|---|
| IDW p2 k8 | 38,81 | 46,99 | +22,10 | 0,14 |
| IDW p1 all | 37,45 | 45,55 | +11,63 | 0,19 |
| IDW-ST-GNN | 33,10 | 47,17 | +0,02 | 0,13 |

- ST-GNN menghilangkan bias IDW dan memperbaiki 10 dari 13 stasiun, tetapi RMSE gabungannya setara IDW (selisih
  +0,18; IK 95% −14,36 sampai +13,00) karena galat besar pada DKI1 dan LCS-24.
- Masukan ST-GNN hanya memuat sebagian geometri sensor (jarak ke stasiun sumber terdekat dan jumlah sumber), tanpa
  kerapatan sensor yang paling berperan pada LightGBM seluruh fitur (27,43).
- Satu seed pada data test yang sudah pernah diperiksa; hasil eksploratif.
