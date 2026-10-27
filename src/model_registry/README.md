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
