# Model Registry

MLflow 2.17.2 menyimpan run, parameter, metrik, versi dan alias. PostgreSQL menyediakan schema
`mlflow`; payload artefak disimpan di *object storage* melalui API S3: lokal kontainer MinIO
(`s3://mlflow/artifacts`), di Railway Storage Bucket (lihat di bawah).

| Nama model | Peran |
|---|---|
| `spatial-downscaling-pm25` | IDW + koreksi ST-GNN PM2.5 |
| `spatial-downscaling-no2` | Full-feature LightGBM NO₂ |
| `morl-route-policy` | Policy rute |

Training membuat versi dan alias `candidate`. Promotion memverifikasi model sebelum menetapkan
`production`. Worker hanya membaca, menyelesaikan alias sekali per run, dan memverifikasi checksum
artefak. Kredensial berasal dari environment. [Panduan lengkap](../data_worker/spatial_model/README.md).

## Artefak lewat proxy (Railway)

Server dijalankan dengan `--serve-artifacts`: klien (Data Worker, Cache API, mesin pelatihan) hanya perlu
`MLFLOW_TRACKING_URI`; unggah dan unduh artefak lewat server ini. Hanya service ini yang memegang kredensial
object storage. Variabel service `model_registry` (nilai rahasia dari bucket Railway, jangan disalin ke repo):

| Variabel | Nilai |
|---|---|
| `MLFLOW_ARTIFACT_ROOT` | `mlflow-artifacts:/` |
| `MLFLOW_ARTIFACTS_DESTINATION` | `s3://<nama bucket S3>/artifacts` |
| `MLFLOW_S3_ENDPOINT_URL` | endpoint bucket (mis. `https://t3.storageapi.dev`) |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | kunci bucket yang sama |
| `AWS_DEFAULT_REGION` | `auto` |

Nama bucket S3 berbeda dari nama bucket di dashboard Railway; ambil dari `railway bucket credentials`.
Bila `MLFLOW_ARTIFACTS_DESTINATION` tidak diatur, tujuan mengikuti `MLFLOW_ARTIFACT_ROOT` (pengaturan lama dengan `s3://`
langsung; kini hanya dipakai `docker-compose.spatial-verification.yml`).

## Mendaftarkan model dari *bucket* (Railway, TI-DO-10b)

Registry hanya dapat dijangkau lewat *private networking*, sehingga pendaftaran di Railway berjalan sebagai *job*
sekali jalan di dalam jaringan itu. *Job* dibangun dari image Data Worker (sudah berisi `spatial_model`) dan membaca
paket **lewat *artifact proxy* registry** (`mlflow-artifacts:/`): ia hanya butuh `MLFLOW_TRACKING_URI` dan tidak
memegang kunci S3. Tidak ada *script* yang ditempel ke Console.

```bash
python -m spatial_model.training register-from-bucket incoming/spatial/pm25/<model_version>/
```

Perintah itu: menunggu registry bangun (`GET /health`, sampai 120 detik), mengunduh semua berkas di bawah awalan,
memeriksa `bundle.json` (bundel spasial) dan `package.json` (hash setiap berkas, tidak ada berkas yang tidak
terdaftar, tidak ada kredensial, bukan fold LOSO), menulis `package.json` bila bundel spasial belum punya,
mendaftarkan lewat `register` / `register-route` (tag `bundle_sha256` dan/atau `package_sha256`, alias `candidate`),
lalu me-*resolve* versi baru lewat registry dan membandingkan hash-nya.

- **Tidak ada versi setengah jalan.** Pemeriksaan selesai sebelum apa pun diunggah. Bila langkah setelah versi
  dibuat gagal (alias, *resolve*, hash), versi itu dihapus lagi. *Run* MLflow yang gagal tetap tercatat dengan status
  `FAILED`, tanpa versi model.
- **Tidak ada versi ganda.** Paket dengan `package_sha256` yang sama, atau bundel spasial dengan `bundle_sha256` yang
  sama (mengemas ulang bundel menghasilkan `package.json` lain), dilaporkan `already registered` dan tidak didaftarkan.
- **Tanpa kunci S3.** Variabel `AWS_*` dan `MLFLOW_S3_ENDPOINT_URL` dibuang dari proses sebelum MLflow dipakai, dan
  perintah menolak mengunggah bila eksperimen menyimpan artefak langsung ke S3, bukan lewat *proxy*.
- **Promosi tetap manual.** Pendaftaran hanya memberi `candidate`. `production` diberikan lewat `promote` (di bawah).

### Sekali saja: service *job* `model-register`

Prasyarat: `database` dan `model_registry` berjalan (registry menyimpan metadata di `database`). Bila *workspace*
melewati *hard limit* pemakaian, Railway menghapus semua *deployment* dan *job* ini tidak dapat berjalan.

1. Di environment *development*, buat service baru dari repositori yang sama: **Root Directory** `src/data_worker`,
   **Config-as-code path** `/src/data_worker/railway.register.json`. Wajib berkas ini: tanpa itu Railway memakai
   `railway.json` milik Data Worker (cron `15 * * * *` dan `python main.py cycle`), sehingga service ini ikut
   menjalankan siklus akuisisi. `railway.register.json` menetapkan *start command*
   `python -m spatial_model.training railway-job`, *restart policy* `NEVER`, tanpa cron.
2. Variabel: `MLFLOW_TRACKING_URI=http://modelregistry.railway.internal:5000`. Tanpa `AWS_*`, tanpa
   `DATABASE_URL`, tanpa domain publik.
3. Deploy pertama tanpa `REGISTER_PREFIX` / `PROMOTE` hanya mencetak `nothing to do` lalu berhenti.

`railway-job` membaca satu instruksi per deploy: `REGISTER_PREFIX` (daftarkan) **atau** `PROMOTE` (promosikan);
keduanya sekaligus ditolak.

### Setiap pendaftaran

1. **Buat paket dan periksa secara lokal.** Bundel spasial dari `export_bundle` mendapat `package.json` lewat
   `python -m spatial_model.training package <bundle> [--grid-spec-id <id>]`. Bila belum, *job* mengemasnya; set
   `REGISTER_GRID_SPEC_ID=<id>` pada *job* agar paket itu memuat `grid_spec_id`. Paket model rute dibuat dengan `route_model.morl.packaging.package_policy_set`. Lalu jalankan
   `python scripts/verify_model_packages.py spatial --bundle <bundle> --target pm25` (atau `route`).
2. **Unggah foldernya ke *bucket*** `minio` (environment *development*, tab **Files**) **di bawah `artifacts/`**,
   misalnya `artifacts/incoming/spatial/pm25/<model_version>/` atau `artifacts/incoming/route/<model_version>/`.
   *Proxy* hanya melihat isi `artifacts/`; folder di luar itu (mis. `incoming/` lama di akar *bucket*) tidak terbaca.
   Unggah folder apa adanya. Jangan menempelkan kunci S3 di *chat* atau repositori.
3. **Jalankan *job*:** pada service `model-register` set `REGISTER_PREFIX=incoming/spatial/pm25/<model_version>/`
   (tanpa `artifacts/`), lalu **Deploy**.
4. **Registry *sleeping*.** Lalu lintas *private networking* dari *job* membangunkannya; *job* menunggu `GET /health`
   sampai 120 detik. Bila tetap gagal (`Registry not reachable`), bangunkan secara manual dari Console `cache-api`:
   `curl -m 90 -sS -o /dev/null -w "%{http_code}\n" http://modelregistry.railway.internal:5000/health` (jawaban `200`,
   10–30 detik), lalu **Redeploy** *job*. Console dan SSH ke `model_registry` sendiri tidak membangunkannya.
5. **Baca log deploy.** Hasil yang diharapkan:
   ```
   downloaded N files from mlflow-artifacts:/incoming/...
   verified: spatial-downscaling-pm25 ds-v0.3.0-... bundle_sha256 ... package_sha256 ... N files
   registered: spatial-downscaling-pm25 version <n> aliases candidate
   RESOLVED spatial-downscaling-pm25:<n>: all N files match.  RESULT: PASS
   ```
   diikuti satu baris JSON (`status`, `name`, `version`, `bundle_sha256`, `package_sha256`). Kegagalan berakhir
   dengan `railway-job: FAILED: <alasan>` dan kode keluar 1; tidak ada versi yang tertinggal dan `candidate` tetap
   pada versi sebelumnya.
6. **Hapus `REGISTER_PREFIX` dan `REGISTER_GRID_SPEC_ID`.** Bila `REGISTER_PREFIX` tertinggal, deploy ulang (mis. setelah
   *push* ke `dev`) hanya melaporkan `already registered`.

### Promosi (`production`)

Keputusan tim, terpisah dari pendaftaran. Setelah versi diperiksa (untuk model rute: muat ulang dengan
`scripts/verify_model_packages.py route`, karena Data Worker tidak memuat bobot rute), set
`PROMOTE=<pm25|no2|route>:<versi>` pada service `model-register` lalu **Deploy**. `promote` me-*resolve* dan memverifikasi
versi itu (bundel spasial juga dimuat dengan `load_model`) sebelum memberi alias `production`.

Hanya versi yang saat itu memegang `candidate` yang dapat dipromosikan, dan versi yang sudah `production` tidak diubah.
Jadi `PROMOTE` yang tertinggal tidak berbahaya: deploy ulang tidak mengubah apa pun, dan setelah ada pendaftaran yang
lebih baru nilai lama itu ditolak alih-alih memindahkan `production` mundur. Tetap hapus `PROMOTE` setelah selesai.
*Rollback* ke versi lama (bukan `candidate`) tidak dilayani *job* ini.

Di luar Railway (registry lokal) perintah yang sama dijalankan langsung:
`python -m spatial_model.training promote route <versi>` atau `promote pm25 <versi>`.

## Lokal (docker compose)

`docker-compose.yml` menjalankan registry dengan mode *proxy* yang sama, terhadap kontainer `minio` (URL *path-style*,
*bucket* `mlflow`, region `us-east-1`): lihat [`.env.example`](.env.example). Layanan lain hanya diberi
`MLFLOW_TRACKING_URI=http://model_registry:5000`. Untuk mendaftarkan model rute dan bundel spasial ke registry lokal:

```bash
docker compose --profile tools run --rm register_models
```

Cache API yang sudah berjalan memuat *policy* sendiri pada pemeriksaan berikutnya (paling lambat 60 detik di stack
lokal), tanpa *restart*.

Langkah lengkapnya, termasuk *seed* graf jalan, ada di
[docs/devops/local-railway-parity.md](../../docs/devops/local-railway-parity.md).

Setiap model diunggah sebagai paket dengan `package.json` (format dan kompatibilitas:
[TI-AI-11](../../docs/experiments/TI-AI-11/README.md)). Perintah:

```bash
python -m spatial_model.training package <bundle> [--grid-spec-id <id>]
python -m spatial_model.training register <bundle>
python -m spatial_model.training register-route <direktori paket route>
python -m spatial_model.training register-from-bucket <awalan di bawah artifacts/>
python -m spatial_model.training promote <pm25|no2|route> <versi>
```

**Tanpa autentikasi.** Dengan `--serve-artifacts`, siapa pun yang bisa menjangkau server MLflow dapat mengunggah,
mengunduh, dan menghapus artefak di *bucket* lewat server ini; MLflow tidak memakai autentikasi secara bawaan.
Ini hanya dapat diterima selama `model_registry` tidak punya domain publik di Railway dan hanya dijangkau lewat
*private networking* (`modelregistry.railway.internal`). Periksa hal ini sebelum mengatur variabel di atas.

`register_route_package` / `resolve_route_package` berada di Data Worker. Cache API tidak mengimpornya: sejak
TI-SE-07b ia punya *resolver* sendiri ([`src/backend/solver/registry.py`](../backend/solver/registry.py), unduh paket
dari *registry* dan verifikasi checksum) yang dipakai penyedia `morl`
([`src/backend/solver/morl.py`](../backend/solver/morl.py)) untuk memuat `morl-route-policy`.

## Lihat juga

- [`src/data_worker/spatial_model/artifacts/staged.py`](../data_worker/spatial_model/artifacts/staged.py): perintah `register-from-bucket` dan `railway-job`
- [`scripts/railway/README.md`](../../scripts/railway/README.md): prosedur lama lewat Console (diganti bagian di atas)
- [`docs/experiments/TI-AI-11/railway_registry_evidence.md`](../../docs/experiments/TI-AI-11/railway_registry_evidence.md): bukti registry di Railway
- [`src/backend/solver/README.md`](../backend/solver/README.md#penyedia-morl-ti-se-07b): pemakai registry: penyedia `morl` di Cache API
- [`src/model_registry/NAMING_CONVENTION.md`](NAMING_CONVENTION.md): konvensi nama run dan model
