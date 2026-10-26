# IDW-ST-GNN PM2.5 LOSO on ds-v0.3.0

- Coverage: 95 held-out groups, 97 stations, 4358 station-hour observations.
- Full test coverage: True; seed 42; RTX 3050 GPU; forecast and calendar inputs included.
- Each held-out group is excluded from train labels, validation labels, feature fitting and every sensor input.
- Checkpoint selection uses validation from the other groups, never test metrics.
- Configuration is fixed across folds; no per-fold hyperparameter search.
- GPU input caching is checked against original preprocessing before training each fold.
- Complete artifacts/checkpoints: `D:/Github/SIPPolusi/graphify-out/stgnn-ds-v0.3.0/loso_pm25_gpu_seed42_20261014_a7eabc1b` (local; not committed).
- Reproduce: `python run_stgnn_loso.py --all`; resume an interrupted batch with `--resume <run directory>`.

## Pooled test metrics

| Method | n | MAE | RMSE | Bias | R2 |
|---|---:|---:|---:|---:|---:|
| IDW p2 k8 | 4358 | 17.321997 | 23.995371 | -1.568460 | -0.039837 |
| IDW p1 all | 4358 | 16.318650 | 22.178463 | -0.615243 | 0.111672 |
| IDW-ST-GNN | 4358 | 17.860718 | 23.740317 | 2.907207 | -0.017849 |

## Paired comparison

Bootstrap resamples held-out station groups (4,000 draws, seed 0). Differences are RMSE(ST-GNN) - RMSE(baseline).

- Versus IDW p2 k8: -0.2551; 95% interval [-2.3126, +1.8483] ug/m3.
- Versus IDW p1 all: +1.5619; 95% interval [+0.2696, +2.9755] ug/m3.

Best epoch median: 9.0; range 7–26. 0 folds selected zero correction (plain IDW).

Station RMSE improved at 45/97 stations versus IDW p2 k8.

Station RMSE improved at 40/97 stations versus IDW p1 all.

This is one seed on a previously inspected dataset. Confidence intervals do not include training-seed variability, and this run is exploratory.
