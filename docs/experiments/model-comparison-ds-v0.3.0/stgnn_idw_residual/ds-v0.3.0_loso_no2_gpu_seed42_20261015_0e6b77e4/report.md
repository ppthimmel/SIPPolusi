# IDW-ST-GNN NO2 LOSO on ds-v0.3.0

- Coverage: 13 held-out groups, 13 stations, 604 station-hour observations (LCS-21 has no test-block NO2).
- Seed 42; Colab GPU (CUDA); forecast and calendar inputs included; same notebook as the PM2.5 LOSO run (identical SHA-256).
- Each held-out group is excluded from train labels, validation labels, feature fitting and every sensor input; checkpoint selection uses validation from the other groups.
- Configuration fixed across folds; no hyperparameter search.
- Runner: `runner/run_stgnn_loso.py` and `runner/run_stgnn.py` are the PM2.5 runners with `--target`, `--data`, `--out` and `--device` options and a git-free commit fallback; the training notebook is unchanged.
- Checkpoints and per-fold predictions are not committed (Colab Drive run folder).

## Pooled test metrics

| Method | n | MAE | RMSE | Bias | R2 |
|---|---:|---:|---:|---:|---:|
| IDW p2 k8 | 604 | 38.811707 | 46.992219 | 22.101835 | 0.140714 |
| IDW p1 all | 604 | 37.449957 | 45.547466 | 11.629201 | 0.192739 |
| IDW-ST-GNN | 604 | 33.095141 | 47.168199 | 0.019378 | 0.134266 |

## Paired comparison

Bootstrap resamples held-out station groups (4,000 draws, seed 0). Differences are RMSE(ST-GNN) - RMSE(baseline).

- Versus IDW p2 k8: +0.1760; 95% interval [-14.3602, +13.0017] ug/m3.
- Versus IDW p1 all: +1.6207; 95% interval [-11.6769, +13.4995] ug/m3.

Best epoch median: 14.0; range 7–50.

Station RMSE improved at 10/13 stations versus IDW p2 k8 and at 8/13 versus IDW p1 all.

## Per station (test block)

| Station | Mean NO2 | RMSE IDW p2 k8 | RMSE IDW-ST-GNN | Bias IDW-ST-GNN |
|---|---:|---:|---:|---:|
| DKI5 | 12.2 | 87.1 | 83.1 | +80.0 |
| DKJ32 | 21.1 | 72.7 | 26.3 | +23.4 |
| DKJ34 | 24.9 | 24.0 | 14.1 | -10.7 |
| DKI4 | 27.4 | 26.3 | 16.3 | -13.8 |
| DKJ35 | 28.0 | 40.8 | 16.1 | -8.3 |
| DKJ33 | 30.3 | 29.0 | 17.7 | +12.9 |
| DKI3 | 32.8 | 18.6 | 16.4 | +12.1 |
| DKJ37 | 33.3 | 27.0 | 12.6 | -6.2 |
| DKI2 | 43.6 | 31.4 | 39.0 | -29.9 |
| DKJ36 | 54.6 | 41.6 | 25.8 | -20.0 |
| DKI1 | 111.2 | 44.7 | 83.1 | +64.9 |
| LCS-23 | 139.1 | 50.6 | 45.3 | -18.7 |
| LCS-24 | 148.0 | 60.7 | 91.8 | -86.2 |

## Interpretation

The ST-GNN removes the pooled IDW bias (+22.1 to +0.02 ug/m3), lowers MAE (38.8 to 33.1) and improves most outer government stations, but over-predicts DKI1 Bundaran HI and under-predicts LCS-24, and DKI5 stays poor. Those large errors keep the pooled RMSE at the IDW level; the interval against IDW p2 k8 is wide and includes zero. The ST-GNN inputs contain only part of the sensor geometry (nearest IDW source distance and source count, no distance-weighted sensor density), which the tabular LightGBM with all 62 columns (LOSO RMSE 27.429) uses to separate the high central cluster from the outer stations.

This is one seed on a previously inspected dataset. Confidence intervals do not include training-seed variability; the run is exploratory.
