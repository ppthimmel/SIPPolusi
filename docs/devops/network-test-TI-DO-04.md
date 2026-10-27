# Bukti verifikasi TI-DO-04

Panduan dan prosedur: [`private-networking.md`](private-networking.md).
Basis: cabang `TI-DO-04` dari `main` @ `d033e66`, 27 Oktober 2026.
Lingkungan lokal: Docker Desktop 29.8.1, Docker Compose 5.5.1 (Windows 11,
WSL2), stack `docker-compose.yml` + `docker-compose.private.yml`, basis data
baru yang kosong.

## Ringkasan

| Uji | Lokal (compose) | Railway |
|---|---|---|
| Jalur publik: komponen internal tertutup | lulus 9/9 | lulus 5/5 per environment; development dengan gateway + backend aktif 7/7 (§6) |
| Backend → basis data, cache-api, model_registry | lulus 5/5 | belum |
| Data Worker → basis data, model_registry | lulus 3/3 + `main.py check` | basis data: lulus (log cron) |
| Gateway → backend dan cache-api lewat nama internal | lulus 4/4 (smoke test) | backend: lulus; cache-api: **belum lulus**, service masih menjalankan image Backend (§8) |
| DNS internal: terurai di dalam, tidak di luar | lulus | luar: lulus |
| Layanan internal mendengarkan IPv6 | lulus (backend, cache-api, model_registry) | — |
| Layanan tidak tersedia | lulus (backend, basis data) | backend tidak ada: 502, `/healthz` 200 |
| Backend → layanan inferensi final | — | menunggu layanan inferensi |
| Rotasi kata sandi PostgreSQL setelah TCP proxy ditutup | — | **belum** (§8.3) |

## 1. Port host setelah override

Hanya gateway yang memetakan port ke host; layanan internal hanya
mengekspos port di jaringan compose.

```
$ docker compose -f docker-compose.yml -f docker-compose.private.yml up -d --build gateway
$ docker compose ps --format '{{.Service}}\t{{.Status}}\t{{.Ports}}'
backend         Up 6 seconds (healthy)    8000/tcp
cache_api       Up 8 seconds (healthy)    8000/tcp
gateway         Up 1 second               0.0.0.0:8081->8080/tcp, [::]:8081->8080/tcp
minio           Up 13 seconds (healthy)   9000-9001/tcp
model_registry  Up 7 seconds              5000/tcp
postgres        Up 14 seconds (healthy)   5432/tcp
```

## 2. Jalur publik (dari host)

Jalur yang ditolak: port host backend, cache-api, PostgreSQL, model_registry,
dan MinIO ditolak; nama service compose tidak terurai dari luar jaringan.

```
$ python scripts/network_test.py --timeout 3 --open http://localhost:8081/healthz --closed tcp://localhost:8000 --closed tcp://localhost:8001 --closed tcp://localhost:5432 --closed tcp://localhost:5000 --closed tcp://localhost:9000 --closed tcp://localhost:9001 --closed tcp://backend:8000 --closed tcp://postgres:5432
network test 2026-10-26T19:33:14+00:00 from evexr_
PASS  reachable   http://localhost:8081/healthz
      DNS 127.0.0.1, ::1; HTTP 200 30 ms {"status":"ok","service":"gateway"}
PASS  unreachable tcp://localhost:8000
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:8000 No connection could be made because the target machine actively refused it; [::1]:8000 ...
PASS  unreachable tcp://localhost:8001
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:8001 ... actively refused it; [::1]:8001 ...
PASS  unreachable tcp://localhost:5432
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:5432 ... actively refused it; [::1]:5432 ...
PASS  unreachable tcp://localhost:5000
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:5000 ... actively refused it; [::1]:5000 ...
PASS  unreachable tcp://localhost:9000
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:9000 ... actively refused it; [::1]:9000 ...
PASS  unreachable tcp://localhost:9001
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:9001 ... actively refused it; [::1]:9001 ...
PASS  unreachable tcp://backend:8000
      DNS: backend not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres:5432
      DNS: postgres not resolvable (getaddrinfo failed)
9/9 checks passed
```

(Pesan Windows yang sama dipersingkat dengan `...`.)

## 3. Jalur internal yang diizinkan

Dari dalam container backend (skrip dibaca dari stdin):

```
$ docker compose exec -T backend python - --open tcp://postgres:5432 --open http://cache_api:8000/health --open http://model_registry:5000/health --open http://127.0.0.1:8000/health/ready --closed tcp://inference:8000 < scripts/network_test.py
network test 2026-10-26T19:33:58+00:00 from 9aa35bd94552
PASS  reachable   tcp://postgres:5432
      DNS 172.21.0.2; TCP connect to [172.21.0.2]:5432 ok
PASS  reachable   http://cache_api:8000/health
      DNS 172.21.0.4; HTTP 500 10 ms {"error":{"code":"internal_error","message":"Terjadi galat internal pada server.","request_id":"6742a7f3-6139-49a8-9d85-
PASS  reachable   http://model_registry:5000/health
      DNS 172.21.0.5; HTTP 200 9 ms OK
PASS  reachable   http://127.0.0.1:8000/health/ready
      DNS 127.0.0.1; HTTP 200 9 ms {"status":"ready","service":"backend","checks":{"database":"ok"}}
PASS  unreachable tcp://inference:8000
      DNS: inference not resolvable (No address associated with hostname)
5/5 checks passed
```

Jalannya ini sebelum perbaikan cache-api di §7; cache-api tetap menjawab
sendiri (JSON galatnya dengan `request_id`), jadi jalur jaringannya
terbukti. Setelah perbaikan, dari backend yang sama:

```
PASS  reachable   http://cache_api:8000/health
      DNS 172.21.0.4; HTTP 200 6 ms {"status":"degraded","database":"ok","graph_version":null,"pollution_time_window":null,"pollution_data_stale":null}
PASS  reachable   http://cache_api:8000/v1/osm/version
      DNS 172.21.0.4; HTTP 503 4 ms {"error":{"code":"cache_unavailable","message":"Schema cache belum dibuat (UndefinedTableError).","request_id":"3c5e0a49
2/2 checks passed
```

`inference` belum ada sehingga tidak terurai, sesuai tahap MVP.

Dari dalam Data Worker (image dibangun dari cabang ini):

```
$ docker compose run --rm --no-deps -T data_worker python - --open tcp://postgres:5432 --open http://model_registry:5000/health --closed tcp://backend.railway.internal:8000 < scripts/network_test.py
network test 2026-10-26T19:34:31+00:00 from d852a8ac956c
PASS  reachable   tcp://postgres:5432
      DNS 172.21.0.2; TCP connect to [172.21.0.2]:5432 ok
PASS  reachable   http://model_registry:5000/health
      DNS 172.21.0.5; HTTP 200 5 ms OK
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (Name or service not known)
3/3 checks passed

$ docker compose run --rm --no-deps data_worker python main.py check
2026-10-26T19:34:37.028+00:00 INFO    data_worker: cek konfigurasi: ok
2026-10-26T19:34:37.029+00:00 INFO    data_worker: cek impor modul: ok
2026-10-26T19:34:37.029+00:00 INFO    data_worker: cek basis data: ok
```

Gateway sampai backend lewat nama internal `backend:8000`, tanpa port host
backend:

```
$ python scripts/smoke_test.py --gateway http://localhost:8081
smoke test 2026-10-26T19:44:35+00:00
  gateway: http://localhost:8081
  backend: (not checked directly)
PASS  gateway liveness               GET http://localhost:8081/healthz
      HTTP 200 42 ms {"status": "ok", "service": "gateway"} request_id=ae35025627fe4f13df8a54a7b8019c51
PASS  route request via gateway      POST http://localhost:8081/v1/routes
      HTTP 503 16 ms {"status": "data_unavailable", "route": null, "metrics": null, "route_source": "fallback", "preference_applied": false, "message": "OSM graph export is unavaila request_id=c422dedfbe93082f672827ba4e08f825
PASS  cache API via gateway          GET http://localhost:8081/v1/osm/version
      HTTP 503 12 ms {"error": {"code": "cache_unavailable", "message": "Schema cache belum dibuat (UndefinedTableError).", "request_id": "b33f1f9313729ac9077d3dbbb59fde4b", "reason request_id=b33f1f9313729ac9077d3dbbb59fde4b
PASS  unknown path not proxied       GET http://localhost:8081/health
      HTTP 404 6 ms {"error": {"code": "not_found", "message": "Unknown path", "request_id": "ffc7d045c03ceeaed28563e381a1712e"}} request_id=ffc7d045c03ceeaed28563e381a1712e
4/4 checks passed
```

503 `data_unavailable` dan `cache_unavailable` adalah jawaban layanan itu
sendiri pada basis data kosong; yang diuji adalah bahwa request sampai ke
layanan yang benar lewat jaringan privat.

## 4. IPv6 (syarat jaringan privat Railway)

Sebelumnya `model_registry` menjalankan `mlflow server --host 0.0.0.0`
(hanya IPv4), sehingga tidak terjangkau lewat `*.railway.internal`. Setelah
diubah ke `--host "[::]"`:

```
model_registry-1  | [2026-10-26 19:36:17 +0000] [37] [INFO] Listening at: http://[::]:5000 (37)

$ docker compose exec -T model_registry python - --open http://127.0.0.1:5000/health --open "http://[::1]:5000/health" < scripts/network_test.py
PASS  reachable   http://127.0.0.1:5000/health
      DNS 127.0.0.1; HTTP 200 4 ms OK
PASS  reachable   http://[::1]:5000/health
      DNS ::1; HTTP 200 3 ms OK
2/2 checks passed
```

Backend dan cache-api sudah mendengarkan IPv4 + IPv6 (`host=""`):
`http://[::1]:8000/health` dijawab keduanya. Akses dari backend ke `model_registry:5000` lewat IPv4 tetap 200 setelah
perubahan.

## 5. Layanan tidak tersedia

| Skenario | Hasil |
|---|---|
| Backend dihentikan | `POST /v1/routes` → **502 dalam 3,97 s**; `/healthz` tetap 200; log gateway `{"level":"error","message":"POST /v1/routes 502",...,"upstream_status":""}` |
| Backend dijalankan lagi | request berikutnya dijawab backend (503 `data_unavailable`) setelah ±2 s, **tanpa restart gateway** |
| PostgreSQL dihentikan | backend `/health` 200; `/health/ready` → `503 {"status":"not_ready","service":"backend","checks":{"database":"unavailable"}}`; `tcp://postgres:5432` tidak terurai |
| PostgreSQL dihentikan, Data Worker `check` | `cek basis data: GAGAL - tidak dapat terhubung ke basis data postgres:5432/sippolusi (OperationalError: failed to resolve host 'postgres' ...)`; kata sandi tidak tercetak |

## 6. Railway

Proyek `humble-endurance`, 27 Oktober 2026 (WIB). Diperiksa dengan Railway
CLI 5.68.0.

### 6.1 Kondisi awal

| Environment | Berjalan | Domain publik | TCP proxy basis data |
|---|---|---|---|
| development | cache-api, data_worker (cron), database; frontend, model_registry, observability tidur | gateway, backend, frontend, grafana | `tokaido.proxy.rlwy.net:51696` **aktif** |
| staging | tidak ada (deploy gateway terakhir `FAILED` 14 Okt) | gateway, backend, frontend, observability | `iriguchi.proxy.rlwy.net:56279` **aktif** |

- Semua variabel antarlayanan di kedua environment sudah memakai
  `*.railway.internal` (`DATABASE_URL`, `BACKEND_UPSTREAM`,
  `MLFLOW_TRACKING_URI`); tidak ada yang memakai alamat publik. Variabel
  diperiksa hanya host:port-nya, tanpa mencetak kredensial.
- Gateway dan backend tidak punya deployment aktif di kedua environment
  (gateway development: setiap deploy `SKIPPED` karena *watch path*).
- Gateway belum memiliki `CACHE_API_UPSTREAM`, yang diwajibkan sejak
  TI-DO-02; frontend masih memakai `BACKEND_UPSTREAM` lama, padahal kode
  frontend di `dev` membaca `GATEWAY_UPSTREAM`.

Uji publik development sebelum perubahan (TCP proxy terbuka; domain backend
tidak menjawab hanya karena backend tidak berjalan, domainnya masih ada):

```
network test 2026-10-26T19:50:21+00:00 from evexr_
FAIL  reachable   https://gateway-development-fa8c.up.railway.app/healthz
      DNS 69.46.46.90; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"g_OQQjI8RmyGxLz_Y53eZw"})
PASS  reachable   https://frontend-development-49bf.up.railway.app/
      DNS 69.46.46.93; HTTP 200 1324 ms <!doctype html> <html lang="id">   <head>     <meta charset="UTF-8" />     <meta name="viewport" content="width=device-w
PASS  unreachable https://backend-development-0740.up.railway.app/health
      DNS 69.46.46.77; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"d13RQhMcRj6QogkJwUFZXw"})
FAIL  unreachable tcp://tokaido.proxy.rlwy.net:51696
      DNS 66.33.22.222; TCP connect to [66.33.22.222]:51696 ok
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres.railway.internal:5432
      DNS: postgres.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://cache-api.railway.internal:8000
      DNS: cache-api.railway.internal not resolvable (getaddrinfo failed)
5/7 checks passed
```

### 6.2 Perubahan

| Perubahan | Environment |
|---|---|
| TCP proxy `database` dihapus (`railway tcp-proxy delete`) | development, staging |
| Domain `backend-*.up.railway.app` dihapus (`railway domain delete`) | development, staging |
| Gateway: `CACHE_API_UPSTREAM=http://${{cache-api.RAILWAY_PRIVATE_DOMAIN}}:${{cache-api.PORT}}`, `PORT=8080` | development |
| Frontend: `GATEWAY_UPSTREAM=http://${{gateway.RAILWAY_PRIVATE_DOMAIN}}:${{gateway.PORT}}` | development |
| Gateway mendengarkan IPv6 juga (`listen [::]:${PORT}`), agar frontend dapat memanggilnya lewat jaringan privat | kode (cabang ini) |

Variabel diisi dengan `--skip-deploys`; belum ada deployment baru.

### 6.3 Jalur publik setelah perubahan

```
network test 2026-10-26T20:01:13+00:00 from evexr_
PASS  unreachable https://backend-development-0740.up.railway.app/health
      DNS 69.46.46.77; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"k9ckAvBoRhSsYP6b0ubPiw"})
PASS  unreachable postgres://tokaido.proxy.rlwy.net:51696
      DNS 66.33.22.222; TCP accepted but no PostgreSQL answered (b'')
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres.railway.internal:5432
      DNS: postgres.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://cache-api.railway.internal:8000
      DNS: cache-api.railway.internal not resolvable (getaddrinfo failed)
5/5 checks passed
```

```
network test 2026-10-26T20:01:13+00:00 from evexr_
PASS  unreachable https://backend-staging-fe40.up.railway.app/health
      DNS 69.46.46.45; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"33-GZDi5Tk-BNZ-ymrpb1w"})
PASS  unreachable postgres://iriguchi.proxy.rlwy.net:56279
      DNS 66.33.22.219; PostgreSQL: An existing connection was forcibly closed by the remote host
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres.railway.internal:5432
      DNS: postgres.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://cache-api.railway.internal:8000
      DNS: cache-api.railway.internal not resolvable (getaddrinfo failed)
5/5 checks passed
```

Alamat TCP proxy lama masih menerima koneksi TCP di edge Railway, tetapi
memutusnya begitu menerima data: tidak ada PostgreSQL di belakangnya. Karena
itu `network_test.py` sekarang punya target `postgres://` yang mengirim
`SSLRequest` (tanpa kredensial). Sebagai pembanding, PostgreSQL lokal
menjawab `N` pada uji yang sama (`PASS reachable postgres://localhost:55432`).

### 6.4 Jalur internal

- **Data Worker → basis data:** siklus cron development 26 Okt 19:15Z
  berjalan dengan `DATABASE_URL` ke `postgres.railway.internal` (satu-satunya
  host yang dikonfigurasi) dan menulis 327 baris:
  `lintasan 20261026T191547Z-54f752 selesai: {... 'status': 'ok', 'stations_ok': 124, ..., 'rows_new': 327 ...}`.
- **cache-api:** berjalan (`SUCCESS`) dan health check Railway lulus
  (`GET /health HTTP/1.1 200`); basis datanya hanya
  `postgres.railway.internal`.
- **Gateway → backend:** lulus (§6.6).
- **Gateway → cache-api:** request sampai lewat jaringan privat, tetapi
  service `cache-api` menjalankan image Backend (§6.5–6.6).
- **Frontend → gateway:** belum lulus; frontend perlu redeploy (§6.6).
- **Uji dari dalam container (`railway ssh`):** belum dijalankan; akun
  Railway belum memiliki kunci SSH terdaftar.

### 6.5 Gateway development (deploy 26 Okt 20:25Z, `dev` @ `4a7140f`)

Gateway di-deploy dari dashboard (*Deploy the repo*), karena deploy otomatis
dari GitHub selalu `SKIPPED` ("No changes to watched files"). Backend belum
punya deployment aktif. Deploy `railway up src/backend --path-as-root` gagal
dengan `Root directory "src/backend" was not found in the deployed source`:
`--path-as-root` mengunggah isi `src/backend` saja, sedangkan Root Directory
service tetap `src/backend`. Deploy CLI harus dari akar repositori tanpa
`--path-as-root`, atau dari dashboard.

```
smoke test 2026-10-26T20:28:01+00:00
  gateway: https://gateway-development-fa8c.up.railway.app
  backend: (not checked directly)
PASS  gateway liveness               GET https://gateway-development-fa8c.up.railway.app/healthz
      HTTP 200 309 ms {"status": "ok", "service": "gateway"} request_id=8fa307539d895d8f7565882621654071
FAIL  route request via gateway      POST https://gateway-development-fa8c.up.railway.app/v1/routes
      HTTP 502 302 ms "<html>\r\n<head><title>502 Bad Gateway</title></head>\r\n<body>\r\n<center><h1>502 Bad Gateway</h1></center>\r\n<hr><center>nginx</center>\r\n</body>\r\n</html request_id=42fa3031b4ae3bab61c7c7dfd4dfe184
FAIL  cache API via gateway          GET https://gateway-development-fa8c.up.railway.app/v1/osm/version
      HTTP 404 307 ms {"detail": "Not Found"} request_id=9d8b64998efa89402518c94d59c31274
PASS  unknown path not proxied       GET https://gateway-development-fa8c.up.railway.app/health
      HTTP 404 286 ms {"error": {"code": "not_found", "message": "Unknown path", "request_id": "c1851ae23816962cac58ab37872de547"}} request_id=c1851ae23816962cac58ab37872de547
2/4 checks passed
```

Log gateway untuk request yang sama:

```
[error] backend.railway.internal could not be resolved (3: Host not found), ... request: "POST /v1/routes HTTP/1.1"
[ERRO] POST /v1/routes 502 request_id="42fa3031b4ae3bab61c7c7dfd4dfe184" ... upstream_status=""
[WARN] GET /v1/osm/version 404 request_id="9d8b64998efa89402518c94d59c31274" ... upstream_status="404" upstream_duration_s="0.003"
```

Yang terbukti:

- **Backend tidak tersedia:** gateway menjawab 502 dalam 0,3 s karena nama
  `backend.railway.internal` tidak terurai (service tanpa deployment tidak
  punya alamat privat); `/healthz` tetap 200.
- **Gateway → cache-api lewat jaringan privat:** request sampai
  (`upstream_status="404"`, 3 ms) dan tercatat di log cache-api:
  `10.247.34.84:34528 - "GET /v1/osm/version HTTP/1.1" 404`.

Temuan: service `cache-api` di development ternyata menjalankan **Backend**,
bukan Cache API. Log start-nya `configuration ok: environment=development ...`
(`logger="backend"`), build log `load build definition from
src/backend/Dockerfile`, dan 404-nya berformat FastAPI bawaan
`{"detail": "Not Found"}`, bukan `{"error": ...}` milik Cache API. Penyebabnya
dibahas di §6.6.

### 6.6 Development lengkap (backend 26 Okt 20:46Z)

Backend di-deploy dengan `railway up` dari akar repositori (setelah
`.railwayignore`), cache-api di-redeploy dengan
`RAILWAY_DOCKERFILE_PATH=/src/backend/osm/Dockerfile`.

```
smoke test 2026-10-26T20:56:32+00:00
  gateway: https://gateway-development-fa8c.up.railway.app
  backend: (not checked directly)
PASS  gateway liveness               GET https://gateway-development-fa8c.up.railway.app/healthz
      HTTP 200 358 ms {"status": "ok", "service": "gateway"} request_id=3e2e9d4d4ffa0326e84deb88d70e3bad
PASS  route request via gateway      POST https://gateway-development-fa8c.up.railway.app/v1/routes
      HTTP 503 322 ms {"status": "data_unavailable", "route": null, "metrics": null, "route_source": "fallback", "preference_applied": false, "message": "OSM graph export is unavaila request_id=4cd1a2a70d7ce165ad2a1937d761151b
FAIL  cache API via gateway          GET https://gateway-development-fa8c.up.railway.app/v1/osm/version
      HTTP 404 312 ms {"detail": "Not Found"} request_id=7ba90ce81f923f2c123d00d8632461e3
PASS  unknown path not proxied       GET https://gateway-development-fa8c.up.railway.app/health
      HTTP 404 277 ms {"error": {"code": "not_found", "message": "Unknown path", "request_id": "76b15c0c4a6f68ce5bbb0548ff5da74c"}} request_id=76b15c0c4a6f68ce5bbb0548ff5da74c
3/4 checks passed
```

```
network test 2026-10-26T20:56:34+00:00 from evexr_
PASS  reachable   https://gateway-development-fa8c.up.railway.app/healthz
      DNS 69.46.46.90; HTTP 200 287 ms {"status":"ok","service":"gateway"}
PASS  reachable   https://frontend-development-49bf.up.railway.app/
      DNS 69.46.46.93; HTTP 200 1340 ms <!doctype html> <html lang="id">   <head>     <meta charset="UTF-8" />     <meta name="viewport" content="width=device-w
PASS  unreachable https://backend-development-0740.up.railway.app/health
      DNS 69.46.46.77; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"fg-61qTOQkSemyqVJH0Vcg"})
PASS  unreachable postgres://tokaido.proxy.rlwy.net:51696
      DNS 66.33.22.222; PostgreSQL: An existing connection was forcibly closed by the remote host
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://cache-api.railway.internal:8000
      DNS: cache-api.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres.railway.internal:5432
      DNS: postgres.railway.internal not resolvable (getaddrinfo failed)
7/7 checks passed
```

Hasil:

- **Gateway → backend lewat jaringan privat: lulus.** Backend menjawab
  sendiri (`data_unavailable`, karena basis data development belum memiliki
  ekspor graf OSM); log backend:
  `10.247.34.84:33248 - "POST /v1/routes HTTP/1.1" 503`, dan start-up
  `configuration ok: ... database=postgres.railway.internal:5432/railway`
  (backend → basis data privat).
- **Jalur publik: 7/7.** Gateway dan frontend terbuka; domain lama backend,
  TCP proxy basis data, dan nama `*.railway.internal` tertutup dari internet.
- **Gateway → cache-api: gagal, image salah.** Build cache-api masih
  `load build definition from src/backend/Dockerfile`. Metadata deployment
  menunjukkan berkas konfigurasi ikut dibaca (`propertyFileMapping`:
  `build.dockerfilePath ← $.build.dockerfilePath`): cache-api memakai
  `src/backend/railway.json` milik Backend, yang `"dockerfilePath":
  "Dockerfile"`-nya mengalahkan variabel `RAILWAY_DOCKERFILE_PATH` dan isian
  dashboard. Jadi bukan soal path relatif atau absolut. (Perbaikan awal
  berupa `src/backend/osm/railway.json` diganti; lihat §8.2.)
- **Frontend → gateway: gagal, deployment lama.** `/v1/routes` lewat
  frontend 502; log frontend `gateway could not be resolved (3: Host not
  found)`. Frontend masih berjalan dari deployment sebelum
  `GATEWAY_UPSTREAM` diisi (variabel diisi dengan `--skip-deploys`), sehingga
  memakai nilai bawaan `http://gateway:8080`.

### 6.7 Belum dilakukan

1. cache-api: lihat §8.2 (langkah ini diganti setelah review).
2. Redeploy `frontend` agar `GATEWAY_UPSTREAM` berlaku. Frontend memanggil
   `gateway.railway.internal`; bila environment hanya IPv6, gateway perlu
   `listen [::]` dari PR ini (deployment gateway saat ini dari `dev`).
3. Ulangi:

```bash
python scripts/smoke_test.py --gateway https://gateway-development-fa8c.up.railway.app
curl -s -X POST -H 'Content-Type: application/json' -d '{"origin":{"lon":106.8227,"lat":-6.1754},"destination":{"lon":106.8456,"lat":-6.2088},"mode":"walk","alpha":0.5,"beta":0.5}' https://frontend-development-49bf.up.railway.app/v1/routes
```

Variabel `DATABASE_PUBLIC_URL` pada service `database` masih ada (dibuat
otomatis oleh template), tetapi menunjuk ke proxy yang sudah dihapus. Bila
TCP proxy diaktifkan lagi untuk akses sementara, host dan port-nya berubah.

## 7. Temuan yang ikut diperbaiki

- **Cache-api pada basis data kosong.** Tanpa schema `osm`, `GET /health`
  dan `GET /v1/osm/version` menjawab **500** `internal_error`
  (`UndefinedTableError: relation "osm.graph_version" does not exist`),
  bukan 503 `cache_unavailable`. Health check image ikut gagal sehingga
  `docker compose up gateway` berhenti di dependensi `cache_api`.
  **Perbaikan:** `osm/db.py` memetakan `UndefinedTableError` dan
  `InvalidSchemaNameError` ke `CacheUnavailable(cache_empty)`, sama seperti
  belum ada `graph_version` aktif. Hasilnya `/health` 200 `degraded`,
  `/v1/osm/version` 503 `cache_unavailable`, dan compose berjalan tanpa
  `--no-deps` (§1, §3). Setelah review, pemetaan ini dibatasi pada schema
  `osm` dan `pollution` (pesan galat harus menyebut `"osm.`/`"pollution.` atau
  schema `"osm"`/`"pollution"`; semua kueri memakai nama berskema), dan galat
  aslinya dicatat pada level WARNING. Tabel salah ketik atau schema lain tetap
  500. Uji `osm/tests/test_db.py` (6 kasus); seluruh uji image
  (`--target test`): osm 106 lulus, pollution 20 lulus.
- **`src/gateway/.env` lokal lama** belum memuat `CACHE_API_UPSTREAM`
  (ditambahkan pada TI-DO-02), dan gateway menolak start dengan pesan yang
  menyebut variabel tersebut. Tambahkan baris dari `.env.example`
  (berkas `.env` tidak masuk repositori).

## 8. Tanggapan review (27 Oktober 2026)

Review AureliusJustin pada PR #58. Uji di bawah dijalankan ulang dengan kode
setelah perbaikan.

### 8.1 `--refused` dan proxy

`--closed` meluluskan nama yang tidak terurai, sehingga salah ketik nama host
ikut lulus. `network_test.py` sekarang punya `--refused`: lulus hanya bila DNS
menjawab lalu koneksi ditolak, diputus, habis waktu, atau dijawab fallback
Railway. Skrip juga mengabaikan `HTTP(S)_PROXY` (sebelumnya, dengan
`HTTPS_PROXY=http://127.0.0.1:9`, pemeriksaan gateway gagal walaupun gateway
hidup). Pembuktian: `--refused tcp://nonexistent-typo-host:5432` **gagal**
(`DNS: nonexistent-typo-host not resolvable`), sedangkan `--closed` untuk
target yang sama lulus.

Lokal (compose + override privat):

```
network test 2026-10-27T03:55:33+00:00 from evexr_
PASS  reachable   http://localhost:8081/healthz
      DNS 127.0.0.1, ::1; HTTP 200 7 ms {"status":"ok","service":"gateway"}
PASS  unreachable tcp://backend:8000
      DNS: backend not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres:5432
      DNS: postgres not resolvable (getaddrinfo failed)
PASS  refused     tcp://localhost:8000
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:8000 No connection could be made because the target machine actively refused it; [::1]:8000 No connection could be made because the target machine actively refused it
PASS  refused     tcp://localhost:8001
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:8001 No connection could be made because the target machine actively refused it; [::1]:8001 No connection could be made because the target machine actively refused it
PASS  refused     tcp://localhost:5432
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:5432 No connection could be made because the target machine actively refused it; [::1]:5432 No connection could be made because the target machine actively refused it
PASS  refused     tcp://localhost:5000
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:5000 No connection could be made because the target machine actively refused it; [::1]:5000 No connection could be made because the target machine actively refused it
PASS  refused     tcp://localhost:9000
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:9000 No connection could be made because the target machine actively refused it; [::1]:9000 No connection could be made because the target machine actively refused it
PASS  refused     tcp://localhost:9001
      DNS 127.0.0.1, ::1; TCP: [127.0.0.1]:9001 No connection could be made because the target machine actively refused it; [::1]:9001 No connection could be made because the target machine actively refused it
9/9 checks passed
```

```
network test 2026-10-27T03:56:04+00:00 from 0293780bd999
PASS  reachable   tcp://postgres:5432
      DNS 172.21.0.3; TCP connect to [172.21.0.3]:5432 ok
PASS  reachable   http://cache_api:8000/health
      DNS 172.21.0.5; HTTP 200 6 ms {"status":"degraded","database":"ok","graph_version":null,"pollution_time_window":null,"pollution_data_stale":null}
PASS  reachable   http://model_registry:5000/health
      DNS 172.21.0.4; HTTP 200 4 ms OK
PASS  refused     tcp://postgres:5433
      DNS 172.21.0.3; TCP: [172.21.0.3]:5433 Connection refused
4/4 checks passed
```

```
smoke test 2026-10-27T03:56:04+00:00
  gateway: http://localhost:8081
  backend: (not checked directly)
PASS  gateway liveness               GET http://localhost:8081/healthz
      HTTP 200 23 ms {"status": "ok", "service": "gateway"} request_id=117435f525af7cd80e88b45156709007
PASS  route request via gateway      POST http://localhost:8081/v1/routes
      HTTP 503 13 ms {"status": "data_unavailable", "route": null, "metrics": null, "route_source": "fallback", "preference_applied": false, "message": "OSM graph export is unavaila request_id=4621b0e389cc988740d38326468202b4
PASS  cache API via gateway          GET http://localhost:8081/v1/osm/version
      HTTP 503 7 ms {"error": {"code": "cache_unavailable", "message": "Schema cache belum dibuat (UndefinedTableError).", "request_id": "56ed6dd319dbad45b506f85d22547bd5", "reason request_id=56ed6dd319dbad45b506f85d22547bd5
PASS  unknown path not proxied       GET http://localhost:8081/health
      HTTP 404 4 ms {"error": {"code": "not_found", "message": "Unknown path", "request_id": "904258ed348ec1ce22b77b03c57ff80b"}} request_id=904258ed348ec1ce22b77b03c57ff80b
4/4 checks passed
```

Railway, dari luar:

```
network test 2026-10-27T03:54:12+00:00 from evexr_
PASS  reachable   https://gateway-development-fa8c.up.railway.app/healthz
      DNS 69.46.46.90; HTTP 200 319 ms {"status":"ok","service":"gateway"}
PASS  reachable   https://frontend-development-49bf.up.railway.app/
      DNS 69.46.46.93; HTTP 200 1293 ms <!doctype html> <html lang="id">   <head>     <meta charset="UTF-8" />     <meta name="viewport" content="width=device-w
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://cache-api.railway.internal:8000
      DNS: cache-api.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres.railway.internal:5432
      DNS: postgres.railway.internal not resolvable (getaddrinfo failed)
PASS  refused     https://backend-development-0740.up.railway.app/health
      DNS 69.46.46.77; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"lA2mlGb9QlSLHcBf-8Y8hA"})
PASS  refused     postgres://tokaido.proxy.rlwy.net:51696
      DNS 66.33.22.222; PostgreSQL: An existing connection was forcibly closed by the remote host
7/7 checks passed
```

```
network test 2026-10-27T03:54:14+00:00 from evexr_
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres.railway.internal:5432
      DNS: postgres.railway.internal not resolvable (getaddrinfo failed)
PASS  refused     https://backend-staging-fe40.up.railway.app/health
      DNS 69.46.46.45; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"0jkX0vcYQuaKHNTDJH0Vcg"})
PASS  refused     postgres://iriguchi.proxy.rlwy.net:56279
      DNS 66.33.22.219; PostgreSQL: An existing connection was forcibly closed by the remote host
4/4 checks passed
```

### 8.2 cache-api di Railway: belum lulus

```
smoke test 2026-10-27T03:54:58+00:00
  gateway: https://gateway-development-fa8c.up.railway.app
  backend: (not checked directly)
PASS  gateway liveness               GET https://gateway-development-fa8c.up.railway.app/healthz
      HTTP 200 381 ms {"status": "ok", "service": "gateway"} request_id=103fe17f3f8ee79feb6d2969519983e3
PASS  route request via gateway      POST https://gateway-development-fa8c.up.railway.app/v1/routes
      HTTP 503 335 ms {"status": "data_unavailable", "route": null, "metrics": null, "route_source": "fallback", "preference_applied": false, "message": "OSM graph export is unavaila request_id=a6697b4d4922791d84873c5a0547f727
FAIL  cache API via gateway          GET https://gateway-development-fa8c.up.railway.app/v1/osm/version
      HTTP 404 365 ms {"detail": "Not Found"} request_id=9298847bd016cb5a09c3e2cb081ac8aa
PASS  unknown path not proxied       GET https://gateway-development-fa8c.up.railway.app/health
      HTTP 404 295 ms {"error": {"code": "not_found", "message": "Unknown path", "request_id": "ecfcdd1330f920fe2557ced96c229956"}} request_id=ecfcdd1330f920fe2557ced96c229956
3/4 checks passed
```

Gateway → backend lulus. Percobaan pertama pagi ini menjawab 502 karena
backend sedang *cold start*: log start-up backend `03:54:15`, request pertama
`03:54:16`; percobaan ulang 20 detik kemudian lulus.

cache-api masih gagal. Deployment terakhirnya (`dev` @ `4a7140f`, 26 Okt
20:32Z) dibangun dari `src/backend/Dockerfile`, dan *Railway Config File* di
dashboard kosong: Config as Code sedang dihentikan Railway (berkas yang sudah
dipakai berlaku sampai 1 Desember 2026; sejak 28 Agustus 2026 service baru
tidak bisa mengaktifkannya), jadi `osm/railway.json` tidak bisa dipasang.

Metadata deployment tetap memuat `propertyFileMapping`
`build.dockerfilePath ← $.build.dockerfilePath`: Railway membaca
`src/backend/railway.json` secara otomatis karena Root Directory-nya
`src/backend`. Perbaikan yang diganti:

- `src/backend/osm/railway.json` dihapus.
- `dockerfilePath` dihapus dari `src/backend/railway.json`. Backend tidak
  terpengaruh (bawaan Railway adalah `Dockerfile` di Root Directory), dan
  cache-api memakai *Dockerfile Path* dari dashboard atau
  `RAILWAY_DOCKERFILE_PATH=/src/backend/osm/Dockerfile` (sudah terisi).
- Health check `/health` dan restart `On Failure` (5) cache-api diisi di
  dashboard, tidak bergantung pada berkas.

Langkah tersisa: deploy cache-api dari checkout cabang ini
(`railway up --service cache-api --environment development --ci` dari akar
repositori) agar `src/backend/railway.json` tanpa `dockerfilePath` yang
dibaca, lalu ulangi smoke test. Hasil dicatat di sini sebelum pemeriksaan
cache-api dianggap lulus.

### 8.3 Rotasi kata sandi PostgreSQL: belum

PostgreSQL development dan staging pernah terbuka ke internet lewat TCP proxy
(§6.1), dan alamat proxy lama tercatat di dokumen ini. Semua service memakai
user `postgres` yang sama, termasuk `MLFLOW_BACKEND_STORE_URI` model_registry
yang memuat kata sandi sebagai teks. Langkah rotasi ada di
[`private-networking.md`](private-networking.md#menutup-jalur-publik-di-railway)
(langkah 3). Belum dijalankan.

### 8.4 Lain-lain

- Nama domain privat model registry adalah `modelregistry.railway.internal`
  (service `model_registry`); `private-networking.md` dan komentar
  `src/model_registry/Dockerfile` sudah diperbaiki.
- Grafana development (`grafana-development-8ad2`) dan service
  `observability` staging masih publik; dicatat sebagai pengecualian di matriks
  eksposur, ditangani di TI-DO-08.
