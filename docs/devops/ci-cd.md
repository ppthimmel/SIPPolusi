# CI/CD layanan (TI-DO-06)

Pipeline membangun, menguji, dan menjalankan regresi untuk setiap PR dan
setiap commit di `dev`/`main`. Deployment ke Railway hanya terjadi setelah
pipeline commit tersebut lulus. Definisi: [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
Bukti run: [`ci-cd-evidence-TI-DO-06.md`](ci-cd-evidence-TI-DO-06.md).
Build dan runtime tiap layanan: [`build-runtime.md`](build-runtime.md).

```
PR ke dev/main, push ke dev/main
        │
        ├─ build / backend, cache-api, gateway, data-worker, frontend ─┐
        ├─ test / backend                                              ├─► regression ─► ci-passed ─► Railway deploy
        └─ test / data-worker (PostGIS 18)                             ┘   (cache flow,               (Wait for CI)
                                                                            e2e gateway)
```

## Pemicu

| Peristiwa | Yang berjalan | Deployment |
|---|---|---|
| PR ke `dev` atau `main` (termasuk tiap push ke cabang PR) | seluruh pipeline | tidak ada |
| Push/merge ke `dev` | seluruh pipeline | Railway, environment yang mengikuti `dev`, bila `ci-passed` lulus |
| Push/merge ke `main` | seluruh pipeline | Railway `production`, bila `ci-passed` lulus |
| *Run workflow* manual (tab Actions) | seluruh pipeline | tidak ada |

Run PR yang tertimpa push baru dibatalkan; run di `dev`/`main` selalu
diselesaikan agar Railway mendapat hasil untuk setiap commit yang ditunggunya.

## Tahap dan job

| Tahap | Job | Isi | Gagal berarti |
|---|---|---|---|
| build | `build / backend` | `src/backend/Dockerfile`, termasuk pemeriksaan `import main` | dependensi/impor backend rusak |
| build | `build / cache-api` | `src/backend/osm/Dockerfile`, target `runtime` | image Cache API tidak dapat dibangun |
| build | `build / gateway` | `src/gateway/Dockerfile`; target `runtime` menjalankan tahap `test` (`tests/run.sh`): validasi variabel, render template + `nginx -t`, routing ke upstream tiruan | konfigurasi atau routing gateway rusak |
| build | `build / data-worker` | `src/data_worker/Dockerfile`, termasuk impor `preflight.RUNTIME_MODULES` | modul siklus tidak dapat diimpor |
| build | `build / frontend` | `src/frontend/Dockerfile` (`tsc -b && vite build`) | galat tipe atau build web |
| test | `test / backend` | `src/backend/tests` dan paket Cache API (`osm`, `pollution`, `planner`, `solver`) | uji unit backend gagal |
| test | `test / data-worker` | seluruh `src/data_worker/tests` pada PostGIS 18 sementara, termasuk `test_regresi.py` dan uji penyimpanan | uji Data Worker gagal |
| regression | `regression / cache flow + gateway e2e` | lihat bagian berikut | regresi lintas layanan |
| gerbang | `ci-passed` | lulus hanya bila semua job di atas `success` | ada job gagal, dibatalkan, atau dilewati |

Build dan test berjalan paralel. `regression` baru mulai setelah keduanya
lulus, sehingga kegagalan selalu tercatat pada tahap paling awal yang rusak.
Image tidak di-push ke registry; Railway membangun image-nya sendiri dari
Dockerfile yang sama, jadi build di CI adalah pemeriksaan yang sama dengan
yang akan dijalankan Railway.

## Regresi pada jalur final

Regresi yang disepakati untuk layanan final, dijalankan pada setiap PR dan
commit yang dapat di-deploy:

1. **Alur cache Data Worker → Backend** (`-m cacheflow` pada `pollution`,
   `planner`, `solver`): penulis Data Worker (`osm_cache`,
   `spatial_model.cache`) menulis graf dan bobot polusi ke PostGIS 18 lokal,
   lalu endpoint Backend membacanya.
2. **Uji regresi cacat** `src/data_worker/tests/test_regresi.py` (bagian dari
   `test / data-worker`): setiap uji pernah gagal pada telaah kode.
3. **Routing gateway** (bagian dari `build / gateway`).
4. **Smoke test end-to-end**: `docker compose` menjalankan PostGIS, Backend,
   Cache API, dan gateway dari Dockerfile yang sama. Skema `osm` dan
   `pollution` dibuat lebih dulu dengan DDL Data Worker
   (`osm_cache.schema_sql()`), seperti urutan di Railway, dengan tabel kosong.
   Lalu
   [`scripts/smoke_test.py`](../../scripts/smoke_test.py) memeriksa
   `/healthz`, `POST /v1/routes` sampai Backend, `/v1/osm/version` sampai
   Cache API, jalur tak dikenal tidak diteruskan, serta liveness dan
   readiness Backend.

Tidak ada bypass: tidak ada `continue-on-error`, dan uji alur cache yang
dilewati (misalnya karena `TEST_DATABASE_URL` tidak terjangkau) dinyatakan
gagal oleh [`scripts/ci/check_junit.py --no-skips`](../../scripts/ci/check_junit.py).
`ci-passed` juga gagal bila ada job yang dibatalkan atau dilewati.

## Aturan deployment

Deployment dilakukan oleh Railway, bukan oleh GitHub Actions: kredensial
deploy tetap di Railway dan tidak pernah ada di GitHub. Aturannya:

1. **Hanya `dev` dan `main` yang di-deploy.** Di Railway, Settings → Source
   tiap service: environment `production` mengikuti cabang `main`,
   environment pengembangan/staging mengikuti `dev`. Cabang lain tidak
   pernah di-deploy.
2. **Deploy menunggu CI.** Aktifkan **Wait for CI** (Settings → Source) pada
   setiap service yang terhubung ke repo: `backend`, Cache API, `gateway`,
   `data_worker`, dan frontend bila dipasang, di setiap environment. Railway
   menahan deployment berstatus *Waiting* sampai workflow commit itu selesai;
   bila ada job gagal, deployment *Skipped* dan versi lama tetap berjalan.
3. **Hanya lewat PR.** Branch protection `dev` dan `main` (Settings →
   Branches): *Require a pull request before merging* dan *Require status
   checks to pass* dengan check **`ci-passed`**. Karena `ci-passed` bergantung
   pada semua job lain, cukup check ini yang diwajibkan.
4. **Watch path tetap berlaku.** `railway.json` tiap layanan membatasi
   deploy ke perubahan foldernya (`src/backend/**`, `src/gateway/**`, ...).
   Commit yang hanya mengubah dokumen tetap menjalankan CI, tetapi tidak
   memicu deploy.

Pengaturan nomor 2 dan 3 ada di dashboard Railway dan GitHub, bukan di repo;
pemilik proyek perlu mengaktifkannya sekali per service/cabang.

## Environment dan rahasia

- **CI tidak memakai rahasia platform.** Workflow hanya meminta
  `contents: read`. PostGIS di CI adalah container sementara dengan kredensial
  uji (`test:test`); stack compose memakai `.env.example` apa adanya.
- **Rahasia deploy hanya di Railway**, per environment (tab Variables), dan
  dirujuk lewat referensi seperti `${{database.DATABASE_URL}}`. GitHub tidak
  menyimpan token Railway.
- **Log tidak mencetak rahasia.** Backend dan Data Worker menyamarkan
  kredensial pada log, gateway tidak mencatat query string atau header (lihat
  [Logging](build-runtime.md#logging)). Bila kelak workflow memerlukan
  rahasia, simpan sebagai *environment secret* GitHub (GitHub menyamarkannya
  di log) dan jangan `echo` nilainya.
- **PR dari fork** tidak mendapat rahasia apa pun dari GitHub, dan pipeline
  ini memang tidak membutuhkannya.

## Status dan artefak

- **Status per commit/PR**: setiap job tampil sebagai check pada PR dan
  commit (`build / gateway`, `test / data-worker`, `ci-passed`, ...), dengan
  tautan ke log job tersebut. Badge status `dev` ada di [README](../../README.md).
- **Artefak** (14 hari, tab Summary run):
  | Artefak | Isi |
  |---|---|
  | `reports-test-backend` | JUnit XML Backend dan tiap paket Cache API |
  | `reports-test-data-worker` | JUnit XML Data Worker |
  | `reports-regression` | JUnit XML alur cache, keluaran smoke test, `docker compose ps` dan log seluruh container |
  | `*.dockerbuild` (satu per image) | rekaman build dari `docker/build-push-action`: log langkah, durasi, cache |
- **Deployment**: Railway mencatat SHA commit pada setiap deployment dan
  melaporkan statusnya ke GitHub (bagian *Deployments* pada PR/commit dan
  halaman Environments repo).

## Menelusuri kegagalan

| Gejala | Tahap | Tempat melihat |
|---|---|---|
| Check `build / <layanan>` merah | build | log job atau artefak `*.dockerbuild`; baris `build check failed`/`build check gagal`, `FAIL ...` (gateway), atau `nginx: [emerg]` |
| Check `test / <layanan>` merah | test | log job (ringkasan `-ra` di akhir), JUnit pada artefak `reports-test-*` |
| Check `regression / ...` merah | regression | langkah yang merah: alur cache, `check_junit.py` (uji dilewati), start compose, atau smoke test (`FAIL ...`); log container di `reports-regression/compose.log` |
| `ci-passed` merah, job lain hijau | gerbang | job yang dilewati/dibatalkan; jalankan ulang run |
| CI hijau, deployment Railway *Failed* | deploy | Railway → Deployments → log build/deploy commit tersebut; status juga tampil pada *Deployments* GitHub |
| CI merah, deployment *Skipped* | deploy ditahan | memang diharapkan: perbaiki CI, versi lama tetap berjalan |

## Menjalankan pemeriksaan yang sama secara lokal

```bash
docker build --target runtime src/gateway                       # build / gateway
python -m pytest src/backend/tests                              # test / backend
(cd src/backend/osm && pytest)                                  # juga pollution, planner, solver
docker compose -f src/backend/testdb/docker-compose.yml up -d
export TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test
(cd src/data_worker && python -m pytest tests)                  # test / data-worker
(cd src/backend/pollution && pytest -m cacheflow)               # regression: alur cache
docker compose up -d --no-deps --wait postgres
python -c "import sys; sys.path.insert(0, 'src/data_worker'); from osm_cache import schema_sql; print(schema_sql())"   | docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose up -d --build --no-deps --wait backend cache_api gateway
python scripts/smoke_test.py --gateway http://localhost:8081 --backend http://localhost:8000
```
