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

## Lokal (docker compose)

`docker-compose.yml` menjalankan registry dengan mode *proxy* yang sama, terhadap kontainer `minio` (URL *path-style*,
*bucket* `mlflow`, region `us-east-1`): lihat [`.env.example`](.env.example). Layanan lain hanya diberi
`MLFLOW_TRACKING_URI=http://model_registry:5000`. Untuk mendaftarkan model rute dan bundel spasial ke registry lokal:

```bash
docker compose --profile tools run --rm register_models
docker compose restart cache_api   # Cache API memuat policy saat mulai
```

Langkah lengkapnya, termasuk *seed* graf jalan, ada di
[docs/devops/local-railway-parity.md](../../docs/devops/local-railway-parity.md).

Setiap model diunggah sebagai paket dengan `package.json` (format dan kompatibilitas:
[TI-AI-11](../../docs/experiments/TI-AI-11/README.md)). Perintah:

```bash
python -m spatial_model.training package <bundle> [--grid-spec-id <id>]
python -m spatial_model.training register <bundle>
python -m spatial_model.training register-route <direktori paket route>
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

- [`scripts/railway/README.md`](../../scripts/railway/README.md): prosedur pendaftaran dari bucket Railway
- [`docs/experiments/TI-AI-11/railway_registry_evidence.md`](../../docs/experiments/TI-AI-11/railway_registry_evidence.md): bukti registry di Railway
- [`src/backend/solver/README.md`](../backend/solver/README.md#penyedia-morl-ti-se-07b): pemakai registry: penyedia `morl` di Cache API
- [`notebooks/spatial_model/NAMING_CONVENTION.md`](../../notebooks/spatial_model/NAMING_CONVENTION.md): konvensi nama run dan model
