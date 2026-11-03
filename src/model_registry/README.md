# Model Registry

MLflow 2.17.2 menyimpan run, parameter, metrik, versi dan alias. PostgreSQL menyediakan schema
`mlflow`; MinIO menyimpan payload pada `s3://mlflow/artifacts` melalui API S3.

| Nama model | Peran |
|---|---|
| `spatial-downscaling-pm25` | IDW + koreksi ST-GNN PM2.5 |
| `spatial-downscaling-no2` | Full-feature LightGBM NO₂ |
| `morl-route-policy` | Policy rute |

Training membuat versi dan alias `candidate`. Promotion memverifikasi model sebelum menetapkan
`production`. Worker hanya membaca, menyelesaikan alias sekali per run, dan memverifikasi checksum
artefak. Kredensial berasal dari environment. [Panduan lengkap](../data_worker/spatial_model/README.md).

## Artefak lewat proxy (Railway)

Server dijalankan dengan `--serve-artifacts`: klien (Worker, backend, mesin pelatihan) hanya perlu
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
langsung, dipakai compose lokal).

Setiap model diunggah sebagai paket dengan `package.json` (format dan kompatibilitas:
[TI-AI-11](../../docs/experiments/TI-AI-11/README.md)). Perintah:

```bash
python -m spatial_model.training package <bundle> --graph-version jakarta-20261005
python -m spatial_model.training register <bundle>
python -m spatial_model.training register-route <direktori paket route>
```
