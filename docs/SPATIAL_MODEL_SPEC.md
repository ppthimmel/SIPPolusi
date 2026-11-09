# Spesifikasi Spatial Downscaling Model

Revisi **27 Oktober 2026**, diselaraskan dengan implementasi TI-AI-05 atas keputusan pemilik
proyek. Dokumen ini menetapkan kontrak aktif dan menggantikan ketentuan pergantian sumber
AOD/MERRA-2 pada rancangan sebelumnya. Dokumen Desain Word di direktori ini diperbarui pada
bagian keputusan arsitektur, antarmuka, sumber data, risiko, dan kasus uji terkait.

## Estimasi dan sumber atmosfer

- Dataset aktif adalah **ds-v0.3.0**; snapshot dan hasil eksperimen tidak diubah oleh revisi ini.
- PM2.5: `max(0, IDW + target_scale × correction)` dengan koreksi ST-GNN. Basis IDW memakai
  p = 2, k = 8 (`spatial_model/models/stgnn/sensors.py`).
- NO₂: LightGBM seluruh 62 fitur, target konsentrasi langsung, hasil minimal nol.
- Pasangan ini adalah keputusan implementasi pemilik proyek dan berbeda dari rekomendasi
  [laporan perbandingan model](experiments/model-comparison-ds-v0.3.0/README.md#rekomendasi)
  (IDW p = 1 seluruh stasiun untuk PM2.5). Versi PM2.5 yang terdaftar dengan alias `production`
  (`spatial-downscaling-pm25` versi 1) adalah checkpoint split tetap run `847559c6`
  (RMSE test 18,37 vs IDW 18,74), bukan fold LOSO. Bundle NO₂ sudah dipaket tetapi, menurut
  [bukti registry TI-AI-11](experiments/TI-AI-11/railway_registry_evidence.md), belum terdaftar;
  tanpa versi terdaftar Worker memakai IDW untuk NO₂.
- Analisis dan prakiraan **GEOS-CF** menjadi fitur regional pada pelatihan dan operasi,
  dengan satuan dan urutan fitur sesuai bundle. Data hanya boleh dipakai apabila tersedia
  pada waktu inferensi. Pemetaan ke 100 m tidak menghasilkan pengukuran baru 100 m.
- AOD MAIAC dan MERRA-2 berada di luar kontrak fitur aktif. Tidak ada kewajiban pergantian
  sumber berdasarkan ketersediaan AOD, tidak ada penalti confidence khusus AOD, dan tidak
  ada konsentrasi background tambahan yang dijumlahkan setelah prediksi.
- `background_source` tetap kosong karena model aktif tidak memakai komponen aditif terpisah.
  Kehadiran GEOS-CF sebagai fitur tidak mengubah arti kolom tersebut.

## Fallback dan confidence

Model atau riwayat wajib yang tidak tersedia memicu IDW **secara independen per polutan**.
IDW fallback memakai p = 2, k = 8 dari `spatial_model/configs/idw_baseline.toml`.
Hasil polutan yang berhasil dipertahankan. `model_metadata` mencatat sumber, versi konkret,
alasan fallback, dan sumber yang digunakan ruas. Konsentrasi tidak tersedia tetap kosong;
nilai kosong tidak boleh diubah menjadi nol.

Confidence aktif bersifat **provisional**, dengan `0.39 × exp(-d/3000)` berdasarkan jarak
sensor dalam meter, lalu diagregasi berbobot panjang ke ruas. Nilai ini indikator heuristik,
bukan probabilitas kebenaran atau ketidakpastian terkalibrasi. Antarmuka pengguna harus
menjelaskan sifat estimasi dan kategori confidence; tidak boleh menyebut adanya penalti AOD.
Verifikasi tampilan frontend berada di ruang lingkup frontend, bukan bukti penyelesaian TI-AI-05.

## Agregasi dan penelusuran wajib

Ruas dipotong pada batas sel dalam EPSG:32748. Konsentrasi per polutan dihitung sebagai
`Σ(length_m × concentration) / Σ(length_m)` atas potongan dengan nilai polutan tersedia.
Indeks paparan dihitung sesudah agregasi. Panjang seluruh potongan dan nilai kosong disimpan
agar hasil dapat dihitung ulang, termasuk ruas yang melewati beberapa sel.

Setiap **penulisan cache baru melalui Worker** wajib menyimpan:

| Rekaman | Isi |
|---|---|
| `pollution.inference_run` | Run ID immutable, awal/akhir window, versi graf dan model, metadata per polutan, fingerprint input, GridSpec, konfigurasi, dan checksum trace |
| `pollution.grid_prediction` | Sel, koordinat, nilai PM2.5/NO₂, confidence, dan sumber per polutan |
| `pollution.edge_cell_set`, `pollution.edge_cell_piece` | Pasangan ruas–sel dan panjang potongan dalam meter, disimpan sekali per isi (`pieces_sha256`) dan dirujuk `inference_run.edge_cell_set_id`; run sebelum November 2026 memakai `pollution.edge_cell` |
| `pollution.edge_metadata` | Setiap `model_metadata` per ruas yang berbeda dalam satu run, dirujuk `edge_pollution.metadata_id` |
| `pollution.edge_pollution.trace_run_id` | Tautan dari EdgeWeight ke run sumbernya |

Writer menolak trace yang hilang atau tidak merekonstruksi nilai ruas. Seluruh trace dan
EdgeWeight ditulis dalam **satu transaksi PostgreSQL**; status `complete` hanya terlihat
sesudah commit. Run ID yang sama tidak boleh menerima trace berbeda. Keluaran Parquet lokal
tetap opsional dan tidak menjadi syarat penelusuran cache. Trace bertahan selama window cache
dipertahankan; retensi menghapus cache dan trace bersama.

Migrasi bersifat aditif. Baris historis tetap dapat dibaca dengan `trace_run_id = null` dan
status trace `legacy_unavailable`; tidak dibuat provenance sintetis untuk data historis.
Pemanggilan ulang pipeline pada window lama tanpa trace menghitung ulang dan melengkapi trace.

## Acceptance criteria TI-AI-05

1. Model terdaftar menghasilkan konsentrasi grid dengan kontrak fitur dan aturan waktu tersedia.
2. Setiap EdgeWeight baru memiliki versi model, window, dan trace sel/panjang yang tersimpan
   atomik serta dapat dibaca ulang tanpa berkas lokal.
3. Sumber dan fallback dibedakan per polutan; nilai kosong tetap dibedakan dari nol.
4. Agregasi menangani ruas multisel dan dapat direkonstruksi dari trace.
5. Cache kompatibel dengan kontrak EdgeWeight dan backend; data historis tetap terbaca.

Trace dapat dibaca melalui
`GET /v1/pollution/edge-weights/{edge_id}/trace?time_window_start=latest`, atau window UTC
tertentu. Endpoint ini read-only. Apply `src/data_worker/database/spatial_migration.sql`
sebelum menjalankan backend/Worker baru pada basis data yang sudah ada.

## Bukti

[provenance_verification.json](experiments/TI-AI-05/provenance_verification.json) mencatat
replay historis lokal dengan model nyata, MLflow/MinIO, dua sel, satu ruas multisel,
cache PostgreSQL/PostGIS, pembacaan backend, dan rekonstruksi saat artefak lokal dimatikan.
Ini bukti integrasi lokal, bukan klaim akurasi seluruh kota atau deployment Railway.

## Lihat juga

- [`docs/PROTOKOL_EVALUASI.md`](PROTOKOL_EVALUASI.md): protokol evaluasi dan status model
- [`docs/experiments/model-comparison-ds-v0.3.0/README.md`](experiments/model-comparison-ds-v0.3.0/README.md): bukti perbandingan model (rekomendasinya IDW p=1)
- [`docs/experiments/model-comparison-ds-v0.3.0/stgnn_idw_residual/ds-v0.3.0_loso_pm25_gpu_seed42_20261014_a7eabc1b/report.md`](experiments/model-comparison-ds-v0.3.0/stgnn_idw_residual/ds-v0.3.0_loso_pm25_gpu_seed42_20261014_a7eabc1b/report.md): ST-GNN PM2.5 LOSO (a7eabc1b)
- [`docs/experiments/model-comparison-ds-v0.3.0/stgnn_idw_residual/ds-v0.3.0_fcst_calendar_gpu_seed42_20261014_847559c6/report.md`](experiments/model-comparison-ds-v0.3.0/stgnn_idw_residual/ds-v0.3.0_fcst_calendar_gpu_seed42_20261014_847559c6/report.md): ST-GNN PM2.5 split tetap (847559c6), checkpoint yang terdaftar
- [`src/data_worker/spatial_model/README.md`](../src/data_worker/spatial_model/README.md): kode training, inferensi, dan cache
- [`src/data_worker/docs/GEOS_CF_FEATURE_SPEC.md`](../src/data_worker/docs/GEOS_CF_FEATURE_SPEC.md): fitur GEOS-CF
- [`src/backend/pollution/README.md`](../src/backend/pollution/README.md): pembaca cache polusi di Cache API
