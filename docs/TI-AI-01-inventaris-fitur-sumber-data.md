# TI-AI-01 — Inventarisasi Fitur dan Sumber Data

Dokumen ini mendefinisikan fitur, sumber, dan transformasi data untuk:
- **Spatial Downscaling Model** (`src/spatial_model/main.py`)
- **Multi-Objective Route Model** (`src/route_model/main.py`)

Status dokumen: **baseline implementasi** untuk TI-AI-02 / TI-SE-02.  
Tanggal: 2026-09-30.

## 1) Inventaris sumber data

| Source ID | Nama sumber | Cakupan | Resolusi spasial | Resolusi temporal | Update | Akses/lisensi | Status ketersediaan | Jalur asal data (traceability) | Risiko utama |
|---|---|---|---|---|---|---|---|---|---|
| SRC-OSM | OpenStreetMap (jalan, simpul, atribut) | Area layanan SIPPolusi | vektor (node/edge) | snapshot periodik | 3 jam (sinkron worker) | ODbL (atribusi wajib) | **Tersedia** | `raw/osm/{region}/{yyyymmddhh}.pbf` → `stg_osm_edges`, `stg_osm_nodes` | kelengkapan atribut tidak merata |
| SRC-SAT | Data satelit aerosol/meteorologi (produk operasional) | Area layanan SIPPolusi | grid raster (produk-bergantung) | harian/jam-an (produk-bergantung) | 3 jam (sinkron worker) | **Per sumber produk**; wajib validasi lisensi per provider | **Parsial** (provider final belum dikunci) | `raw/sat/{provider}/{product}/{yyyymmddhh}.nc` → `stg_sat_tiles` | lisensi/retensi belum final, potensi gap cloud cover |
| SRC-AQI-ST | Ground station kualitas udara (AQI/PM2.5) | Titik stasiun terbatas | titik | jam-an | near-real-time / batch harian | **Belum ditetapkan** | **Risiko** | `raw/aq_station/{provider}/{yyyymmdd}.csv` → `stg_aq_station_obs` | distribusi spasial stasiun bias ke area padat |
| SRC-MET-ST | Ground station cuaca (angin, suhu, RH, curah hujan) | Titik stasiun terbatas | titik | jam-an | near-real-time / batch harian | **Belum ditetapkan** | **Risiko** | `raw/met_station/{provider}/{yyyymmdd}.csv` → `stg_met_station_obs` | missing rate tinggi saat outage stasiun |
| SRC-TRF | Lalu lintas historis/near-real-time (opsional) | ruas jalan tertentu | edge-level | 5–15 menit / jam-an | provider-bergantung | **Belum ditetapkan** | **Risiko** | `raw/traffic/{provider}/{yyyymmddhhmm}.parquet` → `stg_traffic_edges` | potensi lisensi komersial, cakupan terbatas |

> Catatan: sumber berstatus **Risiko** tidak boleh diasumsikan tersedia pada implementasi awal. Gunakan fallback yang terdokumentasi pada bagian transformasi.

## 2) Data dictionary fitur per model

### 2.1 Spatial Downscaling Model

| Feature ID | Nama fitur | Peran | Definisi | Satuan | Source ID | Resolusi target | Transformasi inti | Indikator ketersediaan |
|---|---|---|---|---|---|---|---|---|
| SP-P-01 | `road_density` | Predictor | panjang jalan per grid | km/km² | SRC-OSM | grid 250m, 1 jam | agregasi edge→grid (sum length / area) | `is_road_density_available` |
| SP-P-02 | `intersection_density` | Predictor | jumlah persimpangan per grid | count/km² | SRC-OSM | grid 250m, 1 jam | hitung node degree>=3 lalu agregasi | `is_intersection_density_available` |
| SP-P-03 | `sat_aod` | Predictor | aerosol optical depth dari satelit | unitless | SRC-SAT | grid 250m, 1 jam | reproyeksi + resampling ke grid model | `is_sat_aod_available` |
| SP-P-04 | `wind_speed` | Predictor | kecepatan angin permukaan | m/s | SRC-MET-ST | grid 250m, 1 jam | interpolasi spasial stasiun→grid | `is_wind_speed_available` |
| SP-P-05 | `temperature` | Predictor | suhu udara | °C | SRC-MET-ST | grid 250m, 1 jam | interpolasi spasial + winsorize p1-p99 | `is_temperature_available` |
| SP-T-01 | `pm25_grid_target` | Target/Ground truth | PM2.5 referensi untuk training | µg/m³ | SRC-AQI-ST (+downscale) | grid 250m, 1 jam | station QA/QC, transform log1p opsional | `is_pm25_target_available` |
| SP-B-01 | `hour_of_day_sin/cos` | Background term | encoding siklus harian | unitless | turunan waktu | grid 250m, 1 jam | cyclical encoding dari timestamp UTC+7 | `is_time_feature_available` |
| SP-B-02 | `day_of_week_onehot` | Background term | encoding pola mingguan | unitless | turunan waktu | grid 250m, 1 jam | one-hot (Mon-Sun) | `is_time_feature_available` |

### 2.2 Multi-Objective Route Model

| Feature ID | Nama fitur | Peran | Definisi | Satuan | Source ID | Resolusi target | Transformasi inti | Indikator ketersediaan |
|---|---|---|---|---|---|---|---|---|
| RT-P-01 | `edge_length` | Predictor | panjang segmen jalan | meter | SRC-OSM | edge, 5 menit | ekstrak geometri edge | `is_edge_length_available` |
| RT-P-02 | `edge_travel_time_est` | Predictor | estimasi waktu tempuh edge | detik | SRC-TRF (fallback OSM speed) | edge, 5 menit | prioritas traffic; fallback = length/speed_profile | `is_edge_travel_time_available` |
| RT-P-03 | `edge_pollution_cost` | Predictor | biaya paparan polusi per edge | µg/m³·menit (indeks) | keluaran Spatial Model | edge, 5 menit | join grid PM2.5 ke edge (mean sepanjang edge) | `is_edge_pollution_cost_available` |
| RT-P-04 | `edge_safety_penalty` | Predictor | penalti keamanan/akses | indeks 0-1 | SRC-OSM | edge, 5 menit | aturan berbasis tipe jalan + akses pejalan kaki | `is_edge_safety_penalty_available` |
| RT-T-01 | `chosen_route_label` | Target/Ground truth | rute pilihan aktual/historis (jika supervised) | kategori/urutan edge | log perjalanan (belum ditetapkan) | trip-level | map-matching + sequence labeling | `is_chosen_route_label_available` |
| RT-B-01 | `objective_weights` | Background term | bobot objektif (waktu/polusi/jarak) dari kebijakan produk | unitless (sum=1) | konfigurasi sistem | request-level | normalisasi bobot | `is_objective_weights_available` |

## 3) Aturan pemisahan predictor vs target

1. Kolom berlabel `SP-T-*` dan `RT-T-*` **hanya** dipakai sebagai target training/evaluasi.  
2. Target tidak boleh dipakai sebagai predictor pada timestamp yang sama.  
3. Pengecualian (mis. lagged target `t-1`) hanya boleh digunakan bila:
   - ada alasan metodologis tertulis di dokumen eksperimen, dan
   - fitur diberi nama eksplisit (`lag_pm25_1h`) dan flag leakage check lulus.

## 4) Aturan transformasi, penyelarasan waktu, dan celah data

### 4.1 Penyelarasan waktu
- Semua timestamp ingest disimpan dalam UTC; pemodelan operasional memakai lokal `Asia/Jakarta` untuk fitur kalender.
- Window inferensi default:
  - Spatial model: bucket 1 jam (`HH:00:00`).
  - Route model: bucket 5 menit (`HH:MM:00`, kelipatan 5).
- Toleransi keterlambatan data:
  - satelit/stasiun: maks 2 jam dari bucket target.
  - traffic: maks 15 menit dari bucket target.

### 4.2 Transformasi minimum implementasi
- Reproyeksi geospasial ke CRS tunggal proyek (tetapkan di TI-SE-02; default kandidat EPSG:4326 + grid operasional terdefinisi).
- Normalisasi satuan:
  - kecepatan ke m/s,
  - panjang ke meter,
  - PM2.5 ke µg/m³.
- QA/QC numerik:
  - nilai negatif pada PM2.5/AOD/curah hujan → `NULL`,
  - outlier clipping p1-p99 untuk variabel meteorologi kontinu.

### 4.3 Penanganan celah data
- Hierarki imputasi:
  1. forward-fill terbatas (maks 2 bucket),
  2. spasial neighbor mean (k-nearest grid/edge),
  3. fallback climatology per jam-hari.
- Jika semua gagal: nilai `NULL` dipertahankan dan `is_*_available=0`.
- Fitur dengan missing >30% per hari ditandai `degraded_feature=1` untuk monitoring.

## 5) Indikator ketersediaan data (wajib)

Untuk setiap fitur predictor operasional harus tersedia:
- flag boolean `is_<feature>_available`,
- `source_freshness_minutes`,
- `source_version` (provider + product version),
- `ingestion_run_id` untuk audit jalur asal.

## 6) Risiko bias, keterbatasan cakupan, asumsi penggunaan

1. **Bias spasial stasiun**: ground station cenderung berada di area tertentu; estimasi area minim sensor kurang stabil.  
2. **Bias temporal satelit**: observasi dipengaruhi cuaca/cloud cover; tidak selalu tersedia setiap bucket.  
3. **Bias jaringan jalan OSM**: kelengkapan atribut jalan berbeda antar wilayah.  
4. **Asumsi fallback traffic**: ketika data traffic tidak ada, profil kecepatan berbasis kelas jalan bisa under/over-estimate.  
5. **Lisensi belum final** untuk SRC-AQI-ST, SRC-MET-ST, SRC-TRF: diperlakukan sebagai risiko pengadaan data, bukan dianggap siap pakai.

## 7) Keputusan pemilihan fitur (baseline)

- **Dipilih untuk baseline Spatial model**: SP-P-01..SP-P-05 + SP-B-01..SP-B-02; target SP-T-01.
- **Dipilih untuk baseline Route model**: RT-P-01..RT-P-04 + RT-B-01; target RT-T-01 opsional (hanya bila data label tersedia).
- **Ditunda/bersyarat**:
  - fitur bergantung SRC-TRF jika lisensi/akses belum jelas,
  - target RT-T-01 bila log perjalanan belum tersedia.

Dokumen ini memenuhi kebutuhan inventaris fitur-sumber untuk implementasi lanjutan TI-AI-02/TI-SE-02 dengan penanda risiko pada sumber yang belum pasti.
