# Build dan runtime layanan (TI-DO-02)

Panduan membangun, menjalankan, dan memeriksa tiga layanan yang di-deploy ke
Railway: **Data Worker**, **Backend API**, dan **API Gateway (NGINX custom)**.
Hasil uji ada di [`smoke-test-TI-DO-02.md`](smoke-test-TI-DO-02.md).

Konfigurasi ini tidak bergantung pada Traefik atau K3s: build memakai
Dockerfile per layanan, Railway membangun dan menjalankannya, dan NGINX custom
menjadi titik masuk publik di depan Backend (Dokumen Desain, Tabel 3.2).
Manifes `k3s/` lama (Ingress Traefik) sudah dihapus.

## Ringkasan

| Layanan | Folder (Root Directory Railway) | Start (CMD Dockerfile) | Mode Railway | Pemeriksaan |
|---|---|---|---|---|
| Backend API | `src/backend` | `python serve.py` | Service web, restart `ON_FAILURE` | `GET /health` (hidup), `GET /health/ready` (basis data) |
| Cache API (API publik `/v1/routes`, `/v1/exposure-surface`; baca internal `/v1/osm`, `/v1/pollution`) | `src/backend`, `RAILWAY_DOCKERFILE_PATH=osm/Dockerfile` | `uvicorn osm.app:app` | Service web privat | `GET /health` (`HEALTHCHECK` image) |
| API Gateway | `src/gateway` | entrypoint image `nginx` | Service web publik, restart `ON_FAILURE` | `GET /healthz` |
| Data Worker | `src/data_worker` | `python main.py cycle` | Cron `15 * * * *`, restart `NEVER` | `python main.py check` |

Setiap folder memuat `Dockerfile`, `.dockerignore`, `.env.example`, dan
`railway.json` (builder, *watch path*, health check, restart, cron). Cache API
memakai folder dan berkas yang sama dengan Backend, dengan Dockerfile sendiri
(panduan: [`src/backend/osm/README.md`](../../src/backend/osm/README.md)).

Pemetaan jalur ke layanan hanya ada di gateway, dan gateway hanya membuka API
publik: `POST /v1/routes` dan `GET /v1/exposure-surface` ke Cache API, dengan
rate limit per alamat IP. Jalur lain, termasuk `/v1/osm/*`, `/v1/pollution/*`,
dan seluruh Backend, dijawab 404 oleh gateway dan hanya dapat dicapai lewat
jaringan privat (TI-DO-05, [`src/gateway/README.md`](../../src/gateway/README.md)).
Container frontend meneruskan `/v1/*` apa adanya ke gateway (`GATEWAY_UPSTREAM`),
tidak langsung ke layanan di belakangnya, sehingga request dari aplikasi web
mendapat batas waktu, `X-Request-ID`, dan rate limit yang sama dengan klien lain.

## Konfigurasi per layanan dan environment

- **Satu image untuk semua environment.** Tidak ada Dockerfile atau berkas
  konfigurasi per environment. Yang berbeda antar-environment hanya nilai
  variabel, yang diisi pada tab Variables tiap service di Railway (atau
  `src/<layanan>/.env` untuk compose).
- **`railway.json` per layanan** memuat hal yang sama di semua environment.
  Bila suatu environment memerlukan nilai lain, pakai blok `environments` di
  berkas yang sama, bukan berkas baru. **Catatan:** pada uji 9 Oktober,
  Railway membaca blok `environments.staging` (tercatat di
  `propertyFileMapping` deployment) tetapi nilai dashboard hasil duplikasi
  environment tetap menang: cron dan perintah tidak berubah. Untuk pengaturan
  deploy (cron, start command), periksa tab Settings tiap environment
  setelah deployment.
- **`.env.example` per layanan** adalah daftar variabel dan pemiliknya. Tidak
  ada berkas env global; kredensial yang dipakai beberapa layanan (DSN
  PostgreSQL) diisi di Railway sebagai referensi `${{database.DATABASE_URL}}`
  agar tidak disalin.
- **Nama environment.** Backend membaca `ENVIRONMENT` (`development`,
  `staging`, `production`; alias `DEV`, `prod`). Bila kosong dipakai
  `RAILWAY_ENVIRONMENT_NAME` yang disuntikkan Railway; environment Railway
  selain `development` dan `production` diperlakukan sebagai `staging`.
  Di luar `development`, `DATABASE_URL` wajib.
- **Rahasia tidak masuk image.** `.dockerignore` mengecualikan `.env*`
  (kecuali `.env.example`). Railway menyuntikkan variabel saat runtime.

### Variabel

| Layanan | Variabel | Wajib | Contoh Railway |
|---|---|---|---|
| Backend | `DATABASE_URL` | di luar development | `${{database.DATABASE_URL}}` |
| Backend | `PORT` | tidak (bawaan 8000) | `8000`; gateway merujuknya |
| Backend, Data Worker | `LOG_LEVEL`, `LOG_FORMAT` | tidak | `INFO`; `json` otomatis di Railway |
| Gateway | `CACHE_API_UPSTREAM` | ya | `http://${{cache-api.RAILWAY_PRIVATE_DOMAIN}}:${{cache-api.PORT}}` (sesuaikan nama service) |
| Gateway | `PORT` | tidak | disuntikkan Railway |
| Cache API | `DATABASE_URL` | ya | `${{database.DATABASE_URL}}` |
| Frontend | `GATEWAY_UPSTREAM` | tidak (bawaan `http://gateway:8080`) | `http://${{gateway.RAILWAY_PRIVATE_DOMAIN}}:${{gateway.PORT}}` |
| Data Worker | `DATABASE_URL` | ya | `${{database.DATABASE_URL}}` |
| Data Worker | `SPKU_*` | tidak | lihat [`src/data_worker/README.md`](../../src/data_worker/README.md) |

## Menjalankan secara lokal

Prasyarat: Docker. Salin `.env.example` setiap layanan menjadi `.env`
(perintahnya ada di [README](../../README.md)), lalu:

```bash
docker compose up --build -d
docker compose run --rm --no-deps data_worker python main.py check
python scripts/smoke_test.py --gateway http://localhost:8081 --backend http://localhost:8000
```

Catatan: di compose, `data_worker` berjalan dengan `serve --now` sehingga
langsung menjalankan satu siklus (121 permintaan ke portal SPKU). Untuk
menguji gateway dan backend saja, jalankan image satu per satu seperti di
bawah.

Tanpa compose (misalnya untuk membangun satu image):

```bash
docker build -t sippolusi-backend src/backend
docker build -f src/backend/osm/Dockerfile --target runtime -t sippolusi-cache-api src/backend
docker build -t sippolusi-gateway src/gateway
docker build -t sippolusi-data-worker src/data_worker
docker run --rm -p 8000:8000 --env-file src/backend/.env sippolusi-backend
docker run --rm -p 8001:8000 --env-file src/backend/.env sippolusi-cache-api
docker run --rm -p 8081:8080 -e CACHE_API_UPSTREAM=http://host.docker.internal:8001 sippolusi-gateway
docker run --rm --env-file src/data_worker/.env sippolusi-data-worker python main.py check
```

`.env` contoh memakai host `postgres`, yang hanya dikenal di jaringan compose.

## Menyiapkan service di Railway

Untuk setiap layanan: *New Service → GitHub Repo*, lalu di Settings:

1. **Source → Root Directory**: folder pada tabel ringkasan.
2. **Config-as-code**: `/src/<folder>/railway.json` bila tidak terbaca
   otomatis. Nilai yang diisi manual di dashboard mengalahkan berkas ini;
   kosongkan isian dashboard agar berkas yang berlaku.
3. **Variables**: sesuai tabel variabel di atas.
4. **Networking**: hanya **gateway** yang diberi domain publik. Backend cukup
   dijangkau lewat `backend.railway.internal`; penutupan domain publiknya
   dikerjakan pada TI-DO-04.

Environment `staging` (proyek `humble-endurance`) dibuat dengan menduplikasi
`development`, lalu hanya menyisakan `database`, `backend`, dan service baru
`gateway`. Basis datanya terpisah dan kosong saat dibuat. Data Worker tidak
dipasang permanen di staging agar portal SPKU tidak disapu dua kali per jam;
untuk mengujinya, tambahkan service-nya, kosongkan **Cron Schedule** dan isi
**Custom Start Command** `python main.py check` di Settings → Deploy sebelum
deploy, lalu baca log-nya.

Untuk Data Worker, langkah migrasi dan peralihan cron ada di
[`src/data_worker/docs/RAILWAY.md`](../../src/data_worker/docs/RAILWAY.md).

## Kegagalan build dan start

Setiap kegagalan menghentikan proses dengan pesan yang menyebut penyebab dan
cara memperbaikinya, tanpa mengulang kata sandi.

| Tahap | Pemeriksaan | Contoh pesan |
|---|---|---|
| Build backend | `python -c "import main"` | `build check failed: 'import main' raised; see the traceback above (missing package in requirements.txt?)` |
| Build Data Worker | impor modul yang dipakai siklus (`preflight.RUNTIME_MODULES`) | `build check gagal: modul 'spatial_model.inference' gagal diimpor (ModuleNotFoundError: ...); periksa requirements.txt lalu build ulang image.` |
| Build gateway | tahap `test` (`tests/run.sh`): validasi variabel, render template lewat entrypoint asli + `nginx -t`, routing ke upstream tiruan, rate limit 429, 502/504 | baris `FAIL  ...` per kasus, atau `nginx: [emerg] ...` dengan nomor baris |
| Start backend | `settings.load_settings()` sebelum port dibuka; kode keluar 2 | `- DATABASE_URL is required in staging; set it in the service variables (Railway: ${{database.DATABASE_URL}}).` |
| Start gateway | `10-validate-env.sh` sebelum template dirender; kode keluar 1. Nilai upstream yang ditolak tidak dicetak | `gateway: invalid configuration: CACHE_API_UPSTREAM is not set; Railway: http://cache-api.railway.internal:8000 (the service built from src/backend/osm/Dockerfile), compose: http://cache_api:8000` |
| Start Data Worker | `preflight.config_problems()` sebelum siklus; kode keluar 2 | `- DATABASE_URL belum diatur. Lokal: salin src/data_worker/.env.example ke .env; Railway: isi dengan referensi ${{database.DATABASE_URL}} ...` |

Backend dan Data Worker melaporkan seluruh masalah konfigurasi sekaligus;
gateway berhenti pada masalah pertama.
Basis data yang tidak terjangkau **tidak** menghentikan backend: proses tetap
hidup, `/health` tetap 200, dan `/health/ready` menjawab 503 sampai basis data
kembali, agar gangguan basis data tidak berubah menjadi gangguan seluruh API.

Kode keluar Data Worker: `0` berhasil, `1` ada langkah siklus atau
pemeriksaan yang gagal, `2` konfigurasi tidak sah (portal tidak disentuh).

## Health check

| Endpoint | Arti | Dipakai oleh |
|---|---|---|
| Gateway `GET /healthz` → 200 | Proses NGINX hidup | Health check deploy Railway (`railway.json`) |
| Backend `GET /health` → 200 | Proses hidup dan melayani | Health check deploy Railway, compose |
| Backend `GET /health/ready` → 200/503 | Basis data menjawab `SELECT 1` dalam 3 detik | Smoke test, pemantauan |
| Data Worker `python main.py check` | Konfigurasi, impor modul, koneksi + PostGIS | Sebelum mengaktifkan cron, diagnosis |

Respons readiness hanya memuat `ok`, `not_configured`, atau `unavailable`;
rincian galat ada di log. Pemisahan hidup/siap yang lebih lengkap dan sinyal
job tertinggal adalah cakupan TI-DO-08.

## Logging

- Semua layanan menulis ke **stdout**. Railway menandai stderr sebagai
  `error`; sebelumnya setiap baris INFO Data Worker tampil sebagai galat.
- Di Railway, Backend dan Data Worker menulis satu objek JSON per baris
  (`time`, `level`, `logger`, `message`), sehingga filter level di Log
  Explorer berfungsi (`@level:error`). Di lokal formatnya teks biasa; ubah
  dengan `LOG_FORMAT=json|text`.
- Kredensial disamarkan pada teks akhir, termasuk *traceback*:
  `postgresql://user:***@host`, `password=***`, `token=***`, serta nilai
  variabel yang namanya memuat `PASSWORD`, `SECRET`, `TOKEN`, `_KEY`,
  `DATABASE_URL`, atau `DSN`.
- Gateway menulis log akses JSON tanpa query string, header, isi request,
  alamat klien, atau user agent, dan menyertakan `request_id` yang sama dengan
  header `X-Request-ID` pada respons.

Membaca log: `railway logs --service <nama> --environment <env>` atau tab
Deployments → View logs. Untuk satu request, cari `request_id` dari header
`X-Request-ID`.

## Smoke test

`scripts/smoke_test.py` (pustaka standar saja) memeriksa gateway hidup,
`POST /v1/routes` dan `GET /v1/exposure-surface` lewat gateway sampai Cache API,
jalur internal (`/v1/osm/version`, `/v1/routes/solve`, `/health`) dijawab 404
oleh gateway, serta (bila `--backend` diberikan) liveness dan readiness backend.
Kode keluar 1 bila ada pemeriksaan gagal.

Pemeriksaan lewat gateway menguji bahwa layanan yang benar menjawab, bukan
bahwa datanya tersedia. Environment baru tanpa graf jalan tetap lulus selama
Cache API menjawab dengan galat seragamnya sendiri (misalnya 503
`cache_unavailable`). 502/504 dari gateway, 404 dari gateway untuk jalur
publik, atau 429 dinyatakan gagal. Smoke test mengirim empat request ke API
publik, jauh di bawah batas rate limit.

```bash
python scripts/smoke_test.py --gateway https://<domain-gateway> --backend https://<domain-backend>
```

Data Worker tidak memiliki endpoint HTTP; smoke test-nya adalah
`python main.py check` di dalam image yang sama (lokal) dan status keluar
eksekusi cron terakhir di Railway (`railway logs --service data_worker`).
