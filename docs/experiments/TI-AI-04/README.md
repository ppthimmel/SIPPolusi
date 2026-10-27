# TI-AI-04: Baseline IDW pada ds-v0.3.0

Run terverifikasi: [laporan](runs/20261013T101200Z-idw-ds-v0.3.0-f6f15b8f/report.md).

| Protokol | Polutan | Observasi | RMSE (µg/m³) | R² |
|---|---|---:|---:|---:|
| LOSO | PM2.5 | 4358 | 23.995 | -0.040 |
| LOSO | NO₂ | 604 | 46.992 | 0.141 |
| Fixed split | PM2.5 | 689 | 18.745 | 0.141 |

Konfigurasi pembanding: IDW power 2, 8 tetangga. Lihat [protokol](../../PROTOKOL_EVALUASI.md) dan [perbandingan model](../model-comparison-ds-v0.3.0/README.md).
