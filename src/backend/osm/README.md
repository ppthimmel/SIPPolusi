# `osm`: Akses OSM Cache DB untuk Backend

Modul baca-saja ke schema `osm` (jaringan jalan DKI Jakarta). Modul ini mengikuti
skema database yang sebenarnya (`handoff_database.md`) dan Dokumen Desain:

- Tabel 3.3: struktur data;
- Tabel 3.6: `compute_request_bbox`, `snap_to_graph`, `assemble_request_subgraph`;
- Tabel 3.11: `read_road_graph`;
- Tabel 3.15: kontrak `IRoadGraphRead`;
- Tabel 3.16: kode galat.

Modul ini berdiri sendiri dan **tidak memakai** `main.py`, `database/`, atau `route_model/`.

Aplikasi `osm.app` adalah **Cache API** yang juga menyajikan `/v1/pollution` (Spatial
Pollution Cache DB) dari modul [`../pollution`](../pollution/README.md). Kedua modul memakai
pool koneksi dan skema galat yang sama. Bukti TI-SE-04 ada di
[`../pollution/VERIFICATION.md`](../pollution/VERIFICATION.md).

## Menjalankan dan menguji

Ada tiga cara. **A** dan **B** menjalankan aplikasi di laptop, tetapi datanya tetap dari
database Railway (lewat URL publik). **C** menjalankan aplikasi sebagai service di Railway,
yang terhubung ke database lewat jaringan privat. Database Railway yang dipakai saat ini ada di
environment **`development`** dan dipakai bersama tim (data SPKU masuk setiap jam).

### Persiapan

Isi `src/backend/.env` dengan `DATABASE_URL`, tanpa tanda kutip. Dari laptop, pakai URL
publik Railway (`DATABASE_PUBLIC_URL`). File ini di-ignore git; jangan di-commit.

```
DATABASE_URL=postgresql://<user>:<password>@<host>:<port>/railway
```

Semua perintah di bawah dijalankan dari **root repo**, kecuali disebutkan lain.

### A. Python lokal (Python ≥ 3.11)

1. Buat dan aktifkan virtualenv (nama `venv` sudah di-ignore git), lalu pasang dependensi:

   ```bash
   cd src/backend
   python3 -m venv venv
   source venv/bin/activate          # ulangi setiap membuka terminal baru
   pip install -r osm/requirements-dev.txt
   ```

2. Jalankan tes dari `src/backend/osm`:

   ```bash
   cd osm
   pytest -m "not integration"       # unit tes, tanpa database   → 94 passed
   pytest -m integration             # baca-saja ke DB asli       → 15 passed
   pytest                            # keduanya                   → 109 passed
   cd ..
   ```

   Unit tes memakai graf sintetis 5 × 5 dan repository tiruan. Tes integrasi dilewati
   otomatis bila database tidak dapat dihubungi.

3. Buat laporan verifikasi seluruh graf versi aktif, dari `src/backend`. Di bagian atas
   laporan harus tertulis `**Hasil: LULUS**`.

   ```bash
   python -m osm.verify                          # tampilkan di layar
   python -m osm.verify > osm/VERIFICATION.md    # atau perbarui berkas laporan
   ```

4. Jalankan server, dari `src/backend`. Biarkan terminal ini tetap terbuka:

   ```bash
   uvicorn osm.app:app --reload --port 8001
   ```

5. Di **tab terminal lain**, coba endpoint (hasil yang diharapkan ada di bawah). Semua endpoint
   juga bisa dicoba lewat browser di <http://localhost:8001/docs> dengan tombol **Try it out**.

   ```bash
   curl -s http://localhost:8001/health
   curl -s "http://localhost:8001/v1/osm/nearest-node?lon=106.8272&lat=-6.1754&mode=walk"
   curl -s "http://localhost:8001/v1/osm/streets?q=sudirman&limit=3"
   curl -s -X POST http://localhost:8001/v1/osm/subgraph -H "Content-Type: application/json" \
        -d '{"origin":{"lat":-6.1754,"lon":106.8272},"destination":{"lat":-6.1950,"lon":106.8230},"mode":"walk"}'
   curl -s "http://localhost:8001/v1/osm/nearest-node?lon=106.55&lat=-5.75&mode=walk"
   ```

6. Untuk berhenti, tekan **Ctrl+C** di terminal server, lalu jalankan `deactivate`.

### B. Docker lokal

Image ini berdiri sendiri dan tidak memakai `src/backend/Dockerfile`. Konteks build-nya adalah
`src/backend` (agar `osm/` dan `pollution/` ikut), dan `osm/Dockerfile.dockerignore` membatasi
berkas yang dikirim hanya ke kedua paket itu. Image runtime memakai `python:3.11-slim`, berjalan sebagai user non-root, punya
`HEALTHCHECK` ke `/health`, dan mendengarkan pada `PORT` (default 8000). File `.env` tidak ikut
masuk image; konfigurasi diberikan lewat environment. Pastikan Docker Desktop sudah menyala.

Semua perintah Docker di bawah memakai path dari **root repo**. Kalau terminal masih di
`src/backend` (misalnya setelah langkah A), kembali ke root dulu dengan `cd ../..`.

1. Build dan jalankan di <http://localhost:8001>:

   ```bash
   docker compose -f src/backend/osm/docker-compose.yml up --build -d
   docker compose -f src/backend/osm/docker-compose.yml ps        # tunggu sampai (healthy)
   ```

2. Coba endpoint dengan perintah `curl` yang sama seperti A-5, atau buka <http://localhost:8001/docs>.
   Untuk melihat log:

   ```bash
   docker compose -f src/backend/osm/docker-compose.yml logs -f
   ```

3. Jalankan tes di container (Python 3.11, sama dengan produksi):

   ```bash
   docker build -f src/backend/osm/Dockerfile --target test -t sippolusi-backend-osm-test src/backend
   docker run --rm --env-file src/backend/.env sippolusi-backend-osm-test
   docker run --rm sippolusi-backend-osm-test   # tanpa DB
   ```

   Container menjalankan tes `osm` lalu `pollution`, jadi ada dua baris hasil:

   | Perintah | `osm` | `pollution` |
   |---|---|---|
   | dengan `--env-file` | 109 passed | 20 passed, 10 skipped |
   | tanpa DB | 94 passed, 15 skipped | 20 passed, 10 skipped |

   Sepuluh tes `pollution` yang dilewati adalah uji alur tulis → baca; uji ini butuh database
   uji lokal dan kode Data Worker, jadi dijalankan di luar container (lihat
   [`../pollution/README.md`](../pollution/README.md)).

   ```bash
   ```

4. Buat laporan verifikasi lewat image runtime:

   ```bash
   docker run --rm --env-file src/backend/.env sippolusi-backend-osm python -m osm.verify
   ```

5. Matikan container:

   ```bash
   docker compose -f src/backend/osm/docker-compose.yml down
   ```

Tanpa compose, image juga bisa dijalankan langsung:

```bash
docker build -f src/backend/osm/Dockerfile -t sippolusi-backend-osm src/backend
docker run --rm -p 8001:8000 --env-file src/backend/.env sippolusi-backend-osm
```

### Setelah ada perubahan kode

Image Docker menyalin kode saat **build**; container tidak membaca berkas di laptop. Jadi
setiap perubahan kode di `src/backend/osm` atau `src/backend/pollution` perlu langkah berikut sebelum dicoba.

| Cara menjalankan | Perubahan kode `.py` | Perubahan `requirements*.txt` | Perubahan `.env` |
|---|---|---|---|
| A. `uvicorn --reload` | Otomatis dimuat ulang; cukup refresh `/docs` | `pip install -r osm/requirements-dev.txt`, lalu jalankan ulang uvicorn | Jalankan ulang uvicorn |
| B. Docker compose | Build ulang (perintah di bawah) | Build ulang (perintah di bawah) | `up -d` saja, tanpa `--build` |
| C. Railway | Push ke branch; Railway build dan deploy otomatis | Sama | Ubah di tab *Variables*; Railway deploy ulang |

Build ulang dan ganti container yang sedang berjalan (dari root repo). Tidak perlu `down`
lebih dulu; compose mengganti container lama setelah image baru selesai dibangun:

```bash
docker compose -f src/backend/osm/docker-compose.yml up --build -d
```

Lalu periksa bahwa container baru sudah sehat, dan muat ulang `/docs` di browser
(Cmd+Shift+R) supaya contoh terbaru tampil:

```bash
docker compose -f src/backend/osm/docker-compose.yml ps
```

Image tes juga perlu dibangun ulang sebelum tes dijalankan di container:

```bash
docker build -f src/backend/osm/Dockerfile --target test -t sippolusi-backend-osm-test src/backend
docker run --rm --env-file src/backend/.env sippolusi-backend-osm-test
```

Sesudah perubahan kode, urutan pengecekan yang disarankan:
1. `pytest` (cara A) atau tes di container (cara B);
2. `python -m osm.verify` bila perubahan menyentuh pembacaan atau perakitan graf;
3. coba contoh di `/docs`, termasuk edge case (lihat bagian di bawah).

Untuk membersihkan image lama yang menumpuk setelah sering build ulang:

```bash
docker image prune -f
```

### C. Service di Railway

Railway membangun image dari GitHub, jadi kodenya harus sudah di-push ke branch yang dipakai.

1. Di project Railway yang sama dengan database, buat service baru: *New → GitHub Repo →
   SIPPolusi*. Lalu atur:
   - **Branch:** branch yang berisi modul ini.
   - **Root Directory:** `src/backend`, karena image memuat `osm/` dan `pollution/`.
   - **Config-as-code:** Settings → *Config-as-code* → *Railway Config File*
     `/src/backend/osm/railway.json`. Berkas ini memilih `osm/Dockerfile` (relatif terhadap Root
     Directory). Tanpa itu Railway membaca `src/backend/railway.json` milik Backend, yang
     `dockerfilePath`-nya mengalahkan variabel `RAILWAY_DOCKERFILE_PATH` dan isian dashboard,
     sehingga service ini menjalankan Backend, bukan Cache API (terlihat dari log
     `logger="backend"` dan 404 `{"detail": "Not Found"}` untuk `/v1/osm/*`). Tahap terakhir
     Dockerfile adalah image runtime.
2. **Variables:** isi `DATABASE_URL` dengan referensi ke service database, misalnya
   `${{database.DATABASE_URL}}` (sesuaikan nama service). Referensi ini memakai URL privat
   `postgres.railway.internal`. Variabel `PORT` diisi otomatis oleh Railway.
3. **Settings:** set *Healthcheck Path* ke `/health`. Jangan buat domain publik: Cache API hanya
   dijangkau gateway lewat jaringan privat (TI-DO-04, `docs/devops/private-networking.md`).
   Uji dari luar lewat gateway, `https://<gateway>/v1/osm/...`.
4. Uji dari terminal laptop dengan perintah seperti A-5, tetapi alamatnya diganti domain Railway.
   Coba juga perjalanan ±10 km, yang dari laptop kena 503 karena jaringan lambat. Di Railway
   hasilnya seharusnya 200:

   ```bash
   curl -s https://<domain-railway>/health
   curl -s -X POST https://<domain-railway>/v1/osm/subgraph -H "Content-Type: application/json" \
        -d '{"origin":{"lat":-6.1754,"lon":106.8272},"destination":{"lat":-6.2654,"lon":106.8272},"mode":"walk"}'
   ```

API ini belum punya rate limiting; di desain, itu tugas API Gateway. Jadi domain publik
sebaiknya dihapus setelah selesai menguji, atau service ini diakses hanya lewat gateway.

### Contoh input per endpoint (`/docs`)

Setiap endpoint di <http://localhost:8001/docs> sudah punya contoh input. Cara memakainya:

1. Klik endpoint, lalu klik **Try it out**. Kolom input terisi contoh pertama.
2. Untuk mencoba contoh lain, pilih dari dropdown **Examples** di bawah kolom parameter.
   Untuk `POST /subgraph`, dropdown-nya ada di atas kotak *Request body*.
3. Klik **Execute**. Hasilnya muncul di bagian *Responses*, beserta perintah `curl` yang setara.

Semua contoh memakai data `jakarta-20261005` dan sudah diuji ke database asli.

| Endpoint | Contoh (nama di dropdown) | Input | Hasil yang diharapkan |
|---|---|---|---|
| `GET /health` | – | – | 200, `"status":"ok"`, `"database":"ok"` |
| `GET /v1/osm/version` | – | – | 200, `jakarta-20261005`, 299.067 node, 373.352 ruas |
| `GET /v1/osm/graph` | bbox *Sekitar Monas, ±0,2 km²* + mode *Jalan kaki* | `bbox=106.825,-6.177,106.829,-6.173`, `mode=walk` | 200, 182 node, 215 ruas, `"rejected":{}` |
| | bbox yang sama + mode *Sepeda* | `mode=bike` | 200, 24 node, 16 ruas (area taman Monas tertutup untuk sepeda) |
| | bbox *Sekitar Monas, ±1,2 km²* | `bbox=106.822,-6.180,106.832,-6.170` | 200, 911 node, 1.176 ruas (respons ±370 KB) |
| | bbox *Seluruh Jakarta* | `bbox=106.68,-6.38,106.98,-6.08` | 400 `invalid_request`, "bbox terlalu luas (1101.3 km²)" |
| | bbox *Format salah* | `bbox=106.82,-6.18,106.83` | 400 `invalid_request` |
| `GET /v1/osm/roads` | bbox *±0,2 km²* + highway *Jalur pejalan kaki* | `highway=footway&highway=pedestrian` | 200, GeoJSON `FeatureCollection` berisi trotoar/jalur pejalan kaki |
| | bbox *±1,2 km²* + highway *Jalan utama* | `highway=primary&highway=secondary` | 200, ruas primary/secondary di sekitar Monas |
| | bbox *±0,2 km²* + highway *Jalan utama* | | 200, `"features":[]` (tidak ada jalan utama di area sekecil itu) |
| `GET /v1/osm/nearest-node` | Monas + *Jalan kaki* | `lon=106.8272&lat=-6.1754&mode=walk` | 200, `node_id` 10792891204, `distance_m` ±11,7 |
| | Monas + *Sepeda* | `mode=bike` | 200, `node_id` 2624398858, ±198 m (simpul terdekat yang boleh dilalui sepeda) |
| | *ketik manual:* laut utara | `lon=106.86&lat=-6.085` | 422 `point_not_on_network` |
| | *ketik manual:* Kepulauan Seribu | `lon=106.55&lat=-5.75` | 422 `outside_study_area` |
| `GET /v1/osm/nearest-edge` | Monas + *Jalan kaki* | `lon=106.8272&lat=-6.1754&mode=walk` | 200, `edge_id` 321900 (footway), `fraction_along` 1.0 |
| | Monas + *Sepeda* | `mode=bike` | 200, `edge_id` 74292 (pedestrian yang boleh sepeda), ±198 m |
| `GET /v1/osm/nodes/{node_id}` | *Lampu lalu lintas dekat Monas* | `7114808048` | 200, `tags` = `traffic_signals` |
| | *Simpul terdekat Monas* | `10792891204` | 200, `tags` = `{}` |
| | *Tidak ada* | `42` | 404 `not_found` |
| `GET /v1/osm/nodes/{node_id}/neighbors` | *Lampu lalu lintas* + *Jalan kaki* | `7114808048`, `mode=walk` | 200, beberapa ruas Jalan Medan Merdeka Utara (termasuk 215759 dan 215760) |
| | *Lampu lalu lintas* + *Sepeda* | `mode=bike` | 200, hanya ruas 215760; 215759 satu arah dan arah baliknya tertutup untuk sepeda |
| | *Simpul terdekat Monas* + *Jalan kaki* | `10792891204`, `mode=walk` | 200, satu ruas (321900, footway) |
| `GET /v1/osm/edges/{edge_id}` | *Jalan Medan Merdeka Barat* | `87108` | 200, primary, `oneway` true, `tags.lanes` = `"3"`, geometri LineString |
| | *Tidak ada* | `999999999` | 404 `not_found` |
| `GET /v1/osm/streets` | *Sudirman* | `q=sudirman` | 200, Jalan Jenderal Sudirman, 212 segmen, ±10.649 m |
| | *Husni Thamrin* | `q=Husni Thamrin` | 200, Jalan Mohammad Husni Thamrin, 96 segmen, ±3.054 m |
| | *Tanpa huruf* | `q=%%%` | 400 `invalid_request` |
| `POST /v1/osm/subgraph` | *Jalan kaki Monas → Bundaran HI* | lihat di bawah | 200, `origin_node` 10792891204, 3.444 node, 4.282 ruas |
| | *Sepeda Monas → Bundaran HI* | `"mode":"bike"` | 200, 2.621 node, `origin_snap_distance_m` ±199 |
| | *Jalan kaki Monas → Jl. Medan Merdeka Utara (±440 m)* | `buffer_m` 200, `include_graph` true | 200, `destination_node` 7114808048, 262 node, 329 ruas, plus daftar node/ruas di `graph` (±100 KB) |
| | *Tujuan di laut* | tujuan `-6.085, 106.86` | 422 `point_not_on_network` |
| | *Tujuan Kepulauan Seribu* | tujuan `-5.75, 106.55` | 422 `outside_study_area` |

Contoh *request body* `POST /v1/osm/subgraph` (Monas → Bundaran HI):

```json
{
  "origin":      {"lat": -6.1754, "lon": 106.8272},
  "destination": {"lat": -6.1950, "lon": 106.8230},
  "mode": "walk"
}
```

Field opsional: `buffer_m` (margin bbox dalam meter; default max(500 m, 25% jarak)) dan
`include_graph` (`true` untuk menyertakan node dan ruas subgraf). Respons galat selalu
berbentuk `{"error": {"code", "message", "request_id"}}`.

Catatan: `lon` dan `lat` di `/nearest-node` dan `/nearest-edge` masing-masing hanya punya
contoh Monas, karena dropdown keduanya terpisah. Untuk mencoba titik lain, ketik nilainya
langsung, misalnya titik laut atau Kepulauan Seribu dari tabel di atas.

### Edge case yang perlu diuji

Semua kasus di bawah sudah dicoba ke database asli (`jakarta-20261005`), dan sebagian besar
juga dicakup tes otomatis (`pytest`). Kolom **Cara uji** menyebut nama contoh di dropdown **Examples** pada `/docs`.
Kasus yang tidak bisa dibuat lewat `/docs` diberi perintah `curl`. Semua galat berbentuk
`{"error": {"code", "message", "request_id"}}`.

**Validasi input (400 `invalid_request`)**

| Kasus | Endpoint | Cara uji | Hasil |
|---|---|---|---|
| bbox hanya 3 angka | `/graph`, `/roads` | bbox *Hanya 3 angka* | 400 |
| bbox min > max | `/graph`, `/roads` | bbox *min > max* | 400 |
| bbox bukan angka | `/graph`, `/roads` | bbox *Bukan angka* | 400 |
| bbox > 25 km² | `/graph`, `/roads` | bbox *Seluruh Jakarta* | 400, "bbox terlalu luas (1101.3 km²)" |
| `highway` tidak dikenal | `/roads` | highway *Nilai tidak dikenal* | 400, "Nilai highway tidak dikenal: rocket" |
| `mode` tidak dikenal | semua yang punya `mode` | ketik `car`, atau body *mode "car"* | 400 |
| Parameter wajib hilang | `/nearest-node` | `curl "localhost:8001/v1/osm/nearest-node?lon=106.8272&mode=walk"` | 400, "lat: Field required" |
| `lat` bukan angka | `/nearest-node` | ketik `lat=abc` | 400 |
| lon/lat tertukar (lat 106) | `/subgraph` | body *lat 106 (lon/lat tertukar)* | 400, "origin.lat: … ≤ 90" |
| `max_distance_m` > 1000 | `/nearest-node`, `/nearest-edge` | max_distance_m *1001 m* | 400 |
| `node_id` 0 atau negatif | `/nodes/{id}`, `/edges/{id}` | node_id *0*, edge_id *Negatif* | 400 |
| ID melebihi batas `bigint` | `/nodes/{id}`, `/edges/{id}` | *> batas bigint* | 400 (sebelumnya 500, sudah diperbaiki) |
| `q` < 3 karakter | `/streets` | q *2 karakter* | 400 |
| `q` tanpa 3 huruf/angka berurutan | `/streets` | q *Tanpa huruf* (`%%%`), *Hanya spasi* | 400 |
| `limit` 0 atau 21 | `/streets` | limit *0*, *21* | 400 |
| `buffer_m` > 10000 | `/subgraph` | body *buffer_m > 10000* | 400 |
| Field body hilang | `/subgraph` | body *Tanpa destination* | 400, "destination: Field required" |
| JSON rusak | `/subgraph` | `curl -X POST localhost:8001/v1/osm/subgraph -H "Content-Type: application/json" -d '{"origin":'` | 400, "JSON decode error" |

**Wilayah studi dan jaringan jalan (422)**

| Kasus | Endpoint | Cara uji | Hasil |
|---|---|---|---|
| Tujuan di Kepulauan Seribu | `/subgraph` | body *Tujuan Kepulauan Seribu* | 422 `outside_study_area`, "Titik tujuan …" |
| Asal di Kepulauan Seribu | `/subgraph` | body *Asal Kepulauan Seribu* | 422 `outside_study_area`, "Titik asal …" |
| Titik di luar wilayah studi | `/nearest-node`, `/nearest-edge` | ketik `lon=106.55`, `lat=-5.75` | 422 `outside_study_area` |
| Titik di laut, dalam wilayah studi | `/subgraph`, `/nearest-node` | body *Tujuan di laut*, atau ketik `lon=106.86`, `lat=-6.085` | 422 `point_not_on_network` |
| Radius terlalu kecil | `/nearest-node`, `/nearest-edge` | max_distance_m *5 m* (simpul terdekat 11,7 m) | 422 `point_not_on_network` |
| Tujuan di komponen jalan terisolasi | `/subgraph` | body *Tujuan di komponen jalan terisolasi* (node 3776134358, komponen 18 simpul) | 422 `no_route_found` |
| `buffer_m` terlalu kecil memutus jalur | `/subgraph` | body *buffer_m 100 memutus jalur*; tanpa `buffer_m` pasangan titik yang sama terhubung | 422 `no_route_found` |
| Di luar DKI tetapi dalam kotak wilayah studi (Tangerang) | `/subgraph` | body *Tangerang, di dalam kotak wilayah studi* | 422 `point_not_on_network`, bukan `outside_study_area` (keterbatasan: wilayah studi masih berupa kotak) |

**Kasus batas yang berhasil (200)**

| Kasus | Endpoint | Cara uji | Hasil |
|---|---|---|---|
| Asal = tujuan | `/subgraph` | body *Asal = tujuan* | 200, `origin_node` = `destination_node` |
| bbox tanpa jalan (laut) | `/graph`, `/roads` | bbox *Di laut, tanpa jalan* | 200, 0 node/ruas atau `"features":[]` |
| Filter yang tidak cocok | `/roads` | bbox *±0,2 km²* + highway *Jalan utama* | 200, `"features":[]` |
| Radius batas atas | `/nearest-node` | max_distance_m *1000 m* | 200 |
| Perbedaan moda: area pejalan kaki | `/graph`, `/nearest-node` | mode *Jalan kaki* vs *Sepeda* di Monas | walk 182 node vs bike 24 node; simpul sepeda terdekat ±198 m |
| Perbedaan moda: jalan satu arah | `/nodes/{id}/neighbors` | node *Lampu lalu lintas* + walk vs bike | walk: 215759 dan 215760; bike: hanya 215760 |
| Simpul tanpa tetangga untuk sepeda | `/nodes/{id}/neighbors` | node *Simpul terdekat Monas* + *Sepeda* | 200, `"neighbors":[]` |
| Pencarian tidak peka huruf besar-kecil | `/streets` | q *Huruf besar* (`SUDIRMAN`) | sama dengan `sudirman` |
| Spasi di tepi kata kunci | `/streets` | q *Spasi di tepi dipangkas* | `"query":"sudirman"` |
| `%`/`_` dianggap teks biasa | `/streets` | q *% diperlakukan sebagai teks* (`sudirman%`) | 200, `"results":[]` |
| Nama tidak ditemukan | `/streets` | q *Tidak ada jalan bernama ini* | 200, `"results":[]` |
| ID tidak ada | `/nodes/{id}`, `/edges/{id}` | *Tidak ada* | 404 `not_found` |

**Infrastruktur dan protokol**

Kasus ini diuji dengan `curl` atau dengan menjalankan server memakai konfigurasi khusus.
Jalankan dari `src/backend` dengan venv aktif. Variabel environment di depan perintah
mengalahkan nilai di `.env`.

| Kasus | Cara uji | Hasil |
|---|---|---|
| `X-Request-ID` diteruskan | `curl -i -H "X-Request-ID: uji-123" localhost:8001/v1/osm/version` | header `x-request-id: uji-123` |
| `X-Request-ID` tidak valid | `curl -i -H "X-Request-ID: ada spasi" localhost:8001/v1/osm/version` | diganti UUID v4 baru |
| Path tidak ada | `curl localhost:8001/v1/osm/tidak-ada` | 404 `not_found` |
| Method salah | `curl -X POST localhost:8001/v1/osm/version` | 405 `method_not_allowed` |
| Database tidak dapat dihubungi | `DATABASE_URL=postgresql://nobody:x@127.0.0.1:1/none uvicorn osm.app:app --port 8002`, lalu `curl localhost:8002/health` dan `curl localhost:8002/v1/osm/version` | `/health`: `"status":"degraded"`, `"database":"unavailable"`; endpoint lain: 503 `cache_unavailable`, `"reason":"database_unavailable"` |
| Kueri melewati `statement_timeout` | `OSM_STATEMENT_TIMEOUT_MS=1 uvicorn osm.app:app --port 8003`, lalu `curl "localhost:8003/v1/osm/graph?bbox=106.822,-6.180,106.832,-6.170&mode=walk"` | 503 `cache_unavailable`, `"reason":"query_timeout"`, "Kueri basis data cache melewati statement_timeout." |
| Cache polusi kosong | `curl "localhost:8001/v1/pollution/edge-weights?bbox=106.825,-6.177,106.829,-6.173"` (data Railway saat ini) | 503 `cache_unavailable`, `"reason":"cache_empty"` |
| Perjalanan jauh dari laptop (±10 km) | body *Jalan kaki Monas → Bundaran HI* dengan tujuan diganti `{"lat": -6.2654, "lon": 106.8272}` | dari laptop bisa 503 karena jaringan lambat; di Railway seharusnya 200 |

### Hasil yang diharapkan

| Uji | Hasil |
|---|---|
| `/health` | `"status":"ok"`, `"database":"ok"`, `"graph_version":"jakarta-20261005"`, `"pollution_time_window"` (null selama cache polusi kosong) |
| nearest-node Monas | `"node_id":10792891204`, `"distance_m":11.7…` |
| streets `sudirman` | `Jalan Jenderal Sudirman`, 212 segmen |
| subgraph Monas → Bundaran HI (walk) | `"node_count":3444`, `"edge_count":4282`, `"rejected":{}` |
| titik di Kepulauan Seribu | HTTP 422, `"code":"outside_study_area"` |
| `python -m osm.verify` | `**Hasil: LULUS**` |

### Jika ada masalah

- **`"database":"unavailable"` atau 503 `cache_unavailable`.** Periksa `DATABASE_URL` dan koneksi
  internet. Dari laptop harus memakai URL publik, bukan `postgres.railway.internal`.
- **503 untuk perjalanan jauh (≥ ±5 km) dari laptop.** Ini karena koneksi laptop ke Railway lambat,
  bukan karena kodenya salah. Untuk uji lokal, tambahkan `OSM_STATEMENT_TIMEOUT_MS=5000` ke `.env`.
- **Port 8001 sudah dipakai.** Ganti `--port`, atau ubah `"8001:8000"` di `docker-compose.yml`.
- **Docker gagal membaca `.env`.** `--env-file` Docker tidak membuang tanda kutip, jadi tulis
  nilainya tanpa `"..."`.
- **`ModuleNotFoundError: osm`.** Jalankan `uvicorn` dan `python -m osm.verify` dari `src/backend`,
  dan `pytest` dari `src/backend/osm`.

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

Setiap sesi database dibuka dengan `default_transaction_read_only = on`. Ini mencegah kode
Backend menulis secara tidak sengaja, tetapi **bukan batas keamanan**: nilai itu hanya bawaan
sesi dan bisa dimatikan oleh sesi mana pun. Selama Backend masih memakai user `postgres`
(superuser), perlindungan sesungguhnya baru ada setelah role baca-saja (`backend_reader`) dibuat.

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
| GET | `/streets?q=&limit=` | Cari nama jalan; `q` wajib memuat ≥ 3 huruf/angka berurutan agar index trigram terpakai | handoff 9.8 |
| POST | `/subgraph` | Bagian OSM dari R8–R10: `compute_request_bbox`, `read_road_graph`, `snap_to_graph`, `assemble_request_subgraph` | Tabel 3.6, UT-BE-04..06 |
| GET | `/health` | Status koneksi database, versi graf aktif, time window polusi terakhir dan `pollution_data_stale` | |

- **Format data:**
  - `bbox` ditulis `min_lon,min_lat,max_lon,max_lat`.
  - Titik ditulis `{lat, lon}`.
  - Geometri berupa GeoJSON dengan urutan `[lon, lat]`.
- **Galat** memakai skema seragam
  `{"error": {"code", "message", "request_id"}}`. Galat 503 menambahkan `reason` (TI-SE-04):
  `cache_empty` (belum ada versi graf `active` / time window `complete`),
  `database_unavailable` (basis data tidak dapat dihubungi), atau `query_timeout`
  (melewati `statement_timeout` atau batas waktu klien). Kode yang dipakai:
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
from pollution.router import router as pollution_router

app = FastAPI(lifespan=osm_lifespan)
install_error_handling(app)
app.include_router(osm_router)
app.include_router(pollution_router)
```

Untuk `plan_route`, gunakan `OsmRepository.read_road_graph(bbox, mode)` lalu
`osm.graph.assemble_request_subgraph(...)`. Bobot polusi dipasang terpisah oleh
`attach_edge_weights`.

## Perakitan dan validasi graf

`read_road_graph` membaca baris `osm.road_node` dan `osm.road_edge`, lalu merakitnya menjadi
`RoadGraph` lewat `graph.assemble_road_graph`. Ruas hanya dimasukkan bila memenuhi semua syarat:
- boleh dilalui moda yang diminta;
- punya geometri minimal 2 titik;
- `length_m > 0`;
- kedua node ujungnya ada;
- ujung geometrinya sama dengan lokasi node `u`/`v`;
- `edge_id`-nya tidak ganda.

Ruas yang gagal dihitung per alasan di `RoadGraph.rejected`. Field `rejected` juga ada di
respons `/graph` dan `/subgraph`. `RoadGraph.edges_by_id()` dipakai untuk memetakan `EdgeWeight`
berdasarkan `edge_id`.

Laporan bukti verifikasi untuk seluruh versi aktif (baca-saja) dibuat dengan:

```bash
python -m osm.verify > osm/VERIFICATION.md   # dari src/backend
```

Hasil terakhir: [VERIFICATION.md](VERIFICATION.md).

## Catatan kinerja

Pencarian nama jalan (`/streets`) memakai index trigram `idx_road_edge_name_trgm`
(`pg_trgm`, ditambahkan 2026-10-12). Contoh hasil: "sudirman" ±11 ms di server, sebelumnya
±100 ms dengan pemindaian seluruh tabel.

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
