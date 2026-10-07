# Spatial Downscaling Model: estimasi grid sampai EdgeWeight

Fase background Gambar 3.7 B16–B27 (Dokumen Desain Tabel 3.8): estimasi
konsentrasi PM2.5 dan NO2 per sel grid 100 m, confidence score, agregasi ke
ruas jalan, dan penulisan `EdgeWeight` ke Spatial Pollution Cache DB
(`pollution.edge_pollution`). Baseline dan evaluasinya ada di
[`baseline/`](baseline/README.md) (TI-AI-04). Bukti verifikasi TI-AI-05 ada di
[`docs/experiments/TI-AI-05`](../../../docs/experiments/TI-AI-05).

## Susunan

| Berkas | Isi |
|---|---|
| `grid.py` | `GridSpec`: grid reguler 100 m pada EPSG:32748 |
| `idw.py` | `run_idw_fallback()`: IDW yang sama dengan baseline TI-AI-04 |
| `confidence.py` | `estimate_confidence()`, `confidence_distribution()` |
| `aggregate.py` | `edge_cell_pieces()`, `aggregate_grid_to_edges()` |
| `cache.py` | `write_pollution_weight()`, `read_pollution_weight()`, pembacaan graf aktif dan geometri ruas |
| `inference.py` | `run_downscale_inference()`: ST-GNN → fallback IDW → confidence → agregasi → cache → `RunSummary` |
| `__main__.py` | `python -m spatial_model infer`: satu time window secara manual |

## Alur satu time window

1. **ST-GNN** (`predict_concentration`). Artefak ST-GNN dan FeatureMatrix belum
   ada (TI-AI-02, TI-AI-09), sehingga langkah ini selalu berakhir dengan
   `ModelUnavailableError` dan alur jatuh ke fallback, sesuai fragmen alt
   B23/B24 dan Tabel 3.12.
2. **Fallback IDW** dari ground truth time window berjalan. Ground truth dibaca
   dengan `load_ground_truth_window()`, dengan kendali mutu dan parameter IDW
   (power 2, neighbors 8) yang dibaca dari konfigurasi baseline
   `baseline/configs/idw_baseline.toml`. Baseline dan fallback memakai
   implementasi dan aturan yang sama.
3. **Confidence score** per sel (lihat bagian berikut).
4. **Agregasi ke ruas** (lihat bagian berikut).
5. **Cache**: `write_pollution_weight()` dalam satu transaksi.
6. **`RunSummary`**: status, rasio cakupan, sebaran confidence score, versi
   model, `estimation_source`, alasan fallback, dan durasi.

`run_id` ditentukan dari (time_window_start, model_version). Time window yang
sudah `complete` dengan versi model yang sama dilewati (`status = "skipped"`),
sehingga pemanggilan ulang tidak menulis dua kali (UT-SDM-01b, IT-K-07).

## Definisi agregasi grid ke ruas

Setiap segmen garis ruas dipotong tepat pada batas sel grid (EPSG:32748),
tanpa densifikasi. Nilai ruas e:

```
c_e = Σ_k (ℓ_ek · c_k) / Σ_k ℓ_ek
```

ℓ_ek adalah panjang potongan ruas e di sel k (meter). Aturannya:

- PM2.5, NO2, confidence score, dan jarak ke stasiun diagregasi sendiri-sendiri.
- Sel yang nilai polutannya kosong tidak ikut dalam pembagi polutan itu.
- Indeks paparan gabungan dihitung setelah agregasi dari polutan yang tersedia:
  rata-rata PM2.5/15 dan NO2/25 (PF-06). Bila satu polutan kosong, indeksnya
  hanya dari polutan lain, bukan setengahnya.
- Potongan di luar grid diabaikan. Ruas yang seluruhnya di luar grid tidak
  menghasilkan `EdgeWeight` dan dicatat di `edges_without_estimate`
  (UT-SDM-07e).
- Ruas yang melintasi banyak sel menerima bobot panjang dari setiap sel
  (UT-SDM-07b; ruas terpanjang graf saat ini, 4,9 km, melintasi 66 sel).

Tabel potongan (`edge_id`, `cell_id`, `length_m`) dihitung sekali per
(versi graf, `GridSpec`) dan disimpan sebagai `edge_cell_pieces.parquet`.

## Confidence score

Desain menetapkan sifat confidence score, bukan rumusnya: nilai pada [0, 1],
menurun seiring jarak ke sensor darat terdekat, dan lebih rendah pada zona
tanpa AOD (PF-12). Rumus provisional sampai kalibrasi PF-12 tersedia:

```
c = c_max · exp(−d / L) · (0,7 bila sel ST-GNN tanpa AOD)
```

- d adalah jarak pusat sel ke sensor darat terdekat.
- L = 3000 m. Pada baseline TI-AI-04, RMSE PM2.5 naik tajam ketika stasiun
  terdekat berjarak 3–5 km.
- c_max = 1 untuk ST-GNN dan **0,39 untuk IDW**. Desain meminta IDW "paling
  tinggi 0,4" sekaligus "seluruhnya berkategori keyakinan rendah", padahal
  kategori rendah didefinisikan < 0,4 (0,40 sudah "sedang"). Nilai 0,39
  memenuhi keduanya.
- Nilai per ruas adalah rata-rata berbobot panjang dari nilai sel.

## Penelusuran EdgeWeight

| Pertanyaan | Jawaban |
|---|---|
| Model apa? | `edge_pollution.model_version` (mis. `idw-p2-k8`) dan `estimation_source` (`stgnn` atau `idw`) |
| Input waktu apa? | `edge_pollution.time_window_start`; `ground_truth.parquet` dan hash-nya pada manifest run |
| Graf apa? | `edge_pollution.graph_version` |
| Sel grid sumber yang mana? | `edge_cell_pieces.parquet` (`edge_id` → `cell_id`, `length_m`) dan `grid_prediction.parquet` (nilai setiap sel) |
| Fallback atau data lengkap? | `estimation_source = "idw"`, confidence score < 0,4, `background_source` kosong, dan `fallback_reason` pada `RunSummary` |

Artefak per run ditulis ke `<artifact_dir>/<run_id>/` beserta
`run_manifest.json` yang memuat SHA-256 setiap berkas, GridSpec, versi model,
parameter IDW, dan commit kode. Run ditemukan dari baris cache lewat kunci
(time_window_start, model_version).

## Spatial Pollution Cache DB

- **`write_pollution_weight()`** berjalan dalam satu transaksi:
  1. `pollution_window` ditandai `writing`;
  2. baris disalin dengan `COPY` ke tabel sementara;
  3. `INSERT … ON CONFLICT (edge_id, time_window_start) DO UPDATE`, dengan
     geometri diambil dari `osm.road_edge` di dalam SQL;
  4. baris lama time window yang sama yang tidak termasuk penulisan ini dihapus;
  5. `pollution_window` ditandai `complete`.

  Galat di tengah jalan membatalkan seluruhnya, sehingga time window
  sebelumnya tetap berlaku (UT-OPS-02c, IT-K-08).
- **`read_pollution_weight(bbox, time_window="latest")`** mengembalikan
  `list[EdgeWeight]` dari time window `complete` terakhir yang memotong bbox,
  atau `([], None)` bila belum ada (UT-OPS-01).

## Pada siklus Data Worker

Langkah `trigger_downscale_inference` di `acquisition.py` hanya aktif bila
`DOWNSCALE_WRITE_CACHE=1`; bawaannya `disabled`. Setiap time window menulis
372 ribu baris, sekitar 92 MB termasuk indeks (2,2 GB per hari), sedangkan
volume Railway paket Hobby 5 GB. Tulis ulang dan penghapusan meninggalkan baris
mati sampai autovacuum berjalan, sehingga ukuran sementara dapat mencapai dua
kali lipat. Karena itu, setelah setiap penulisan hanya `DOWNSCALE_KEEP_WINDOWS`
time window complete terakhir yang dipertahankan (bawaan 6, sekitar 0,55 GB dan
paling banyak sekitar 1,1 GB sebelum autovacuum; `prune_pollution_windows`).
Backend memang hanya membaca time window complete terakhir.
`SPATIAL_ARTIFACT_DIR` (opsional) menerima artefak penelusuran. Di Railway,
direktori ini hilang setiap kali kontainer berhenti, kecuali dipasang
Railway Volume atau diganti object storage (TI-DO-03).

## Perbedaan dengan Dokumen Desain

| Butir | Dokumen Desain | Implementasi | Alasan |
|---|---|---|---|
| Sumber estimasi | ST-GNN, IDW bila gagal | Selalu IDW | Artefak ST-GNN dan FeatureMatrix belum ada |
| Suku latar | AOD atau reanalysis (`background_source`) | Kosong | IDW tidak memakai suku latar; konektor makro belum ada |
| Confidence score | Bobot dari galat per zona (PF-12) | Rumus provisional di atas | Kalibrasi PF-12 belum dilakukan |
| Batas confidence IDW | Paling tinggi 0,4 | 0,39 | Agar tetap berkategori rendah (< 0,4) |
| `EdgeWeight` | Tabel 3.3 tanpa `estimation_source` | Ditambah `estimation_source` | Kolom ini ada di `edge_pollution` dan dibutuhkan untuk membedakan fallback |
| Pemanggilan inferensi | HTTP asinkron `IDownscaleInference` | Panggilan fungsi di dalam Data Worker | Layanan inferensi terpisah belum ada; kontrak `RunSummary` sama |
| Artefak penelusuran | Feature dan Dataset Store (object storage) | Direktori lokal | Object storage belum disediakan (TI-DO-03) |
