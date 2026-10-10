# FeatureMatrix ST-GNN (TI-AI-02)

Masukan ST-GNN grid: satu baris per (sel grid 100 m, jam) dengan fitur sumber heterogen yang diselaraskan,
label stasiun-jam terpisah, dan graf grid (Dokumen Desain Tabel 3.3, Tabel 3.8 `build_feature_matrix`,
ABD-01, ABD-02; isu #13).

**Basis kode:** bundle ST-GNN `jakarta_processed_bundle_20261007` oleh FedrianzD. `prepare_stgnn.py` berasal
dari bundle tersebut. Reader sumber dan pembentuk label, yang di bundle berada di notebook, ditulis ulang di
`sources.py` dan `labels.py`. Dengan aturan VIIRS bundle (`--viirs-cell-rule nearest_listed`), keduanya
mereproduksi `nodes`, `edges`, `labels`, dan seluruh 53 kolom `features` bundle persis. Bukti:
[`docs/experiments/TI-AI-02`](../../../docs/experiments/TI-AI-02/README.md).

**Tambahan TI-AI-02** terhadap bundle:
- **Jarak sensor:** `dist_nearest_sensor_m`.
- **Penanda ketersediaan eksplisit:** `<nilai>_available`.
- **Data dictionary:** `feature_spec.json`.
- **Laporan kualitas.**
- **Pemetaan sel VIIRS yang dikoreksi** (lihat [Perbedaan dengan bundle](#perbedaan-dengan-bundle)).

## Menjalankan

Dari `src/data_worker`, dengan folder ekspor Parquet `ground_truth` (mis. `sippolusi_ground_truth_20261006`):

```bash
python -m feature_matrix export --ground-truth <folder ekspor> --out <folder keluaran>
python -m feature_matrix quality <folder keluaran>   # tulis ulang quality_report.{json,md}
python -m feature_matrix spec                        # data dictionary → feature_matrix/feature_spec.json
```

Bawaan `export`:
- label `time_utc` dari 2026-09-13 01:00 sampai sebelum 2026-10-01 00:00 UTC;
- train < 25 Sep, validation 25–27 Sep, test 28–30 Sep;
- jendela 24 jam, satu hop konteks.

Durasi sekitar 10 detik. Keluaran ±3,3 MB dan tidak di-commit.

| Berkas | Isi |
|---|---|
| `features.parquet` | Satu baris per sel/jam: fitur temporal, Landsat, jarak sensor, penanda ketersediaan, provenans sumber; tanpa atribut OSM dan tanpa label |
| `nodes.parquet` | `node_index` ↔ `grid_id` dan koordinat pusat sel |
| `edges.parquet` | `source_node`, `target_node`, `distance_m` |
| `labels.parquet` | Label stasiun-jam, `node_index`, jumlah pembacaan, split kronologis |
| `manifest.json` | SHA-256 masukan (`dataset_processed`, ekspor ground truth) dan keluaran, kebijakan, asumsi, commit |
| `quality_report.{json,md}` | Skema, cakupan, duplikasi, nilai hilang, konsistensi penanda, kebocoran waktu |

## Susunan

| Berkas | Isi |
|---|---|
| `prepare_stgnn.py` | Dari bundle: graf 8 tetangga, label per node, ekspor fitur bertahap dengan pemeriksaan, `read_stgnn_batch`, `grid_predictions_to_roads`. Ditambah jarak sensor dan penanda ketersediaan |
| `sources.py` | `SourceReader`: pembaca langsung `dataset_processed` per sumber |
| `labels.py` | Label stasiun-jam dari ekspor `ground_truth` |
| `grid.py` | Grid 100 m TI-AI-01 (445 × 445, sel aktif 195.782) dan jarak ke sensor |
| `spec.py`, `feature_spec.json` | Data dictionary: tipe, satuan, peran, sumber, penyelarasan setiap kolom |
| `quality.py` | Laporan kualitas |

## Grid, waktu, dan graf

- **Grid:**
  - EPSG:32748, sel 100 m, ID `rRRRR_cCCCC` dengan baris 0 di utara, sama dengan Landsat dan OSM;
  - lookup GEOS-CF memakai `gy × 445 + gx` dari selatan (`gy = 444 − baris`);
  - grid TI-AI-04/05 (`JAKARTA_BBOX`) termuat di dalamnya dengan kisi sejajar.
- **Waktu:** `time_utc` adalah akhir jam label sekaligus waktu inferensi τ. Time window desain adalah
  [τ − 1 jam, τ). Fitur pada baris τ hanya memakai data yang tersedia ≤ τ.
- **Graf:**
  - node = sel yang memuat stasiun berlabel, ditambah satu cincin tetangga sebagai konteks;
  - edge 8 tetangga dua arah, 100 m atau 141,42 m, tanpa self-loop tersimpan;
  - cakupannya hanya area berlabel dan konteksnya, bukan seluruh Jakarta.

## Aturan ketersediaan (tanpa kebocoran waktu)

| Sumber | Kolom | Aturan |
|---|---|---|
| Landsat 8/9 | `ndvi`, `ndbi` (masukan statis), `lst_c` (tidak dipakai) | Pengamatan valid terakhir sel dengan `produced_at <= τ` |
| Sentinel-5P | `no2_mol_m2` | Sama, pada sel 5 km yang memuat pusat sel |
| VIIRS VNP46A2 | `ntl` | Sama, pada sel 15″ yang memuat pusat sel (indeks tile) |
| GEOS-CF `ana` | `geoscf_pm25_ugm3`, `geoscf_no2_ugm3` | Valid time terbaru dengan `available_at_utc <= τ`; umur 7–28 jam |
| Open-Meteo | 7 variabel cuaca | Valid time terbaru dengan waktu tersedia ≤ τ; umur 3–8 jam pada `weather_age_hours`; satu titik, seragam (lihat [Cuaca](#cuaca-open-meteo)) |
| SPKU | `dist_nearest_sensor_m` | Jarak Euclidean EPSG:32748 pusat sel ke stasiun terdekat di himpunan yang diberikan |

- **Tanpa batas umur maksimum:** nilai satelit lama tetap terbawa, dengan umurnya pada `<nilai>_age_hours`.
  - Umur Landsat bisa mencapai 200 hari, Sentinel-5P 10 hari, dan VIIRS 17 hari.
  - Akibatnya "tersedia" berarti ada nilai kausal; seberapa basi nilainya dibaca dari umurnya.
- **Jam sumber bukan prediktor:** `observed_at`, `produced_at`, `available_at_utc` dan ID scene adalah
  provenans, dan pemeriksaan ekspor menolak jam yang melewati τ.

## Penanda ketersediaan dan nilai hilang

- **Penanda per nilai:** setiap kolom nilai (`TEMPORAL_VALUES`, `ndvi`, `ndbi`, `lst_c`,
  `dist_nearest_sensor_m`) memiliki penanda Boolean `<nilai>_available`, yang `True` tepat bila nilainya finite.
- **Tanpa imputasi:** nilai hilang tetap NaN.
- **Penolakan saat ekspor:** penanda yang tidak konsisten, atau kolom Boolean lain, ditolak.
- **Bukan masukan model:** `read_stgnn_batch` tidak mengembalikan penanda. Model membentuk kanal missingness
  sendiri dari NaN setelah praproses yang di-fit pada data train.

## Jarak sensor

- **Himpunan stasiun:** bawaannya seluruh stasiun berlabel (evaluasi transduktif).
  - Sel berlabel berjarak ≤ 66 m, karena stasiunnya sendiri berada di dalam sel.
  - Sel konteks berjarak 51–208 m.
  - Fitur ini baru informatif untuk evaluasi lokasi tak terlihat dan inferensi seluruh grid.
- **Per fold (TI-AI-03):** jarak dihitung ulang tanpa stasiun uji lewat
  `read_stgnn_batch(dir, t, sensors=<stasiun non-uji>)` atau `sensor_distances(nodes, sensors)`.
- **Hasil batch:** `read_stgnn_batch` mengembalikan `sensor_static` `[nodes, 1]` di samping `land_static`.

## Label

- **Nilai label:** rata-rata pembacaan valid dalam [τ − 1 jam, τ), dicap pada τ, satu baris per stasiun (stasiun
  yang berbagi sel tidak dirata-rata).
- **Pembacaan valid:**
  - `qc == ""`, stasiun `in_jakarta_bbox`;
  - tanpa nilai 0 dan tanpa `>= 999.99`;
  - tanpa `DKI_PM25_40`;
  - tanpa PM2.5 `DKI_PM25_33` sejak 2026-09-19 10:00Z.
- **Satuan:** µg/m³, terverifikasi terhadap ISPU portal (lihat [Satuan label](#satuan-label)).
- **Split:** kronologis dan transduktif. Stasiun yang sama muncul di train, validation, dan test, sehingga
  belum mengukur generalisasi ke lokasi baru. Hal itu dikerjakan pada TI-AI-03.

## Perbedaan dengan bundle

| Aspek | Bundle 2026-10-07 | Repo |
|---|---|---|
| Sel VIIRS | `longitude`/`latitude` terdekat. Kolom ini adalah sudut barat laut sel, sehingga pemetaan bergeser setengah piksel (±230 m) ke tenggara | Sel yang memuat pusat sel 100 m menurut indeks tile; `ntl` berubah pada 72,6% baris. Aturan lama tersedia lewat `--viirs-cell-rule nearest_listed` |
| Jarak sensor | Tidak ada | `dist_nearest_sensor_m` dan `sensor_static` |
| Ketersediaan | Hanya NaN; kolom Boolean ditolak | NaN dan `<nilai>_available`, diperiksa konsisten |
| `lst_c` | Ikut diekspor (manifest bundle masih mencantumkannya sebagai nilai land) | Diekspor sebagai provenans (`excluded`), float32; bukan masukan model |
| Data dictionary, laporan kualitas | Tidak ada | `feature_spec.json`, `quality_report.{json,md}` |
| Cuaca | Nilai valid time τ, seolah tersedia pada τ | Valid time terbaru yang tersedia pada τ, dengan `weather_available_at_utc` dan `weather_age_hours` (masukan TCN ke-16). Aturan lama tersedia lewat `--weather-delay-hours -1` |

## Cuaca (Open-Meteo)

- **Masalah:** Historical Forecast API menyambung jam-jam pertama setiap run model menjadi satu deret. Nilai untuk
  valid time t berasal dari run yang dimulai sebelum t, tetapi run itu baru terbit beberapa jam kemudian.
  - Waktu terbit tidak tercatat (`feature_available_at` kosong).
  - Memakai nilai valid time τ pada waktu inferensi τ berarti memakai run yang belum terbit pada τ.
- **Aturan:** nilai valid time t dianggap tersedia pada `floor(t, 6 jam) + 8 jam`.
  - Model global untuk Jakarta (ECMWF IFS, ICON, GFS) berjalan tiap 6 jam.
  - Jeda terbit 8 jam adalah asumsi konservatif; Open-Meteo tidak mencantumkan latensinya.
  - Pada τ dipakai valid time terbaru yang sudah tersedia, sehingga umurnya 3–8 jam.
- **Pemeriksaan:** ekspor dan laporan kualitas menolak `weather_time_utc` atau `weather_available_at_utc` yang
  melewati τ.
- **Perbaikan lebih tepat:** arsip Single Runs Open-Meteo (tersedia sejak April 2026) memuat setiap run lengkap.
  Dengan arsip itu, nilai pada τ dapat diambil dari prakiraan run terakhir yang benar-benar terbit sebelum τ. Ini
  memerlukan akuisisi ulang (lingkup TI-AI-01).

## Satuan label

- **Portal tidak mencantumkan satuan.**
- **Metode verifikasi:** `scripts/check_ispu_units.py` menghitung ulang ISPU dari rata-rata 24 jam konsentrasi
  `observation`, dengan batas µg/m³ Permen LHK P.14/2020, lalu membandingkannya dengan ISPU portal
  (`observation_ispu`).
- **Hasil pada ekspor 2026-10-06:** selisih ≤ 2 poin ISPU pada:
  - PM2.5: 92,8% stasiun-jam (median selisih 0,55);
  - PM10: 92,0%;
  - NO2: 82,7%;
  - SO2: 98,1%;
  - CO: 98,5%.
- **Hipotesis ppb untuk NO2** (× 1,88) cocok pada 0%.
- **Kesimpulan:** konsentrasi sudah dalam µg/m³, sehingga tidak diperlukan konversi satuan.

## Keterbatasan

- **Grid sumber kasar:** Sentinel-5P, GEOS-CF, VIIRS, dan cuaca jauh lebih kasar daripada 100 m. Hanya Landsat
  yang bervariasi antarsel tetangga.
- **Ketersediaan berupa proksi:** `produced_at` satelit, Last-Modified GEOS-CF, dan waktu terbit run cuaca
  yang diasumsikan.
- **OSM hanya dipakai setelah prediksi** (`grid_predictions_to_roads`), dengan asumsi jalan tidak berubah sejak
  snapshot 5 Oktober.
