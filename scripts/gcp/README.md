# SIPPolusi di Google Cloud (project `sippolusi`, region `asia-southeast2`)

Deployment kedua di samping Railway *development*, dibuat 17 November 2026 dari cabang `TI-DO-10b`. Basis data
mulai kosong: graf jalan di-*seed* dari repositori, *cache* polusi diisi Data Worker, dan model rute didaftarkan ulang
dari set TI-AI-09 yang di-commit. Model spasial terdaftar dan `production`: PM2.5 `spatial-downscaling-pm25:1` (ST-GNN,
ds-v0.3.0, sejak 19 November 2026) dan NO₂ `spatial-downscaling-no2:2` (XGBoost + koreksi IDW, ds-v0.4.0, sejak
28 November 2026; sebelumnya versi 1, LightGBM ds-v0.3.0, sejak 19 November 2026).

## Alamat

| Layanan | Jenis | Akses | URL |
|---|---|---|---|
| `frontend` | Cloud Run | publik | https://frontend-87531247158.asia-southeast2.run.app |
| `gateway` (NGINX) | Cloud Run | publik | https://gateway-87531247158.asia-southeast2.run.app |
| `cache-api` | Cloud Run | **internal** | https://cache-api-87531247158.asia-southeast2.run.app |
| `model-registry` (MLflow) | Cloud Run | **internal** (tanpa autentikasi; jangan dibuka) | https://model-registry-87531247158.asia-southeast2.run.app |
| `data-worker` | Cloud Run Job | Cloud Scheduler `data-worker-hourly`, `15 * * * *` UTC | |
| `model-register` | Cloud Run Job | manual (TI-DO-10b) | |
| `seed-graph`, `package-route` | Cloud Run Job | manual, sekali | `scripts/gcp/jobs/*.yaml` |
| `sippolusi-db` | Cloud SQL PostgreSQL 18 + PostGIS, `db-f1-micro`, IP privat saja | | |
| `sippolusi-mlflow-87531247158` | Cloud Storage | privat | artefak MLflow (`artifacts/`) |
| `sippolusi-seed-87531247158` | Cloud Storage | privat | graf jalan, set rute, skrip seed |
| `observability` | Cloud Run, 1 instance selalu menyala | publik (Grafana, login `admin`) | https://observability-87531247158.asia-southeast2.run.app |
| `pushgateway` | Cloud Run, 1 instance | **internal** | https://pushgateway-87531247158.asia-southeast2.run.app |

`/healthz` gateway tidak dapat dipanggil di Cloud Run (jalur berakhiran `z` dicadangkan Google). Gunakan
`/gateway-health`, misalnya `python scripts/smoke_test.py --gateway <url> --health-path /gateway-health`.

## Jaringan

- Layanan **internal** hanya menerima lalu lintas dari VPC `default` (dan dari Cloud Run lain lewat VPC). Dari internet
  jawabannya 404.
- Pemanggil memakai *Direct VPC egress* (`--vpc-egress=private-ranges-only`) ke subnet `default`. Cloud SQL dijangkau
  lewat IP privat (VPC peering `sippolusi-psa-range`).
- Zona DNS privat `run-app-private` mengarahkan `*.run.app` ke `private.googleapis.com` (199.36.153.8/30), sehingga
  panggilan ke URL layanan internal lewat VPC dan diterima oleh *ingress internal*.

## Deploy otomatis (CI/CD)

Setiap *push* ke `dev` yang lulus `ci-passed` menjalankan `.github/workflows/deploy-gcp.yml`: membangun lima image
bertag SHA commit, menjalankan `scripts/gcp/deploy.sh all`, *smoke test* lewat gateway, lalu memindahkan tag `:latest`
ke image yang baru lulus itu (`scripts/gcp/retag-latest.sh`).

- **Commit lama tidak menimpa yang baru.** Tepat sebelum *deploy*, workflow memeriksa bahwa commit-nya masih ujung `dev`.
  Bila tidak, *deploy* dilewati dan *run* commit yang lebih baru yang men-*deploy*.
- **Jalankan manual** (Actions > Deploy to GCP > Run workflow, cabang `dev`) ditolak bila `ci-passed` tidak berhasil untuk
  commit itu.
- **Job sekali jalan** (`scripts/gcp/jobs/*.yaml`) memakai `:latest`, yaitu image terakhir yang lulus *deploy*. Cloud Run
  mengunci image saat `gcloud run jobs replace`, jadi jalankan `replace` lagi sebelum `execute` untuk memakai image baru.

GitHub masuk ke GCP tanpa kunci lewat *Workload Identity Federation*: pool `github`, provider `sippolusi-dev`, yang hanya
menerima workflow `deploy-gcp.yml` dari repositori ini (`repository_id` 1386978923) pada `refs/heads/dev`. Syaratnya
tersimpan di `scripts/gcp/wif-provider.yaml`; setelah berkas itu berubah, terapkan dengan:

```bash
gcloud iam workload-identity-pools providers update-oidc sippolusi-dev --project sippolusi --location global \
  --workload-identity-pool github --flags-file scripts/gcp/wif-provider.yaml
```

Akun `sippolusi-deployer` memegang `roles/run.admin`, `roles/compute.networkViewer`, `roles/artifactregistry.writer` pada
repositori image, dan `roles/iam.serviceAccountUser` pada `sippolusi-app` dan `sippolusi-registry`. Tidak ada rahasia di
GitHub.

## Rahasia dan akun layanan

- Secret Manager: `database-url`, `mlflow-backend-store-uri`. Kata sandi dibuat acak saat penyiapan dan tidak pernah
  ditulis ke repositori atau *chat*.
- Secret Manager: `earthdata-token`, token Earthdata Login untuk VIIRS VNP46A2 (`EARTHDATA_TOKEN` pada Data Worker).
  Token berlaku 60 hari. Perpanjang dengan menambah versi baru, tanpa *deploy* ulang (job memakai `:latest`):
  `printf '%s' "$TOKEN" | gcloud secrets versions add earthdata-token --project=sippolusi --data-file=-`.
  Bila token kedaluwarsa, sumber VIIRS (diperiksa sekali sehari) gagal, sehingga siklus itu tercatat `failed` dan
  eksekusi job keluar dengan galat, walaupun langkah lain tetap berjalan dan estimasi tetap ditulis.
- `sippolusi-registry`: menulis/membaca *bucket* MLflow (satu-satunya yang memegang akses ke artefak, seperti di Railway).
- `sippolusi-app`: Cache API, gateway, frontend, Data Worker, `model-register`. Tanpa akses ke *bucket*.
- `sippolusi-tools`: *job* seed dan pengemasan, serta pemicu Cloud Scheduler.
- `sippolusi-observability`: layanan `observability` dan `pushgateway`; `roles/monitoring.viewer` untuk membaca metrik
  Cloud Monitoring, akses ke secret `grafana-admin-password` (dibuat acak oleh `deploy.sh observability-setup`; baca
  dengan `gcloud secrets versions access latest --secret=grafana-admin-password`) dan, bila ada, `alertmanager-config`
  dan `alertmanager-smtp-password`.

## Perintah

```bash
# image (satu per layanan): context = folder layanan
gcloud builds submit src/data_worker --config scripts/gcp/cloudbuild.yaml --region asia-southeast2 \
  --substitutions _IMAGE=data-worker
# deploy / update layanan dan job
scripts/gcp/deploy.sh <registry|cache-api|gateway|frontend|data-worker|model-register|all>
# job sekali jalan
gcloud run jobs replace scripts/gcp/jobs/seed-graph.yaml --region asia-southeast2
gcloud run jobs execute seed-graph --region asia-southeast2 --wait
```

### Observability (issue #112)

Tidak termasuk `all` dan tidak dijalankan CI: revisi baru me-*restart* Prometheus, yang datanya ada di memori instance.
Rinciannya di [`docs/devops/observability-gcp.md`](../../docs/devops/observability-gcp.md).

Image diberi tag commit asalnya, sehingga revisi yang berjalan dapat ditelusuri ke kode; `deploy.sh observability`
menolak berjalan tanpa `OBS_IMAGE_TAG`. Jalankan dari *working tree* yang bersih pada commit yang sudah di-*push*.

```bash
TAG=$(git rev-parse --short=12 HEAD)
for img in prometheus alertmanager pushgateway grafana; do
  gcloud builds submit src/observability/$img --config scripts/gcp/cloudbuild.yaml --region asia-southeast2 \
    --substitutions _IMAGE=$img,_TAG=$TAG
done
scripts/gcp/deploy.sh observability-setup                      # sekali, oleh admin: akun layanan, secret, metrik log
OBS_IMAGE_TAG=$TAG scripts/gcp/deploy.sh observability          # pushgateway + observability
CACHE_API_SCRAPE=1 OBS_IMAGE_TAG=$TAG scripts/gcp/deploy.sh observability   # hanya selama uji beban (#111)
```

**Notifikasi email (Gmail).** Kata sandi SMTP tidak pernah masuk ke berkas konfigurasi atau repositori:

1. Buat *app password* untuk akun Gmail pengirim (Google Account > Security > 2-Step Verification > App passwords).
2. Simpan sebagai secret tanpa menampilkan atau menulisnya ke berkas (Git Bash; tempel 16 hurufnya tanpa spasi, lalu
   Enter): `read -rs P && printf '%s' "$P" | gcloud secrets create alertmanager-smtp-password --data-file=- && unset P`
   (mengganti: `gcloud secrets versions add alertmanager-smtp-password --data-file=-`).
3. Salin `src/observability/alertmanager/alertmanager.example.yml`, isi alamat pengirim dan penerima, lalu
   `gcloud secrets create alertmanager-config --data-file=<berkas>` dan hapus salinannya.
4. `scripts/gcp/deploy.sh observability-setup` (akses secret) lalu `OBS_IMAGE_TAG=<tag> scripts/gcp/deploy.sh observability`.

Tanpa `alertmanager-config` alert hanya tampil di Grafana.

### Mendaftarkan model (TI-DO-10b)

1. Unggah folder paket ke `gs://sippolusi-mlflow-87531247158/artifacts/incoming/<...>/`.
2. `gcloud run jobs execute model-register --region asia-southeast2 --update-env-vars REGISTER_PREFIX=incoming/<...>/ --wait`
3. Promosi (keputusan tim): `... --update-env-vars PROMOTE=<pm25|no2|route>:<versi>`.

Variabel `--update-env-vars` hanya berlaku untuk eksekusi itu, jadi tidak ada nilai yang tertinggal.

## Verifikasi (17 November 2026)

- `seed-graph`: `writing jakarta-20261005: 299067 nodes, 373352 edges` (id *edge* sama dengan Railway).
- `package-route` lalu `model-register` (`REGISTER_PREFIX=incoming/route/20261102T145533Z-multi-policy-bike-v1-201e7d41/`):
  `registered: morl-route-policy version 1 aliases candidate`, `RESOLVED morl-route-policy:1: all 23 files + package.json
  match.  RESULT: PASS`, `package_sha256 7774d98c...`. Diulang: `already registered ... nothing registered`.
  `PROMOTE=route:1`: `promoted: morl-route-policy:1 is production`.
- `data-worker` (eksekusi pertama): `run_status: complete`, status `degraded` karena PM2.5 dan NO₂ memakai IDW
  (`resolve_registry:RestException`: modelnya belum terdaftar di registry GCP).
- `python scripts/smoke_test.py --gateway https://gateway-87531247158.asia-southeast2.run.app --health-path /gateway-health`:
  6/6 (18 November 2026).
- `POST /v1/routes` (sepeda, Monas ke Bundaran HI) lewat gateway: HTTP 200, `route_source: policy`, `solver: morl`,
  `route_model: morl-route-policy:1`, `time_window_start: 2026-11-17T05:00:00Z`, `estimation.source: idw`.
- `GET /v1/exposure-surface` lewat gateway dan lewat `frontend` (proxy `/v1/`): HTTP 200, 1.384 sel.
- `cache-api` dan `model-registry` dipanggil langsung dari internet: 404 (ingress internal).

## Model PM2.5 dan NO₂ versi 1 (18–19 November 2026)

Dicatat 28 November 2026 dari log *job* `model-register`:

- PM2.5 (`model-register-gdj2p`): `verified: spatial-downscaling-pm25 ds-v0.3.0-723c4558fd2f ... package_sha256
  ce84b2d719560416... 13 files + package.json`, `registered: spatial-downscaling-pm25 version 1 aliases candidate`,
  `RESULT: PASS`. Promosi (`model-register-g9fw8`): `promoted: spatial-downscaling-pm25:1 is production`.
- NO₂ LightGBM (`model-register-xbqj5`): `verified: spatial-downscaling-no2 ds-v0.3.0-f658e5d2feb9 ... package_sha256
  99ab5fcedd1752c0... 5 files + package.json`, `registered: spatial-downscaling-no2 version 1 aliases candidate`,
  `RESULT: PASS`. Promosi (`model-register-l6sg4`): `promoted: spatial-downscaling-no2:1 is production`.

## Model NO₂ (28 November 2026)

XGBoost + koreksi IDW `ds-v0.4.0-9734d6e298ef` (PR #115, pemilihan model di
[`docs/experiments/model-comparison-ds-v0.4.0/`](../../docs/experiments/model-comparison-ds-v0.4.0/README.md)).
Image *job* `data-worker:fb30ad5` (`dev` setelah PR #115).

- `REGISTER_PREFIX=incoming/spatial/no2/ds-v0.4.0-9734d6e298ef/` (eksekusi `model-register-msddx`):
  `verified: ... bundle_sha256 9734d6e298ef6ec3... package_sha256 939f2ddfe7cc76e9... 5 files + package.json`,
  `registered: spatial-downscaling-no2 version 2 aliases candidate`,
  `RESOLVED spatial-downscaling-no2:2: all 5 files + package.json match.  RESULT: PASS`.
- `PROMOTE=no2:2` (eksekusi `model-register-btfsr`): `promoting spatial-downscaling-no2 version 2 (production was 1)`,
  `promoted: spatial-downscaling-no2:2 is production`.

## Belum dikerjakan

- Periksa bahwa siklus Data Worker berikutnya memakai `spatial-downscaling-no2:2` untuk NO₂ (bukan IDW).
- Data lama Railway (riwayat *cache* polusi, `ground_truth`, metadata MLflow) tidak dipindahkan.
- Observability terpasang (issue #112), tetapi penerima notifikasi alert belum diisi (secret `alertmanager-config`), dan
  Data Worker baru mengirim metrik ke Pushgateway setelah kode #112 di-*deploy* dari `dev`.
- *Deploy* otomatis belum pernah berjalan: ia aktif setelah PR-nya masuk ke `dev`, dan izin `sippolusi-deployer` baru
  teruji pada *run* pertama itu.
- `backend` (API lama, `src/backend/Dockerfile`) tidak di-*deploy*; Railway juga tidak menjalankannya.

## Biaya

GCP **tidak** punya batas keras seperti Railway: atur *budget alert* di Billing. Yang berjalan terus adalah Cloud SQL
`db-f1-micro`, layanan `observability` (instance-based billing, CPU selalu dialokasikan) dan satu instance `pushgateway`;
layanan Cloud Run lain berskala ke nol saat tidak dipakai, dan Data Worker dibayar per eksekusi. Probe sintetis setiap
60 detik membuat `gateway` dan `frontend` tetap punya instance aktif, tetapi keduanya ditagih per request.

## Windows

`gcloud.cmd` salah mengurai argumen yang berisi spasi bila dipanggil dari Git Bash. Jalankan perintah semacam itu dari
PowerShell, atau taruh konfigurasi di berkas YAML (`scripts/gcp/jobs/`).
