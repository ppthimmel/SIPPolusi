# Bukti verifikasi TI-DO-02

Panduan yang diverifikasi: [`build-runtime.md`](build-runtime.md).
Basis: cabang `TI-DO-02` dari `dev` @ `2e19d39`, 9 Oktober 2026.
Lingkungan lokal: Docker Desktop 29.8.1 (Windows 11, WSL2).

## 1. Build

| Image | Hasil | Pemeriksaan build |
|---|---|---|
| `src/gateway` | berhasil, 74 MB | template dirender + `nginx -t`: `syntax is ok` |
| `src/backend` | berhasil, 1,4 GB | `python -c "import main"` lulus |
| `src/data_worker` | berhasil, 818 MB | impor `acquisition`, `database`, `spatial_model.inference`, `spku.store`, `psycopg`, `psycopg2` lulus |

Konteks build Data Worker turun menjadi 292 kB: `dataset_processed/`
(±295 MB data latih), `venv/` lokal, dan `.env` tidak lagi ikut ke image.
`.env` backend juga tidak lagi tersalin ke image.

Kegagalan build (template gateway dirusak dengan menghapus satu `;`):

```
nginx: [emerg] unexpected "}" in /etc/nginx/conf.d/default.conf:62
ERROR: failed to build: ... did not complete successfully: exit code: 1
```

## 2. Uji unit (di dalam image, Python 3.11)

| Layanan | Hasil |
|---|---|
| Backend (`src/backend/tests`, baru) | 17 lulus: validasi konfigurasi, penyamaran log, `/health` dan `/health/ready` |
| Data Worker (`src/data_worker/tests`, PostGIS sementara) | 123 lulus, 1 gagal |

Satu kegagalan Data Worker (`test_spatial_baseline.py::test_run_dapat_diulang_dari_manifest_yang_sama`)
**tidak terkait perubahan ini**: `spatial_model/evaluation/idw/report.py:157`
memotong `git_commit` yang bernilai `None` karena `git` tidak ada di dalam
container. Akan muncul juga di CI; dilaporkan terpisah.

## 3. Smoke test runtime (lokal)

Gateway, backend (`ENVIRONMENT=staging`), dan PostGIS 16 pada jaringan Docker
terpisah, kata sandi acak sekali pakai.

```
$ python scripts/smoke_test.py --gateway http://localhost:18081 --backend http://localhost:18000
smoke test 2026-10-09T07:34:33+00:00
  gateway: http://localhost:18081
  backend: http://localhost:18000
PASS  gateway liveness               GET http://localhost:18081/healthz
      HTTP 200 85 ms {"status": "ok", "service": "gateway"} request_id=6db2ca1c5debd4a92bab0ea72be2eec9
PASS  route request via gateway      POST http://localhost:18081/v1/routes
      HTTP 200 20 ms {"route": null, "preference_applied": true, "message": "Multi-objective route policy inference not implemented yet."} request_id=8f616f56093a1897898b773c81e5e2fa
PASS  unknown path not proxied       GET http://localhost:18081/health
      HTTP 404 32 ms {"error": {"code": "not_found", "message": "Unknown path", "request_id": "ca27af03760383e45b3608347796bc61"}} request_id=ca27af03760383e45b3608347796bc61
PASS  backend liveness               GET http://localhost:18000/health
      HTTP 200 24 ms {"status": "ok", "service": "backend"}
PASS  backend readiness (database)   GET http://localhost:18000/health/ready
      HTTP 200 19 ms {"status": "ready", "service": "backend", "checks": {"database": "ok"}}
5/5 checks passed
```

Log gateway untuk request yang sama (`request_id` sama dengan header respons):

```json
{"time":"2026-10-09T07:34:33+00:00","level":"info","message":"POST /v1/routes 200","request_id":"8f616f56093a1897898b773c81e5e2fa","method":"POST","path":"/v1/routes","status":200,"bytes_sent":112,"duration_s":0.006,"upstream_status":"200","upstream_duration_s":"0.004"}
```

Data Worker, `python main.py check` terhadap PostGIS yang sama:

```json
{"time": "2026-10-09T07:38:28.481+00:00", "level": "info", "logger": "data_worker", "message": "cek konfigurasi: ok"}
{"time": "2026-10-09T07:38:28.481+00:00", "level": "info", "logger": "data_worker", "message": "cek impor modul: ok"}
{"time": "2026-10-09T07:38:28.481+00:00", "level": "info", "logger": "data_worker", "message": "cek basis data: ok"}
```

`cycle` penuh tidak dijalankan secara lokal agar tidak menambah 121
permintaan ke portal SPKU; siklus per jam di Railway tetap berjalan.

## 4. Skenario gagal

| Skenario | Hasil | Kode keluar |
|---|---|---|
| Gateway tanpa `BACKEND_UPSTREAM` | `gateway: invalid configuration: BACKEND_UPSTREAM is not set; Railway: http://backend.railway.internal:8000, compose: http://backend:8000` | 1 |
| Gateway, `BACKEND_UPSTREAM=http://backend:8000/` | `... must not contain a path or trailing slash (nginx would replace the request path with it); use scheme://host:port` | 1 |
| Gateway, `BACKEND_UPSTREAM='http://x:1; return 200'` | `... contains characters not allowed in a host:port` | 1 |
| Gateway, backend dihentikan | `502` dalam 4 s, log akses `"level":"error"`; `/healthz` tetap 200 | — |
| Gateway, backend di-deploy ulang dengan IP baru | request berikutnya `200` tanpa restart gateway (resolusi DNS per 10 s) | — |
| Backend `ENVIRONMENT=staging` tanpa `DATABASE_URL`, `PORT=80x` | kedua masalah dilaporkan sekaligus, port tidak dibuka | 2 |
| Backend, basis data tak terjangkau | proses tetap hidup; `/health` 200, `/health/ready` 503 `{"database":"unavailable"}`; kata sandi tidak muncul di log | — |
| Data Worker `cycle` tanpa `DATABASE_URL` | `konfigurasi tidak sah, Data Worker tidak dijalankan: - DATABASE_URL belum diatur. ...` | 2 |
| Data Worker `cycle`, `SPKU_RETRIES=tiga`, `SPKU_DB_SCHEMA=Ground-Truth` | kedua masalah dilaporkan (sebelumnya `ValueError: invalid literal for int()` tanpa nama variabel) | 2 |
| Data Worker `check`, kata sandi salah | `cek basis data: GAGAL - ... password authentication failed for user "sippolusi"` | 1 |
| Data Worker `check`, host `*.railway.internal` dari luar Railway | gagal resolusi + petunjuk memakai `DATABASE_PUBLIC_URL` | 1 |

## 5. Railway staging

Proyek `humble-endurance`, environment `staging` dibuat 9 Oktober 2026
dengan menduplikasi `development`, lalu hanya menyisakan `database` (PostGIS
18, volume baru yang kosong), `backend`, `data_worker`, dan service baru
`gateway`; `data_worker` dihapus lagi dari staging setelah uji (lihat di bawah). Kode di-deploy dari working tree cabang `TI-DO-02` dengan
`railway up` (belum di-commit). Variabel gateway:
`BACKEND_UPSTREAM=http://${{backend.RAILWAY_PRIVATE_DOMAIN}}:${{backend.PORT}}`
(terurai menjadi `http://backend.railway.internal:8000`).

| Service | Deployment | Health check Railway |
|---|---|---|
| gateway | `SUCCESS` | `/healthz` (`railway.json`) |
| backend | `SUCCESS` | `/health` (`railway.json`) |
| data_worker | `SUCCESS` (cron), lalu dihapus dari staging | — |

Smoke test dari luar Railway, lewat domain publik:

```
$ python scripts/smoke_test.py --gateway https://gateway-staging-9379.up.railway.app --backend https://backend-staging-fe40.up.railway.app
smoke test 2026-10-09T08:51:05+00:00
  gateway: https://gateway-staging-9379.up.railway.app
  backend: https://backend-staging-fe40.up.railway.app
PASS  gateway liveness               GET https://gateway-staging-9379.up.railway.app/healthz
      HTTP 200 411 ms {"status": "ok", "service": "gateway"} request_id=c19f933bf9048feee7ec46a02a059884
PASS  route request via gateway      POST https://gateway-staging-9379.up.railway.app/v1/routes
      HTTP 200 330 ms {"route": null, "preference_applied": true, "message": "Multi-objective route policy inference not implemented yet."} request_id=b1652d69d0d3386978ed90e63e571f0c
PASS  unknown path not proxied       GET https://gateway-staging-9379.up.railway.app/health
      HTTP 404 292 ms {"error": {"code": "not_found", "message": "Unknown path", "request_id": "366e65d60c8194d520894513e53e9222"}} request_id=366e65d60c8194d520894513e53e9222
PASS  backend liveness               GET https://backend-staging-fe40.up.railway.app/health
      HTTP 200 310 ms {"status": "ok", "service": "backend"}
PASS  backend readiness (database)   GET https://backend-staging-fe40.up.railway.app/health/ready
      HTTP 200 296 ms {"status": "ready", "service": "backend", "checks": {"database": "ok"}}
5/5 checks passed
```

Log Railway untuk request yang sama: gateway mencatat `POST /v1/routes 200`
(`"level":"info"`, `request_id` `b1652d69d0d3386978ed90e63e571f0c`,
`upstream_status` 200 lewat jaringan privat) dan `GET /health 404`
(`"level":"warn"`). Backend mencatat
`configuration ok: environment=staging port=8000 log_level=INFO database=postgres.railway.internal:5432/railway`
(environment terbaca dari `RAILWAY_ENVIRONMENT_NAME`, tanpa kredensial) dan
seluruh 10 barisnya berlevel `info`. Sebelumnya, di `development`, seluruh
log Data Worker termasuk baris `INFO` tercatat `"level":"error"` karena
ditulis ke stderr.

Data Worker staging: siklus cron 09:15Z berjalan dengan kode baru dan
**lulus**, keluar dengan kode 0:

```
09:15:52 info | siklus akuisisi untuk 2026-10-09T08:00Z/09:00Z dimulai
09:18:02 info | lintasan 20261009T091551Z-c579cc selesai: {'run_id': '20261009T091551Z-c579cc', 'status': 'ok', 'stations_seen': 119, 'stations_ok': 119, 'stations_reporting': 111, ...}
09:18:02 info | siklus 2026-10-09T08:00Z/09:00Z: fetch_ground_truth -> ok
              | "measurements": 223, "stations": 109, "unavailable_stations": 9, "run_status": "ok"
              | "failed": []
```

Level log: 69 `info`, 119 `warn` (`spku.collect: perubahan daftar: appeared
...`, wajar karena basis data staging masih kosong), 0 `error`. Bandingkan
dengan `development` sebelum perubahan, yang mencatat setiap baris sebagai
`error`.

Yang dimaksud sebenarnya adalah `python main.py check`, bukan siklus penuh:
blok `environments.staging` di `railway.json` dibaca Railway tetapi kalah
oleh cron dan start command dashboard hasil duplikasi environment, dan
`railway environment edit` (CLI 5.63.1) tidak menerapkan perubahan apa pun.
Akibatnya portal disapu satu kali tambahan (121 permintaan). Setelah siklus
selesai, service `data_worker` dihapus dari staging agar tidak berulang tiap
jam; `development` tidak berubah (cron `15 * * * *`). Blok `environments`
tidak dipertahankan di repositori.

Ulangi kapan saja:

```bash
python scripts/smoke_test.py --gateway https://gateway-staging-9379.up.railway.app --backend https://backend-staging-fe40.up.railway.app
```
