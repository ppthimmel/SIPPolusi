# Logging dan health check (TI-DO-08)

Observabilitas minimum untuk latency, error, fallback, kesehatan layanan, dan
Data Worker. Berlaku untuk Backend API (`src/backend/main.py`), Cache API
(`src/backend/osm/app.py`), dan Data Worker (`src/data_worker`). API Gateway
sudah menulis access log JSON dengan `request_id` sejak TI-DO-02.

| Bagian | Berkas |
|---|---|
| Format log, correlation ID, redaksi | `src/backend/log_config.py` (identik dengan `src/data_worker/log_config.py`) |
| Middleware request: ID, latency, metrik, `/metrics` | `src/backend/telemetry.py` |
| Health Backend API | `src/backend/main.py` (`/health`, `/health/ready`) |
| Health Cache API | `src/backend/osm/app.py` (`/health/live`, `/health/ready`, `/health`) |
| Kesegaran job Data Worker | `src/data_worker/job_health.py`, `python main.py health` |

## 1. Skema log

Semua layanan Python menulis satu baris per record ke **stdout** (Railway
menandai stderr sebagai error). Di Railway, atau dengan `LOG_FORMAT=json`,
setiap baris adalah objek JSON; secara lokal berupa teks
`<waktu> <LEVEL> <logger> [<correlation_id>]: <pesan>`.

| Kunci | Selalu | Isi |
|---|---|---|
| `time` | ya | ISO 8601 UTC, milidetik |
| `level` | ya | `debug`, `info`, `warn`, `error` (nama yang dipahami Railway) |
| `logger` | ya | nama logger Python (`request`, `main`, `solver`, `data_worker.acquisition`, ...) |
| `message` | ya | teks yang sudah diredaksi; untuk event: `<event> kunci=nilai ...` |
| `service` | bila diketahui | `backend`, `cache-api`, `data_worker` (bawaan: `RAILWAY_SERVICE_NAME`) |
| `correlation_id` | dalam request/job | ID yang sama untuk semua baris satu request atau satu siklus |
| `event` | event terstruktur | nama event; field event menjadi kunci tingkat atas (filter Railway: `@event:job_finished @status:failed`) |

Field event yang bernilai `None` tidak ditulis. Field event tidak dapat
menimpa kunci di atas.

### Event

| Event | Layanan | Level | Field |
|---|---|---|---|
| `http_request` | backend, cache-api | `info`; `error` bila status ≥ 500; `debug` untuk `/health*` dan `/metrics` | `method`, `route` (template, mis. `/v1/osm/nodes/{node_id}`), `status`, `duration_ms` |
| `route_inference` | backend (`POST /v1/routes`) | `info` bila `ok`, `warn` bila `fallback`/`failed` | `outcome` (`ok`/`fallback`/`failed`), `status`, `route_source`, `fallback_reason`, `http_status`, `duration_ms` |
| `route_inference` (baris teks) | cache-api (`/v1/routes/solve`, logger `solver`) | `info`/`warn` | sudah ada sejak TI-SE-07: `request_id`, `solver`, `outcome`, `fallback_reason`, `inference_ms`, ... |
| `job_started` | data_worker | `info` | `job`, `time_window`, `trigger` |
| `job_step` | data_worker | `error` bila `failed`; `warn` bila `degraded`/`unavailable` atau ada `fallback_reason`; selain itu `info` | `time_window`, `step`, `status`, `duration_s`, `step_run_id` (run SPKU/inferensi milik langkah), `fallback_reason`, `error`, `note` (bila status bukan ok) |
| `downscale_fallback` | data_worker | `warn` | `time_window`, `pollutants` (yang jatuh ke IDW), `reason` (JSON per polutan) |
| `job_finished` | data_worker | `info` `ok`, `warn` `degraded`, `error` `failed`/`skipped` | `job`, `time_window`, `trigger`, `status` (= `worker.cycle_run.status`, dari `worker_runs.cycle_status()`), `duration_s`, `failed_steps`, `degraded_steps`, `fallback_steps`, `interrupted` (SIGTERM), `note` |
| `job_health` | data_worker (`main.py health`) | `info` sehat, `error` tidak sehat | `status`, `database`, `ground_truth`, `pollution_cache` |

### Yang tidak pernah dicatat

- **Environment variable, token, kredensial.** Formatter menyamarkan
  `scheme://user:***@host`, pasangan `password=`/`token=`/`api_key=`/..., dan
  nilai verbatim setiap variabel lingkungan bernama rahasia
  (`PASSWORD|SECRET|TOKEN|_KEY|DATABASE_URL|STORE_URI|DSN`), termasuk di dalam
  traceback. Field event yang namanya tampak rahasia (`api_token`, `dsn`, ...)
  ditulis sebagai `***`, baik di `message` maupun di kunci JSON.
- **Data personal yang tidak perlu.** Log request memakai template route,
  bukan path asli, dan tidak pernah memuat query string atau body. Koordinat
  asal/tujuan rute tidak dicatat (PNF-06). Uji:
  `test_coordinates_are_not_logged`, `test_request_log_carries_the_gateway_request_id`.

## 2. Correlation ID

| Jalur | Asal ID | Diteruskan |
|---|---|---|
| Klien → Gateway → Backend / Cache API | NGINX `$request_id` → header `X-Request-ID` | middleware `telemetry` memakainya bila aman (`[A-Za-z0-9._:-]{1,128}`), selain itu membuat UUID v4; dikembalikan pada header respons dan `error.request_id` |
| Cache API → Route Solver | `request.state.request_id` | header `X-Request-ID` dan `request_id` di body `IRouteSolve` (sudah ada, TI-SE-06) |
| Data Worker, satu siklus | `run_id` baris `worker.cycle_run` (TI-DO-07), mis. `20261027T081500Z-3fa9c1`, juga untuk siklus `skipped` | semua baris log siklus dan laporan siklus; `python main.py status` menampilkan `run_id` yang sama, jadi baris gagal langsung dicari di log dengan `@correlation_id:<run_id>`. Run milik langkah (SPKU `full-...`, inferensi `dsi-...`) tercatat sebagai `step_run_id` |

ID dari luar yang tidak lolos pola (mis. memuat baris baru untuk menyuntik
log palsu) diganti, tidak dicatat (`test_unsafe_request_id_is_replaced`).
Correlation ID memakai `contextvars`, sehingga baris dari modul mana pun yang
dicatat selama request/siklus (repository, solver, inferensi) ikut membawanya.

## 3. Health dan readiness

| Layanan | Hidup (liveness) | Siap (readiness) | Dipakai oleh |
|---|---|---|---|
| Backend API | `GET /health` → 200 selama proses melayani | `GET /health/ready` → 200/503, `checks.database` (`ok`/`not_configured`/`unavailable`), hanya `database` yang menentukan 200/503; `checks.route_graph` (`ok`/`unavailable`: berkas graf `OSM_GRAPH_DIR` tidak ada) hanya dilaporkan, karena `POST /v1/routes` publik dilayani Cache API dan image Backend tidak membawa berkas graf | Railway deploy health check: `/health` |
| Cache API | `GET /health/live` → 200 tanpa menyentuh basis data | `GET /health/ready` → 503 bila `database` ≠ `ok` atau `graph` ≠ `ok` (`empty`: belum ada graph_version aktif); `pollution` (`ok`/`stale`/`empty`) dilaporkan tetapi **tidak** membuat API unready | Docker `HEALTHCHECK`: `/health/live`. `GET /health` (status rinci, selalu 200) tetap ada |
| Data Worker (cron) | `python main.py check --skip-db` (konfigurasi, impor) | `python main.py check` (+ koneksi dan skema basis data) | build dan pra-deploy |
| Data Worker, jadwal | — | `python main.py status` (TI-DO-07) → exit 0 sehat, 1 eksekusi terakhir gagal, 3 tidak ada eksekusi untuk window yang jatuh tempo | **alert utama** |
| Data Worker, kesegaran data | — | `python main.py health [--max-lag-hours 2]` → exit 0 segar, 1 tertinggal/tak terbaca, 2 konfigurasi | alert kedua |

Liveness sengaja tidak bergantung pada basis data: basis data yang mati tidak
boleh membuat container di-restart berulang; cukup readiness yang gagal.
Detail galat (pesan driver, host) hanya masuk log, tidak ke respons health.

### `status` atau `health` untuk Data Worker

Keduanya dijalankan di Railway sebagai cron terpisah dengan image Data Worker;
exit bukan 0 membuat run itu gagal dan memicu notifikasi Railway.

| | `python main.py status` (TI-DO-07) | `python main.py health` (TI-DO-08) |
|---|---|---|
| Pertanyaan | Apakah siklus terjadwal berjalan, tepat waktu, dan berhasil? | Apakah data yang dipakai API masih segar? |
| Sumber | `worker.cycle_run` | `ground_truth.run` (lintasan SPKU ok/partial terakhir) dan `pollution.pollution_window` (window complete terbaru) |
| Menangkap | cron berhenti, siklus gagal/macet/terlambat, siklus `skipped` | siklus `ok`/`degraded` tetapi data tidak bertambah (mis. SPKU kosong berjam-jam, inferensi tidak menulis cache), atau cache dibersihkan |
| Jadwal cron | `45 * * * *` | `50 * * * *` |

Alert utama adalah `status`: ia menunjuk `run_id` yang gagal, dan `run_id`
itu adalah `correlation_id` di log. `health` menjadi alert kedua untuk kasus
yang tidak terlihat dari status siklus. Bila hanya satu cron yang dipasang,
pasang `status`.

### `/metrics`

`GET /metrics` pada Backend API dan Cache API **tanpa autentikasi**. Itu
disengaja: kedua service hanya dijangkau lewat jaringan privat Railway, dan
gateway hanya meneruskan `POST /v1/routes`, `GET /v1/exposure-surface`, dan
`/healthz` ke luar, sehingga `/metrics` tidak terbuka ke publik. Bila service
diberi domain publik, `/metrics` harus ditutup (gateway atau token) lebih
dulu. Penghitung disimpan di memori proses: nilainya kembali ke 0 setiap
restart/deploy, dan bila service dijalankan dengan beberapa replika/worker
uvicorn, setiap proses punya penghitung sendiri (Prometheus mengagregasi per
target dengan `sum` dan `rate`).

## 4. Sinyal tidak sehat

| Sinyal | Sumber | Arti |
|---|---|---|
| `/health/ready` 503 | Backend, Cache API | dependensi (basis data, graf) tidak siap; request terkait akan 503 |
| `level:error` dengan `event:http_request` | Backend, Cache API | respons 5xx |
| `sippolusi_http_requests_total{status=~"5.."}` naik | `/metrics` | laju error |
| `sippolusi_http_request_duration_seconds_sum / _count` naik | `/metrics` | latency rata-rata per route |
| `event:route_inference outcome:fallback`, `sippolusi_route_fallback_total{reason}` | Backend | rute dilayani baseline, bukan policy; respons tetap 200 tetapi tercatat `warn` |
| `sippolusi_route_inference_failures_total{reason}` | Backend | inferensi policy melempar exception |
| `event:job_finished status:failed` / `skipped` / `degraded` | Data Worker | ada langkah gagal atau siklus terhenti (`interrupted`) / siklus lain masih memegang kunci / ada langkah `degraded` atau `unavailable` |
| `event:downscale_fallback`, `fallback_steps` pada `job_finished` | Data Worker | model polutan tidak tersedia; EdgeWeight polutan itu dihitung IDW |
| `main.py status` exit 1 / 3 | Data Worker | eksekusi terakhir gagal / tidak ada eksekusi untuk window yang jatuh tempo |
| `main.py health` exit 1, `ground_truth: lagging` | Data Worker | run kolektor SPKU terakhir yang berhasil > 2 jam lalu |
| `main.py health` exit 1, `pollution_cache: lagging`/`missing` | Data Worker | time window complete terbaru berakhir > 2 jam lalu (bila `DOWNSCALE_WRITE_CACHE=1`) |
| `/health/ready` Cache API `pollution: stale` | Cache API | pengguna melihat data basi; Data Worker tertinggal |

Batas 2 jam: siklus untuk jam H berjalan pada H+1:15, sehingga hasil normal
berumur 15–75 menit; lebih dari 2 jam berarti paling sedikit satu siklus
terlewat atau gagal. Cara memasangnya sebagai cron Railway ada di subbab 3.

## 5. Contoh log teredaksi

Dihasilkan dari kode cabang ini (`LOG_FORMAT=json`), dipotong seperlunya.

Backend, satu rute yang dilayani fallback; kedua baris membawa `X-Request-ID`
dari gateway:

```json
{"time": "2026-10-27T01:16:40.191+00:00", "level": "warn", "logger": "main", "message": "route_inference outcome=fallback status=ok route_source=fallback fallback_reason=policy_unavailable http_status=200 duration_ms=11.3", "service": "backend", "correlation_id": "3f1c2a9e-0b1d-4c55-9a9e-1d2c3b4a5f60", "event": "route_inference", "outcome": "fallback", "status": "ok", "route_source": "fallback", "fallback_reason": "policy_unavailable", "http_status": 200, "duration_ms": 11.3}
{"time": "2026-10-27T01:16:40.192+00:00", "level": "info", "logger": "request", "message": "http_request method=POST route=/v1/routes status=200 duration_ms=15.4", "service": "backend", "correlation_id": "3f1c2a9e-0b1d-4c55-9a9e-1d2c3b4a5f60", "event": "http_request", "method": "POST", "route": "/v1/routes", "status": 200, "duration_ms": 15.4}
```

Data Worker, satu siklus: langkah ground truth `degraded`, NO2 jatuh ke IDW, dan DSN yang
ikut tercetak oleh galat koneksi tersamarkan. `correlation_id` adalah `run_id` pada `worker.cycle_run`:

```json
```

## 6. Uji kondisi tidak sehat

| Kondisi | Hasil | Uji |
|---|---|---|
| Backend tanpa `DATABASE_URL` | `/health` 200, `/health/ready` 503 `database: not_configured` | `tests/test_health.py::test_liveness_and_readiness_without_database` |
| Backend, basis data tidak terjangkau (DSN berisi kata sandi) | `/health/ready` 503 `database: unavailable`, kata sandi tidak ada di respons | `test_readiness_reports_unreachable_database_without_details` |
| Backend, berkas graf tidak ada | `/health/ready` tetap 200 bila database ok, `route_graph: unavailable` dilaporkan | `test_missing_route_graph_is_reported_but_does_not_gate_readiness` |
| Backend, policy tidak tersedia / melempar exception | 200 dengan `route_source: fallback`, log `warn` `route_inference outcome=fallback`, exception `error` dengan correlation ID, metrik fallback/failure naik | `tests/test_observability.py` |
| Backend, data rute tidak tersedia | 503, `route_inference outcome=failed`, `http_request` level `error` | `test_route_failure_is_logged_as_failed` |
| Cache API, basis data mati | `/health/live` 200 tanpa query, `/health/ready` 503 | `osm/tests/test_observability.py::test_not_ready_when_database_is_down` |
| Cache API, belum ada graf aktif | `/health/ready` 503 `graph: empty` | `test_not_ready_without_an_active_graph` |
| Cache API, data polusi basi | `/health/ready` 200 `pollution: stale` | `test_stale_pollution_is_reported_but_keeps_the_api_ready` |
| Cache API, error 503 | `http_request` level `error`, dihitung di `/metrics` | `test_server_errors_are_logged_as_errors_and_counted` |
| Data Worker, ground truth terakhir 3 jam lalu | `unhealthy`, `ground_truth: lagging` | `tests/test_observability.py` (worker) |
| Data Worker, cache aktif tanpa window complete | `unhealthy`, `pollution_cache: missing` | `test_cache_aktif_tanpa_time_window_complete_tidak_sehat` |
| Data Worker, basis data tak terbaca | `unhealthy`, `database: unavailable`, tanpa kredensial | `test_basis_data_tak_terbaca_dilaporkan_tanpa_kredensial` |
| `main.py health` saat tertinggal | exit 1, event `job_health` level `error` | `test_perintah_health_keluar_1_dan_mencatat_event_saat_tertinggal` |
| Satu langkah siklus gagal | `job_step` level `error` dengan `error`; langkah lain tetap jalan | `test_langkah_gagal_tercatat_sebagai_error` |
| Langkah `degraded`/`unavailable` | `job_step` level `warn`; `job_finished.status` = `cycle_run.status` = `degraded` | `test_status_job_finished_sama_dengan_cycle_run_untuk_langkah_degraded` |
| Siklus lain memegang kunci | `job_finished status=skipped` level `error`, `correlation_id` = `run_id` baris skipped | `test_siklus_dilewati_karena_kunci_tercatat_skipped` |
| SIGTERM di tengah siklus | `job_finished status=failed interrupted=true` | `test_siklus_terhenti_sigterm_tercatat_failed_interrupted` |
| Correlation ID siklus | semua baris log siklus membawa `run_id` `cycle_run` | `test_run_id_siklus_menjadi_correlation_id_semua_log` |

Contoh keluaran `python main.py health` saat tertinggal (exit 1):

```json
{
  "status": "unhealthy",
  "max_lag_minutes": 120,
  "checks": {
    "database": {"status": "ok"},
    "ground_truth": {"status": "lagging", "last": "2026-10-26T22:06:55+00:00", "lag_minutes": 190.0},
    "pollution_cache": {"status": "lagging", "last": "2026-10-26T21:00:00+00:00", "lag_minutes": 256.9}
  }
}
```

Menjalankan uji (Python 3.11):

```bash
python -m pytest src/backend/tests                      # dari root repo
cd src/backend/osm && python -m pytest                  # juga pollution, planner, solver
cd src/data_worker && python -m pytest tests/test_observability.py tests/test_preflight.py tests/test_data_worker.py
```
