# Bukti Verifikasi TI-SE-04: Operasi baca-tulis dua cache

Tanggal: 2026-10-13. Lingkungan uji tulis: PostgreSQL 18.6 + PostGIS 3.6.4 lokal
([`../testdb`](../testdb/docker-compose.yml)), versi yang sama dengan Railway. Basis data
Railway (environment `development`, dipakai bersama tim) hanya dibaca.

## Ringkasan per acceptance criteria

| Acceptance criteria | Implementasi | Bukti uji |
|---|---|---|
| Pembacaan graf dan bobot polusi dapat difilter menurut cakupan spasial, versi, moda, dan `TimeWindow` | Graf: `osm.repository.read_road_graph(bbox, mode)`, hanya versi `active`. Bobot: `pollution.repository.read_pollution_weight(bbox, time_window, mode=, graph_version=)` | `test_graph_written_by_worker_is_read_by_backend` (bbox, moda), `test_backend_follows_graph_version_switch` (versi), `test_weights_written_by_worker_are_read_by_backend` (latest, `TimeWindow` tertentu, bbox), `test_weights_filtered_by_mode`, `test_weights_of_an_older_graph_version_are_excluded` |
| *Upsert* tidak menduplikasi rekaman untuk kunci serta versi yang sama | Polusi: `write_pollution_weight` (TI-AI-05), `ON CONFLICT (edge_id, time_window_start) DO UPDATE`. Graf: `write_road_graph`, versi tetap; sisa `building` diganti, bukan diduplikasi | `test_upsert_does_not_duplicate` (24 baris dan 1 time window setelah dua kali tulis; nilai terbaru yang terbaca), `test_same_version_is_not_duplicated`, `test_edge_id_reused_from_another_version_fails`, uji tim AI `test_ut_ops_02a_b_*` |
| Cache kosong, data kedaluwarsa, dan kegagalan basis data mengembalikan status yang dapat dibedakan | 503 `cache_unavailable` dengan `reason` `cache_empty` / `database_unavailable` / `query_timeout`; kedaluwarsa tetap 200 dengan `data_stale: true`; time window tertentu yang tidak ada 404 | `test_empty_cache_is_cache_empty`, `test_stale_window_is_flagged`, `test_database_failures_have_their_own_reason`, `test_cache_unavailable_reasons` (API) |
| Uji integrasi membuktikan alur tulis worker/AI lalu baca backend | Penulis: `osm_cache.write_road_graph` dan `spatial_model.inference.cache.write_pollution_weight` (kode Data Worker). Pembaca: repository Backend dan HTTP API | `tests/test_cache_flow.py` (10 uji), termasuk `test_http_api_reads_what_the_worker_wrote` |

## Hasil uji

| Paket | Perintah | Hasil |
|---|---|---|
| `src/backend/pollution` | `TEST_DATABASE_URL=… pytest` | **30 passed** (20 unit + 10 alur tulis → baca) |
| `src/backend/osm` | `pytest` | **109 passed** (94 unit + 15 integrasi baca-saja ke Railway) |
| `src/data_worker` | `TEST_DATABASE_URL=… pytest tests/test_osm_cache_writer.py` | **12 passed** (UT-OPS-04a/b, IT-K-09, galat saat aktivasi, versi ganda, tiga lapis perlindungan `edge_id`) |
| `src/data_worker` (tim AI) | `TEST_DATABASE_URL=… pytest tests/test_spatial_edgeweight.py -k "ut_ops or kompatibilitas or retensi"` | **5 passed** di PostgreSQL 18 (sebelumnya diuji di 16) |
| Container Cache API | `docker run --rm sippolusi-backend-osm-test` | `osm` 94 passed, 15 skipped; `pollution` 20 passed, 10 skipped |

Uji alur di `tests/test_cache_flow.py`:

| Uji | Yang dibuktikan |
|---|---|
| `test_graph_written_by_worker_is_read_by_backend` | Graf tulisan worker terbaca Backend; walk 24 ruas, bike 23 (footway walk-only tersaring); bbox memotong hasil |
| `test_backend_follows_graph_version_switch` | Setelah versi baru `active`, Backend membaca versi baru |
| `test_empty_cache_is_cache_empty` | Cache kosong dan time window `writing` menghasilkan `cache_empty`; time window yang tidak ada menghasilkan `NotFound` |
| `test_weights_written_by_worker_are_read_by_backend` | `latest` = time window `complete` terakhir; `TimeWindow` tertentu terbaca; nilai, sumber, dan versi tertelusuri |
| `test_upsert_does_not_duplicate` | Tulis ulang kunci yang sama tidak menduplikasi; nilai terbaru menggantikan nilai lama |
| `test_weights_filtered_by_mode` | Bobot ruas yang tidak boleh dilalui sepeda tersaring (`not_allowed_for_mode`) |
| `test_weights_of_an_older_graph_version_are_excluded` | Bobot graf lama tidak dipasang ke graf aktif (`other_graph_version`); tetap dapat dibaca dengan `graph_version=all` atau nama versi |
| `test_stale_window_is_flagged` | Umur > 3 jam sejak akhir time window menghasilkan `data_stale: true`, dan data tetap dikembalikan |
| `test_database_failures_have_their_own_reason` | Port tertutup menghasilkan `database_unavailable`; `pg_sleep` melewati `statement_timeout` menghasilkan `query_timeout` |
| `test_http_api_reads_what_the_worker_wrote` | `/v1/osm/version`, `/v1/pollution/edge-weights`, `/v1/pollution/windows`, `/health` lewat HTTP setelah worker menulis |

## Skema cache

Struktur di Railway per 2026-10-13. Salinan DDL-nya:
[`src/data_worker/osm_cache/schema.sql`](../../data_worker/osm_cache/schema.sql).

### Schema `osm` (OSM Cache DB)

| Tabel | Kunci utama | Index lain | Constraint |
|---|---|---|---|
| `graph_version` | `version` | `graph_version_single_active_idx`: UNIQUE `(status)` WHERE `status = 'active'` | CHECK `status` ∈ {building, active, retired} |
| `road_node` | `(node_id, graph_version)` | `idx_road_node_geom` GiST `(geom)` | – |
| `road_edge` | `(edge_id, graph_version)` | GiST `(geom)`; btree `(graph_version, u)`, `(graph_version, v)`, `(osm_way_id)`; GIN trigram `(name)` | – |

### Schema `pollution` (Spatial Pollution Cache DB)

| Tabel | Kunci utama | Index lain | Constraint |
|---|---|---|---|
| `edge_pollution` | `(edge_id, time_window_start)`, kunci idempotensi *upsert* | `idx_edge_pollution_geom` GiST `(geom)` | CHECK `background_source` ∈ {aod, reanalysis}; `estimation_source` ∈ {stgnn, idw} |
| `pollution_window` | `time_window_start` | – | CHECK `status` ∈ {writing, complete} |

### Kunci, index, dan aturan versi

| Kunci / index | Dipakai untuk |
|---|---|
| `graph_version.status` + index unik parsial | Menjamin paling banyak satu versi graf `active`. `write_road_graph` mengubah yang lama menjadi `retired` lebih dulu, lalu mengaktifkan yang baru, dalam satu transaksi. |
| `road_edge (edge_id, graph_version)` + usulan UNIQUE `(edge_id)` | `edge_id` harus unik **global**, karena `edge_pollution` hanya merujuk `edge_id`. `write_road_graph` menjaganya berlapis: memeriksa sebelum menulis; menangkap pelanggaran unique index saat COPY; dan memeriksa lagi setelah menulis untuk database tanpa unique index. Sejak 2026-10-13 basis data Railway juga menjaganya lewat unique index `road_edge_edge_id_key`. |
| `road_* (graph_version, …)`, GiST `geom` | Pembacaan graf per versi aktif dan per bbox (`&&`). |
| `edge_pollution (edge_id, time_window_start)` | `INSERT … ON CONFLICT DO UPDATE`: menulis ulang time window yang sama memperbarui baris, tidak menduplikasi. |
| `edge_pollution.graph_version`, `.model_version`, `.estimation_source`, `.background_source`, `.written_at` | Ketertelusuran setiap estimasi: graf, model, sumber, dan waktu tulis. |
| `pollution_window.status` | `writing` selama transaksi tulis berjalan dan `complete` setelah selesai. Backend hanya membaca `complete`. |
| `pollution_window.time_window_start` | Pemilihan `latest` dan `TimeWindow` tertentu. |
| GiST `edge_pollution.geom` | Filter bbox bobot polusi (`&&`, sama dengan subgraf jalan; lihat D-01 TI-SE-10). |

Aturan kedaluwarsa dan retensi:
- `data_stale = true` bila time window berakhir lebih dari `POLLUTION_STALE_AFTER_S` (3 jam) lalu.
- Penulis menyimpan `DOWNSCALE_KEEP_WINDOWS` (default 6) time window `complete` terakhir
  (`prune_pollution_windows`, TI-AI-05).

**Rekomendasi untuk pengelola basis data.** `edge_pollution` belum punya index btree pada
`time_window_start`. Dengan 6 time window × ±372 ribu ruas, filter bbox lewat GiST ikut
membaca baris dari semua time window, dan `prune_pollution_windows` menghapus
`WHERE time_window_start = ANY(…)` tanpa index. Usulan:
`CREATE INDEX CONCURRENTLY idx_edge_pollution_time_window ON pollution.edge_pollution (time_window_start);`.
Ini belum mendesak selama tabel masih kosong.

## Contoh pembacaan dengan `TimeWindow`

Diambil dari run nyata. Graf 4 × 4 dan dua time window ditulis oleh kode Data Worker ke
basis data lokal, lalu dibaca lewat HTTP API Backend. Daftar `weights` dipotong.

**1. Sebelum ada time window `complete`:**

```
GET /v1/pollution/edge-weights?bbox=106.79,-6.21,106.81,-6.19
-> 503
{"error": {"code": "cache_unavailable",
           "message": "Spatial Pollution Cache DB belum memiliki time window berstatus complete.",
           "request_id": "…", "reason": "cache_empty"}}
```

**2. Daftar time window setelah worker menulis dua time window:**

```
GET /v1/pollution/windows
-> 200
{"latest_complete": {"time_window_start": "2026-10-13T02:00:00Z", "time_window_end": "2026-10-13T03:00:00Z",
                     "status": "complete", "pollutant": "pm25,no2", "coverage_ratio": 1.0,
                     "model_version": "idw-p2-k8", "completed_at": "2026-10-13T03:35:34.489856Z"},
 "data_stale": false, "age_minutes": 35.6,
 "windows": [{"time_window_start": "2026-10-13T02:00:00Z", …}, {"time_window_start": "2026-10-13T01:00:00Z", …}]}
```

**3. `TimeWindow` tertentu, moda sepeda:**

```
GET /v1/pollution/edge-weights?bbox=106.79,-6.21,106.81,-6.19&time_window_start=2026-10-13T01:00:00Z&mode=bike
-> 200
{"time_window_start": "2026-10-13T01:00:00Z", "time_window_end": "2026-10-13T02:00:00Z",
 "model_version": "idw-p2-k8", "coverage_ratio": 1.0, "graph_version": "jakarta-20261001",
 "mode": "bike", "bbox": [106.79, -6.21, 106.81, -6.19],
 "data_stale": false, "age_minutes": 95.6, "count": 23,
 "excluded": {"not_allowed_for_mode": 1},
 "weights": [{"edge_id": 2, "pm25_ugm3": 30.0, "no2_ugm3": 40.0, "exposure_index": 1.8,
              "confidence_score": 0.3, "background_source": null, "estimation_source": "idw",
              "graph_version": "jakarta-20261001", "model_version": "idw-p2-k8"}, …]}
```

Nilai PM2.5 30,0 adalah isi time window 01:00. Time window 02:00 berisi 20,0, sehingga
pemilihan `TimeWindow` terbukti tidak tertukar dengan `latest`. Satu ruas footway tidak ikut
karena tidak boleh dilalui sepeda.

**4. `TimeWindow` yang tidak ada:**

```
GET /v1/pollution/edge-weights?bbox=…&time_window_start=2026-10-12T01:00:00Z
-> 404
{"error": {"code": "not_found",
           "message": "Time window 2026-10-12T01:00:00Z tidak ada di Spatial Pollution Cache DB.",
           "request_id": "…"}}
```

## Kondisi di Railway (environment `development`, baca-saja, 2026-10-13)

- Schema `pollution` ada dengan kunci, index, dan constraint di atas, tetapi masih **kosong**
  (0 baris, 0 time window).
- Cache API terhadap Railway:
  - `/health` → `"pollution_time_window": null`;
  - `/v1/pollution/windows` → daftar kosong;
  - `/v1/pollution/edge-weights` → 503 `"reason": "cache_empty"`.

  Status ini akan berubah begitu Data Worker menjalankan siklus dengan `DOWNSCALE_WRITE_CACHE=1`.
- Semua aplikasi masih terhubung sebagai superuser `postgres`; role baca-saja `backend_reader`
  (Tabel 3.15) belum dibuat. Sesi Backend dibuka `default_transaction_read_only = on`, yang hanya
  mencegah tulis tidak sengaja dari kode Backend dan **bukan batas keamanan**.
- Server: memori 4 GB, 1 vCPU, `max_connections` 100; volume 4,6 GB (sisa ±4,0 GB).
  Pengambilan data SPKU berjalan sekitar menit ke-15 sampai ke-19 setiap jam.
- Perubahan server 2026-10-13 (restart 04:53 UTC, data dan index utuh):
  - `shared_buffers` dinaikkan dari 128 MB ke 1 GB;
  - `max_parallel_workers_per_gather = 0` dan `max_parallel_maintenance_workers = 0`, karena
    `/dev/shm` hanya 62 MB dan kueri, index, atau VACUUM paralel memakai memori bersama dari
    `/dev/shm`;
  - unique index `osm.road_edge (edge_id)` dibuat.

  Setelah perubahan ini, `python -m osm.verify` tetap LULUS dan 15 tes integrasi baca-saja lulus.
- Script import graf lama (`/root/osm-jakarta/`) sudah tidak ada, kemungkinan terhapus saat
  redeploy 6 Okt 2026. Import graf berikutnya memakai `write_road_graph`.

## Di luar cakupan TI-SE-04

- **TI-SE-05:**
  - memasang bobot ke subgraf (`attach_edge_weights`, isi median dan `missing_edge_count`);
  - `validate_route_request` dengan preferensi α/β.
- **TI-SE-08:** endpoint publik `/v1/exposure-surface` (heatmap).
