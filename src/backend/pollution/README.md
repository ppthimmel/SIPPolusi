# `pollution`: Akses Spatial Pollution Cache DB untuk Backend

Modul baca-saja ke schema `pollution` (estimasi PM2.5/NO₂ per ruas per time window). Modul ini
adalah pasangan dari [`../osm`](../osm/README.md): pool koneksi, skema galat, dan aplikasinya
(`osm.app`) dipakai bersama. Rujukan Dokumen Desain: Tabel 3.3 (`TimeWindow`, `EdgeWeight`),
Tabel 3.11 (`read_pollution_weight`), Tabel 3.15 (`IPollutionWeightRead`), Tabel 3.17 (skema).

Baris ditulis oleh Spatial Downscaling Model lewat
[`src/data_worker/spatial_model/inference/cache.py`](../../data_worker/spatial_model/inference/cache.py)
(`write_pollution_weight`, TI-AI-05). Graf OSM ditulis oleh
[`src/data_worker/osm_cache`](../../data_worker/osm_cache/README.md) (`write_road_graph`).
Bukti TI-SE-04: [VERIFICATION.md](VERIFICATION.md).

## Trace sel sumber

`EdgeWeight.trace_run_id` mengacu ke run yang menyimpan GridSpec, window, versi model,
fingerprint input, prediksi sel, dan panjang potongan ruas–sel di PostgreSQL. Baca melalui
`GET /v1/pollution/edge-weights/{edge_id}/trace?time_window_start=latest` atau window UTC tertentu.
Endpoint mengembalikan `available` beserta manifest/checksum dan sel sumber; baris historis
tanpa trace mengembalikan `legacy_unavailable` dan tetap dapat dibaca melalui endpoint bobot.
Trace tidak bergantung pada direktori artefak Worker. Retensi cache menghapus trace terkait.

Jalankan [migrasi aditif](../../data_worker/database/spatial_migration.sql) sebelum upgrade
backend pada basis data lama. Trace baru dan bobot ditulis atomik oleh Worker; backend tetap
read-only. [Spesifikasi aktif](../../../docs/SPATIAL_MODEL_SPEC.md) menjelaskan kontrak GEOS-CF
dan batas interpretasi confidence.

## Aturan pembacaan

- **Hanya time window berstatus `complete`.** Time window yang masih `writing` tidak pernah
  terbaca, sehingga Backend tidak melihat penulisan setengah jalan.
- **`latest`** berarti time window `complete` terakhir. Time window tertentu dipilih dengan
  `time_window_start` (UTC, tepat di awal jam).
- **Filter yang tersedia:**
  - `bbox` (index GiST pada `geom`);
  - `graph_version` (default `active`, yaitu hanya baris yang diestimasi untuk graf aktif;
    baris dari graf lama tidak dipasang ke `edge_id` baru);
  - `mode` (opsional, hanya ruas yang boleh dilalui `walk`/`bike`).

  Baris yang tersaring dihitung di `excluded` per alasan.
- **`data_stale = true`** bila time window berakhir lebih dari 3 jam lalu
  (`POLLUTION_STALE_AFTER_S`). Datanya tetap dikembalikan (Tabel 3.15).
- **Status yang dapat dibedakan:**

  | Kondisi | Respons |
  |---|---|
  | Data tersedia | 200 |
  | Data kedaluwarsa | 200 dengan `"data_stale": true` |
  | Cache kosong (belum ada time window `complete`) | 503 `cache_unavailable`, `"reason": "cache_empty"` |
  | Time window tertentu masih `writing` | 503 `cache_unavailable`, `"reason": "cache_empty"` |
  | Time window tertentu tidak ada | 404 `not_found` |
  | Basis data tidak dapat dihubungi | 503 `cache_unavailable`, `"reason": "database_unavailable"` |
  | Kueri melewati batas waktu | 503 `cache_unavailable`, `"reason": "query_timeout"` |

## Endpoint (`/v1/pollution`)

| Method | Path | Fungsi |
|---|---|---|
| GET | `/windows?limit=` | Daftar time window (terbaru dulu), time window `complete` terakhir, `data_stale` |
| GET | `/edge-weights?bbox=&time_window_start=&mode=&graph_version=` | `read_pollution_weight`: EdgeWeight satu time window di dalam bbox |

- `time_window_start`: `latest` (default) atau ISO 8601, mis. `2026-10-13T05:00:00Z`.
- `graph_version`: `active` (default), `all` (tanpa filter), atau nama versi.
- `bbox` sama dengan `/v1/osm` (maks. `OSM_MAX_BBOX_KM2`, default 25 km²).

Contoh respons ada di [VERIFICATION.md](VERIFICATION.md). Saat ini cache polusi di Railway
masih kosong, sehingga `/edge-weights` menjawab 503 `cache_empty` sampai Data Worker menulis
time window pertama.

## Konfigurasi

| Variabel | Default | Keterangan |
|---|---|---|
| `POLLUTION_STALE_AFTER_S` | `10800` | Batas umur time window sebelum `data_stale = true` |

Variabel koneksi (`DATABASE_URL`, `OSM_STATEMENT_TIMEOUT_MS`, dll.) sama dengan modul `osm`.

## Menjalankan

Modul ini ikut berjalan di Cache API. Cara menjalankannya, baik lokal, Docker, maupun Railway,
sama dengan [`../osm/README.md`](../osm/README.md#menjalankan-dan-menguji). Endpoint
`/v1/pollution` muncul di `http://localhost:8001/docs` lengkap dengan contoh input.

## Menguji

Ada dua jenis tes:

- **Unit tes** (`test_models.py`, `test_api.py`): tanpa database.
- **Uji alur tulis → baca** (`test_cache_flow.py`): menulis dengan kode Data Worker
  (`write_road_graph`, `write_pollution_weight`), lalu membaca dengan Backend (repository dan
  HTTP API). Uji ini **menulis** ke basis data, jadi hanya dijalankan ke PostgreSQL + PostGIS
  **lokal** ([`../testdb`](../testdb/docker-compose.yml)). Host selain localhost ditolak. Setiap
  uji membuat basis data baru dan menghapusnya setelah selesai.

Dari root repo:

1. Nyalakan basis data uji lokal (PostgreSQL 18 + PostGIS 3.6, sama dengan Railway):

   ```bash
   docker compose -f src/backend/testdb/docker-compose.yml up -d
   ```

2. Pasang dependensi tes. Dengan venv dari README `osm` yang sudah aktif, dari `src/backend`:

   ```bash
   pip install -r pollution/requirements-dev.txt
   ```

3. Jalankan tes dari `src/backend/pollution`:

   ```bash
   cd pollution
   pytest -m "not cacheflow"                                                    # unit → 20 passed
   TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test pytest         # semua → 30 passed
   ```

   Tanpa `TEST_DATABASE_URL`, 10 uji alur dilewati (`20 passed, 10 skipped`).

4. Tes penulis graf di Data Worker (dari `src/data_worker`, dengan dependensi yang sama):

   ```bash
   TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test pytest tests/test_osm_cache_writer.py   # → 12 passed
   ```

5. Matikan basis data uji. Datanya ada di tmpfs, jadi ikut hilang:

   ```bash
   docker compose -f src/backend/testdb/docker-compose.yml down
   ```

Di Mac Apple Silicon, image `postgis/postgis` berjalan lewat emulasi amd64
(`platform: linux/amd64`). Start pertama butuh beberapa detik lebih lama.
