# Bukti verifikasi TI-DO-04

Panduan dan prosedur: [`private-networking.md`](private-networking.md).
Basis: cabang `TI-DO-04` dari `main` @ `d033e66`, 27 Oktober 2026.
Lingkungan lokal: Docker Desktop 29.8.1, Docker Compose 5.5.1 (Windows 11,
WSL2), stack `docker-compose.yml` + `docker-compose.private.yml`, basis data
baru yang kosong.

## Ringkasan

| Uji | Lokal (compose) | Railway |
|---|---|---|
| Jalur publik: hanya gateway yang terbuka | lulus 9/9 | belum (lihat §6) |
| Backend → basis data, cache-api, model_registry | lulus 5/5 | belum |
| Data Worker → basis data, model_registry | lulus 3/3 + `main.py check` | belum |
| Gateway → backend dan cache-api lewat nama internal | lulus 4/4 (smoke test) | belum |
| DNS internal: terurai di dalam, tidak di luar | lulus | luar: lulus |
| Layanan internal mendengarkan IPv6 | lulus (backend, cache-api, model_registry) | — |
| Layanan tidak tersedia | lulus (backend, basis data) | — |
| Backend → layanan inferensi final | — | menunggu layanan inferensi |

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

Uji dari luar Railway, 27 Oktober 2026, terhadap domain environment
`staging` dari TI-DO-02:

```
$ python scripts/network_test.py --open https://gateway-staging-9379.up.railway.app/healthz --closed https://backend-staging-fe40.up.railway.app/health --closed tcp://backend.railway.internal:8000 --closed tcp://postgres.railway.internal:5432 --closed https://nonexistent-sippolusi-xyz.up.railway.app/
network test 2026-10-26T19:35:30+00:00 from evexr_
FAIL  reachable   https://gateway-staging-9379.up.railway.app/healthz
      DNS 69.46.46.115; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"LQcCnsbwQV-a3uOZljLL4A"})
PASS  unreachable https://backend-staging-fe40.up.railway.app/health
      DNS 69.46.46.45; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"avp-sbhbRBmOzq8pYqVb7A"})
PASS  unreachable tcp://backend.railway.internal:8000
      DNS: backend.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable tcp://postgres.railway.internal:5432
      DNS: postgres.railway.internal not resolvable (getaddrinfo failed)
PASS  unreachable https://nonexistent-sippolusi-xyz.up.railway.app/
      DNS 69.46.46.14; Railway edge: domain not routed to a service (404 {"status":"error","code":404,"message":"Application not found","request_id":"yJweSV9QR-mJrAwcJH0Vcg"})
4/5 checks passed
```

Yang terbukti: nama `*.railway.internal` tidak terurai dari internet, dan
skrip mengenali domain yang tidak lagi diarahkan ke service (pembanding:
domain acak yang tidak pernah ada memberi jawaban edge yang sama).

Yang **belum**: kedua domain staging, termasuk gateway, tidak lagi diarahkan
ke service, jadi environment staging TI-DO-02 tampaknya sudah tidak
berjalan. Uji jalur internal di Railway (`railway ssh`) juga belum dijalankan
karena sesi Railway CLI di mesin uji kedaluwarsa. Setelah `railway login`,
jalankan bagian **Railway** di
[`private-networking.md`](private-networking.md#railway) pada environment
yang aktif dan tambahkan hasilnya di sini, termasuk penghapusan TCP proxy
database dan domain publik backend.

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
  `--no-deps` (§1, §3). Uji unit baru `osm/tests/test_db.py`; seluruh uji
  image (`--target test`): osm 97 lulus, pollution 20 lulus.
- **`src/gateway/.env` lokal lama** belum memuat `CACHE_API_UPSTREAM`
  (ditambahkan pada TI-DO-02), dan gateway menolak start dengan pesan yang
  menyebut variabel tersebut. Tambahkan baris dari `.env.example`
  (berkas `.env` tidak masuk repositori).
