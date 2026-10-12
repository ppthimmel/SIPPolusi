# `osm`: Akses OSM Cache DB untuk Backend

Modul baca-saja ke schema `osm` (jaringan jalan DKI Jakarta). Modul ini mengikuti
skema database yang sebenarnya (`handoff_database.md`) dan Dokumen Desain:

- Tabel 3.3: struktur data;
- Tabel 3.6: `compute_request_bbox`, `snap_to_graph`, `assemble_request_subgraph`;
- Tabel 3.11: `read_road_graph`;
- Tabel 3.15: kontrak `IRoadGraphRead`;
- Tabel 3.16: kode galat.

Modul ini berdiri sendiri dan **tidak memakai** `main.py`, `database/`, atau `route_model/`.

## Menjalankan

Dari `src/backend`:

```bash
pip install -r osm/requirements-dev.txt
uvicorn osm.app:app --reload --port 8001
```

Dokumentasi interaktif tersedia di `http://localhost:8001/docs`.

### Docker

Image berdiri sendiri dan tidak memakai `src/backend/Dockerfile`. Konteks build-nya adalah
direktori `osm/`. File `.env` tidak ikut masuk image; konfigurasi diberikan lewat environment.

```bash
# dari root repo: build dan jalankan di http://localhost:8001 (DATABASE_URL dari src/backend/.env)
docker compose -f src/backend/osm/docker-compose.yml up --build

# atau tanpa compose
docker build -t sippolusi-backend-osm src/backend/osm
docker run --rm -p 8001:8000 --env-file src/backend/.env sippolusi-backend-osm

# tes di container (Python 3.11); tes integrasi ikut jalan bila DATABASE_URL diberikan
docker build --target test -t sippolusi-backend-osm-test src/backend/osm
docker run --rm sippolusi-backend-osm-test
```

Image runtime memakai `python:3.11-slim` dan berjalan sebagai user non-root. Image ini
punya `HEALTHCHECK` ke `/health`, dan mendengarkan pada `PORT` (default 8000). Di Railway,
pakai Dockerfile ini dengan root directory `src/backend/osm`, lalu isi variabel `DATABASE_URL`
dengan URL privat (`postgres.railway.internal`).

## Konfigurasi

Konfigurasi dibaca dari environment, lalu dari `src/backend/.env`.

| Variabel | Default | Keterangan |
|---|---|---|
| `DATABASE_URL` | wajib | `postgresql://...`. Bentuk `postgresql+asyncpg://` juga diterima. Di Railway gunakan URL privat `postgres.railway.internal`. |
| `OSM_STATEMENT_TIMEOUT_MS` | `1500` | `statement_timeout` per kueri di server (Tabel 3.15). |
| `OSM_COMMAND_TIMEOUT_S` | `10` | Batas waktu di sisi klien, termasuk transfer hasil. |
| `OSM_CONNECT_TIMEOUT_S` | `5` | Batas waktu membuka koneksi. |
| `OSM_POOL_MIN_SIZE` / `OSM_POOL_MAX_SIZE` | `2` / `10` | Ukuran pool asyncpg. |
| `OSM_VERSION_CACHE_TTL_S` | `60` | Lama cache versi graf aktif. |
| `OSM_SNAP_MAX_DISTANCE_M` | `200` | Jarak terjauh titik ke simpul (`snap_to_graph`). |
| `OSM_MAX_BBOX_KM2` | `25` | Luas bbox terbesar untuk `/graph` dan `/roads`. |
| `OSM_STUDY_AREA_BBOX` | `106.68,-6.38,106.98,-6.08` | Wilayah studi: daratan DKI Jakarta. Kepulauan Seribu tidak termasuk. |
| `OSM_WALK_SPEED_MPS` / `OSM_BIKE_SPEED_MPS` | `1.3` / `4.2` | Kecepatan `ModeProfile`. |

Setiap sesi database dibuka dengan `default_transaction_read_only = on`, sehingga
Backend tidak bisa menulis ke database walaupun role `backend_reader` belum dibuat.

## Endpoint (`/v1/osm`)

| Method | Path | Fungsi | Rujukan |
|---|---|---|---|
| GET | `/version` | Versi graf aktif | handoff 9.1 |
| GET | `/graph?bbox=&mode=&include_geometry=` | `read_road_graph(bbox, mode)`: simpul dan ruas versi aktif | Tabel 3.11, UT-OPS-03 |
| GET | `/roads?bbox=&mode=&highway=` | Ruas dalam viewport sebagai GeoJSON (`application/geo+json`) | handoff 9.4 |
| GET | `/nearest-node?lon=&lat=&mode=&max_distance_m=` | Simpul terdekat yang dapat dilalui moda | handoff 9.2, UT-BE-05 |
| GET | `/nearest-edge?lon=&lat=&mode=&max_distance_m=` | Snap posisi ke ruas terdekat (`fraction_along`) | handoff 9.3 |
| GET | `/nodes/{node_id}` | Detail simpul beserta tag OSM | |
| GET | `/nodes/{node_id}/neighbors?mode=` | Ruas keluar dari simpul. Jalan kaki mengabaikan `oneway`, sepeda mematuhinya. | handoff 9.5 |
| GET | `/edges/{edge_id}` | Detail ruas, tag, dan geometri | handoff 9.7 |
| GET | `/streets?q=&limit=` | Cari nama jalan | handoff 9.8 |
| POST | `/subgraph` | Bagian OSM dari R8–R10: `compute_request_bbox`, `read_road_graph`, `snap_to_graph`, `assemble_request_subgraph` | Tabel 3.6, UT-BE-04..06 |
| GET | `/health` | Status koneksi database dan versi aktif | |

- **Format data:**
  - `bbox` ditulis `min_lon,min_lat,max_lon,max_lat`.
  - Titik ditulis `{lat, lon}`.
  - Geometri berupa GeoJSON dengan urutan `[lon, lat]`.
- **Galat** memakai skema seragam
  `{"error": {"code", "message", "request_id"}}`. Kode yang dipakai:
  - `invalid_request` (400)
  - `not_found` (404)
  - `outside_study_area` (422)
  - `point_not_on_network` (422)
  - `no_route_found` (422)
  - `cache_unavailable` (503)
- **`X-Request-ID`** diteruskan jika dikirim oleh pemanggil. Jika tidak, dibuat UUID v4 baru.

Jika peta menampilkan data ini, wajib mencantumkan atribusi "© OpenStreetMap contributors"
(<https://www.openstreetmap.org/copyright>).

## Memakai dari kode lain

```python
from osm.app import osm_lifespan                 # membuka pool, mengisi app.state
from osm.errors import install_error_handling   # skema galat + X-Request-ID
from osm.router import router as osm_router

app = FastAPI(lifespan=osm_lifespan)
install_error_handling(app)
app.include_router(osm_router)
```

Untuk `plan_route`, gunakan `OsmRepository.read_road_graph(bbox, mode)` lalu
`osm.graph.assemble_request_subgraph(...)`. Bobot polusi dipasang terpisah oleh
`attach_edge_weights`.

## Pengujian

```bash
cd src/backend/osm
pytest -m "not integration"   # unit test: graf sintetis 5 x 5 dan API dengan repository tiruan
pytest -m integration          # baca-saja ke DB asli via DATABASE_URL; dilewati bila DB tidak terjangkau
```

## Catatan kinerja

Diukur 2026-10-06 dengan `EXPLAIN ANALYZE` di server, mode jalan kaki:

| Perjalanan | Luas bbox | Kueri ruas | Kueri simpul |
|---|---|---|---|
| 5 km | 19 km² | 12 ms | 64 ms |
| 10 km | 75 km² | 23 ms | 110 ms |
| 20 km | 301 km² | 114 ms | 437 ms |

Dari laptop melalui proxy publik Railway, waktu didominasi transfer data
(satu round trip ±220 ms). Bbox ≥ 19 km² bisa melewati `statement_timeout`, karena
Postgres ikut menghitung waktu mengirim baris ke klien yang lambat. Di jaringan
privat Railway hal ini tidak diharapkan terjadi. Untuk pengembangan lokal,
naikkan `OSM_STATEMENT_TIMEOUT_MS` bila perlu.
