# Local GPU ST-GNN run on ds-v0.3.0

- Target/protocol: PM2.5 fixed split; seed 42.
- Model: IDW p=2, k=8 plus a learned ST-GNN correction; forecast and calendar features included.
- GPU: NVIDIA GeForce RTX 3050 Laptop GPU; PyTorch 2.10.0+cu128; PyG 2.8.1.
- Training: 13 epochs; best epoch 1; patience 12; strict deterministic algorithms.
- Runtime: 170.7 seconds; peak tensor allocation 378.0 MiB.
- Checkpoint and full prediction artifacts: `D:/Github/SIPPolusi/graphify-out/stgnn-ds-v0.3.0/fcst_calendar_gpu_seed42_20261014_847559c6` (local, not committed).
- Reproduction: `run_stgnn.py` with `train_stgnn_ds_v0.3.0.ipynb`; exact hashes/configurations in `run_manifest.json`.

| Split | Method | n | MAE | RMSE | Bias | R2 |
|---|---|---:|---:|---:|---:|---:|
| val | IDW | 1008 | 14.345273 | 19.969657 | -4.364580 | 0.251134 |
| val | IDW-ST-GNN | 1008 | 14.269092 | 19.833912 | -3.628212 | 0.261280 |
| test | IDW | 689 | 13.950790 | 18.744843 | -8.272305 | 0.140886 |
| test | IDW-ST-GNN | 689 | 13.704040 | 18.372117 | -7.439405 | 0.174712 |

Test RMSE decreased by 1.99% against IDW p=2, k=8 on identical observations.
Station RMSE improved at 11/15 test stations.
Paired station-cluster bootstrap 95% interval for RMSE(model) - RMSE(IDW): [-0.5807, -0.1044] ug/m3.

Validation selected the checkpoint; training loss continued decreasing while validation worsened after epoch 1.
This is one seed and a fixed-split experiment, not LOSO. The dataset's test results were already inspected
during development, so this is an exploratory result rather than an independent final holdout claim.
The small early-epoch correction does not establish that graph or temporal learning adds useful information;
ablations and comparison with a train/validation-fitted constant correction are still needed.
For diagnosis only, adding a constant equal to this run's mean test correction yields RMSE 18.392468.
That constant uses test predictions and is not a validation-selected deployable baseline.
The tuned IDW p=1/all result elsewhere in this branch has fixed-split test RMSE 18.879387; its primary
LOSO result 22.178463 uses different observations and must not be compared directly to this run.
