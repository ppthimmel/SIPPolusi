# Bukti verifikasi TI-DO-06

Panduan yang diverifikasi: [`ci-cd.md`](ci-cd.md). Definisi workflow:
[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
Basis: cabang `TI-DO-06` dari `dev` @ `4a7140f`, PR
[#60](https://github.com/ppthimmel/SIPPolusi/pull/60), 27 Oktober 2026 (WIB).
Runner: `ubuntu-latest` GitHub Actions, Python 3.11.

Log di bawah dikutip apa adanya. Tidak ada yang perlu disamarkan: pipeline
tidak memakai rahasia, dan satu-satunya kredensial adalah nilai uji
(`test:test`, `.env.example`). Pencarian `password`/`change-me` pada
`compose.log` run lulus memberi 0 baris.

## 1. Riwayat run pada PR

Setiap run gagal di bawah adalah kegagalan sungguhan yang ditemukan pipeline
pada percobaan pertamanya, bukan kegagalan yang dibuat-buat.

| Run | Commit | Hasil | Tahap gagal | Penyebab | Perbaikan |
|---|---|---|---|---|---|
| [40408597982](https://github.com/ppthimmel/SIPPolusi/actions/runs/40408597982) | `983f5e9` | gagal | test (backend, data-worker) | lihat bagian 2 | `9763dab`, `e2ea3db` |
| [40409073380](https://github.com/ppthimmel/SIPPolusi/actions/runs/40409073380) | `e2ea3db` | gagal | test (backend) | solver baseline tetap berjalan >1 s setelah dibatalkan: daftar `Edge` dibangun sebelum pemeriksaan token pertama | `b7005b6` |
| [40409506351](https://github.com/ppthimmel/SIPPolusi/actions/runs/40409506351) | `b7005b6` | gagal | regression | compose menolak berjalan: `env file src/frontend/.env not found` (compose membaca `env_file` semua service) | `b8c7e3d` |
| [40410071193](https://github.com/ppthimmel/SIPPolusi/actions/runs/40410071193) | `b8c7e3d` | gagal | test (backend) | batas waktu mock `0.153 s < 0.15` gagal tipis di runner bersama | `f3ba277` |
| [40410450094](https://github.com/ppthimmel/SIPPolusi/actions/runs/40410450094) | `f3ba277` | gagal | regression | lihat bagian 3 | `804c4d0` |
| [40411314491](https://github.com/ppthimmel/SIPPolusi/actions/runs/40411314491) | `804c4d0` | **lulus** | – | – | – |

## 2. Contoh run gagal: tahap test

Run 40408597982. Semua job build lulus, dua job test gagal, `regression`
dilewati karena bergantung pada test, dan gerbang menolak commit:

```
ci-passed                              fail
test / backend                         fail
test / data-worker                     fail
regression / cache flow + gateway e2e  skipping
build / backend, cache-api, gateway, data-worker, frontend   pass
```

Log `ci-passed`:

```
build: success
test-backend: failure
test-data-worker: failure
regression: skipped
Error: A required job did not succeed (failed, cancelled or skipped); nothing may be deployed from this commit.
```

`test / backend` menunjuk uji dan barisnya:

```
FAILED tests/test_cancel.py::test_mock_search_does_not_block_the_timeout - assert (False)
 +  where False = wait(0.5)
============= 1 failed, 91 passed, 6 skipped, 1 warning in 13.53s ==============
```

Penyebabnya: solver mock membangun adjacency seluruh input (±0,4 s untuk
grid uji 400 × 400, lebih lama di runner) sebelum memeriksa token pembatalan
pertama, sehingga pencarian yang sudah *timeout* tetap menghitung. Perbaikan
`9763dab` dan `b7005b6` memeriksa token setiap `CHECK_EVERY` (1024) item
selama persiapan input, sama seperti yang sudah dilakukan baseline saat
membangun router.

`test / data-worker`:

```
FAILED tests/test_spatial_baseline.py::test_pipeline_berjalan_tanpa_encoding_utf8 - AssertionError: Unable to decode the command from the command line:
  UnicodeEncodeError: 'utf-8' codec can't encode characters in position 502-503: surrogates not allowed
================= 1 failed, 196 passed, 21 warnings in 26.16s ==================
```

Uji ini memaksa locale `en_US.ISO8859-1` (seperti Windows) yang tidak
terpasang di runner, sehingga Python gagal start sebelum uji dapat memutuskan
untuk dilewati. CI kini memasang locale itu (`sudo locale-gen en_US`), jadi
uji regresi encoding benar-benar berjalan (197 lulus, 0 dilewati).

## 3. Contoh run gagal: tahap regression

Run 40410450094. Build dan test lulus; langkah *Start gateway, backend,
Cache API and PostGIS (compose)* gagal. `compose-ps.txt` pada artefak
`reports-regression`:

```
SERVICE     STATUS
backend     Up About a minute (healthy)
cache_api   Up About a minute (unhealthy)
gateway     Created
postgres    Up About a minute (healthy)
```

`compose.log`:

```
cache_api-1  | asyncpg.exceptions.UndefinedTableError: relation "osm.graph_version" does not exist
cache_api-1  | INFO:     127.0.0.1:55160 - "GET /health HTTP/1.1" 500 Internal Server Error
```

Pada basis data yang belum memiliki skema `osm`, `GET /health` Cache API
menjawab 500, bukan `degraded`, sehingga health check-nya gagal dan gateway
tidak pernah start. Di Railway skema dibuat oleh Data Worker sebelum Cache
API dipakai; stack e2e sempat meniru urutan itu dengan DDL Data Worker sendiri
(`osm_cache.schema_sql()`), tabel tetap kosong. Cache API kemudian diperbaiki
di `dev` (#58: skema `osm`/`pollution` yang belum ada menjadi `degraded` dan
503 `cache_empty`), sehingga langkah itu dihapus setelah review: e2e kini
berjalan pada basis data kosong tanpa skema dan ikut menguji perilaku tersebut.

## 4. Contoh run lulus

Run [40411314491](https://github.com/ppthimmel/SIPPolusi/actions/runs/40411314491),
commit `804c4d0`, 4 menit dari awal sampai `ci-passed`.

| Job | Hasil | Isi |
|---|---|---|
| `build / backend` | lulus | `import main` di dalam image |
| `build / cache-api` | lulus | target `runtime` |
| `build / gateway` | lulus | `tests/run.sh` di dalam build |
| `build / data-worker` | lulus | impor `preflight.RUNTIME_MODULES` |
| `build / frontend` | lulus | `tsc -b && vite build` |
| `test / backend` | lulus | Backend 35; Cache API: osm 100 (+15 integrasi Railway dilewati), pollution 20, planner 35, solver 92 (alur cache dilewati, dijalankan di regression) |
| `test / data-worker` | lulus | 197 lulus, 0 dilewati, PostGIS 18 |
| `regression / cache flow + gateway e2e` | lulus | alur cache pollution 10, planner 2, solver 6, 0 dilewati; smoke test 6/6 |
| `ci-passed` | lulus | semua job `success` |

Pemeriksaan "alur cache tidak boleh dilewati":

```
ci-reports/cacheflow-planner.xml: 2 tests, 0 failures, 0 errors, 0 skipped
ci-reports/cacheflow-pollution.xml: 10 tests, 0 failures, 0 errors, 0 skipped
ci-reports/cacheflow-solver.xml: 6 tests, 0 failures, 0 errors, 0 skipped
```

Smoke test lewat gateway (`smoke-test.txt` pada artefak `reports-regression`;
isi respons terpotong 160 karakter oleh skrip):

```
PASS  gateway liveness               GET http://localhost:8081/healthz
      HTTP 200 3 ms {"status": "ok", "service": "gateway"} request_id=728f7cb441b569377812be15d3d45088
PASS  route request via gateway      POST http://localhost:8081/v1/routes
      HTTP 503 6 ms {"status": "data_unavailable", ...} request_id=acf16aae6675460f0cc1e8edd0437a24
PASS  cache API via gateway          GET http://localhost:8081/v1/osm/version
      HTTP 503 4 ms {"error": {"code": "cache_unavailable", ...}} request_id=c22144af6ec3f30464b97806611a43d0
PASS  unknown path not proxied       GET http://localhost:8081/health
      HTTP 404 1 ms {"error": {"code": "not_found", "message": "Unknown path", ...}}
PASS  backend liveness               GET http://localhost:8000/health
      HTTP 200 1 ms {"status": "ok", "service": "backend"}
PASS  backend readiness (database)   GET http://localhost:8000/health/ready
      HTTP 200 3 ms {"status": "ready", "service": "backend", "checks": {"database": "ok"}}
6/6 checks passed
```

503 pada rute dan Cache API adalah jawaban yang diharapkan untuk basis data
tanpa graf jalan: layanan yang benar menjawab, bukan gateway (502/504).

Artefak run ini: `reports-test-backend`, `reports-test-data-worker`,
`reports-regression`, dan satu rekaman build `*.dockerbuild` per image dari
`docker/build-push-action`.

## 5. Yang belum diverifikasi di sini

- **Deployment Railway setelah CI.** *Wait for CI* dan pemetaan cabang
  (`dev`, `main`) diatur di dashboard Railway, dan branch protection
  `ci-passed` di pengaturan repo GitHub; keduanya perlu diaktifkan pemilik
  proyek (langkah di [`ci-cd.md`](ci-cd.md#aturan-deployment)). Bukti
  deployment *Waiting → Success* dan *Skipped* saat CI gagal diambil pada
  merge pertama ke `dev` setelah pengaturan aktif.
- Run pada `push` ke `dev`/`main` baru terjadi setelah PR ini di-merge;
  isinya sama dengan run PR.
