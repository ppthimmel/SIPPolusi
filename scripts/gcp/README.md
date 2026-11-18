# SIPPolusi di Google Cloud (project `sippolusi`, region `asia-southeast2`)

Deployment kedua di samping Railway *development*, dibuat 17 November 2026 dari cabang `TI-DO-10b`. Basis data
mulai kosong: graf jalan di-*seed* dari repositori, *cache* polusi diisi Data Worker, dan model rute didaftarkan ulang
dari set TI-AI-09 yang di-commit. Model PM2.5 dan NO₂ **belum** ada (bundelnya hanya di *bucket* Railway); Data Worker
memakai *fallback* IDW sampai keduanya diunggah dan didaftarkan.

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
bertag SHA commit, menjalankan `scripts/gcp/deploy.sh all`, lalu *smoke test* lewat gateway.

GitHub masuk ke GCP tanpa kunci lewat *Workload Identity Federation*: pool `github`, provider `sippolusi-dev`, yang hanya
menerima repositori ini (`repository_id` 1386978923) pada `refs/heads/dev`. Akun `sippolusi-deployer` memegang
`roles/run.admin`, `roles/compute.networkViewer`, `roles/artifactregistry.writer` pada repositori image, dan
`roles/iam.serviceAccountUser` pada `sippolusi-app` dan `sippolusi-registry`. Tidak ada rahasia di GitHub.

## Rahasia dan akun layanan

- Secret Manager: `database-url`, `mlflow-backend-store-uri`. Kata sandi dibuat acak saat penyiapan dan tidak pernah
  ditulis ke repositori atau *chat*.
- `sippolusi-registry`: menulis/membaca *bucket* MLflow (satu-satunya yang memegang akses ke artefak, seperti di Railway).
- `sippolusi-app`: Cache API, gateway, frontend, Data Worker, `model-register`. Tanpa akses ke *bucket*.
- `sippolusi-tools`: *job* seed dan pengemasan, serta pemicu Cloud Scheduler.

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

## Belum dikerjakan

- Model PM2.5 dan NO₂: unggah bundelnya (dari *bucket* Railway, setelah batas Railway dinaikkan) ke
  `artifacts/incoming/spatial/<pm25|no2>/<versi>/`, daftarkan dengan `model-register`, lalu promosikan.
- Data lama Railway (riwayat *cache* polusi, `ground_truth`, metadata MLflow) tidak dipindahkan.
- Observability (Grafana/Prometheus) belum dipasang; log tersedia di Cloud Logging.
- *Deploy* otomatis belum pernah berjalan: ia aktif setelah PR-nya masuk ke `dev`, dan izin `sippolusi-deployer` baru
  teruji pada *run* pertama itu.
- `backend` (API lama, `src/backend/Dockerfile`) tidak di-*deploy*; Railway juga tidak menjalankannya.

## Biaya

GCP **tidak** punya batas keras seperti Railway: atur *budget alert* di Billing. Yang berjalan terus hanya Cloud SQL
`db-f1-micro`; layanan Cloud Run berskala ke nol saat tidak dipakai, dan Data Worker dibayar per eksekusi.

## Windows

`gcloud.cmd` salah mengurai argumen yang berisi spasi bila dipanggil dari Git Bash. Jalankan perintah semacam itu dari
PowerShell, atau taruh konfigurasi di berkas YAML (`scripts/gcp/jobs/`).
