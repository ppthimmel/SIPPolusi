# Local model checkpoints

This inventory records the TI-AI-05 workstation as of 2026-11-03. Ignored model
files, `.cache/` receipts and status files are local evidence and are not included
in another clone. Training status below is a dated observation, not a live job monitor.

Store local training artifacts under `checkpoints/spatial_model/<family>/<target>/<protocol>/<run-id>/`.
Use `fixed_split` for held-out evaluation, `loso` for leave-one-group-out evaluation,
and `serving_fit` for a fit using all training stations. These protocols are not interchangeable.
Run directories are ignored by Git; this inventory is tracked. Do not store model artifacts in `graphify-out/`.

## Verified historical full runs

| Model | Target | Protocol | Local artifacts |
| --- | --- | --- | --- |
| ST-GNN with IDW correction | PM2.5 | Held-out / fixed split | `spatial_model/stgnn/pm25/fixed_split/fcst_calendar_gpu_seed42_20261014_847559c6/`: `best.pt`, `progress.pt`, predictions, history and manifest |
| ST-GNN with IDW correction | PM2.5 | LOSO | `spatial_model/stgnn/pm25/loso/loso_pm25_gpu_seed42_20261014_a7eabc1b/`: 95 fold directories containing `best.pt` and `progress.pt`, plus evaluation artifacts |

Paths above are relative to this directory. The held-out run completed 13 epochs with
early stopping and selected epoch 1; it is not a one-epoch smoke run.

## LightGBM historical comparison

Full saved checkpoints were not found locally for PM2.5 held-out, PM2.5 LOSO, or NO2
LOSO. The historical `fixed.py::fit_predict` fits a model and returns predictions
without serializing it; `loso.py` and `idw_correction.py` reuse that function.
The following files exist under `docs/experiments/model-comparison-ds-v0.3.0/results/`:

| Target / protocol | Available evidence |
| --- | --- |
| PM2.5 held-out | `fixed_metrics.csv`, `fixed_state.json` (selected configurations) |
| PM2.5 LOSO | `loso_pm25_metrics.csv`, `loso_pm25_val_metrics.csv`, `summary_loso_pm25.csv` |
| NO2 LOSO | `loso_no2_metrics.csv`, `loso_no2_val_metrics.csv`, `summary_loso_no2.csv` |

The comparison prediction Parquet files were not found in that results directory.
Metrics and configurations are not checkpoints. Recovering fitted models for these
historical comparisons requires retraining with model serialization enabled.
The `20261102T164517-smoke` checkpoint folders were deleted on 2026-11-03.
Other NO2 model files under
`.cache/` are verification or serving artifacts, not an identified complete LOSO run.

## Relocation on 2026-11-03

The two full ST-GNN runs above were moved from `graphify-out/stgnn-ds-v0.3.0/`
with their complete directory contents. SHA-256 was checked for every file before
and after moving. A separate incomplete run containing only `progress.pt`,
`fcst_calendar_seed42_20261014_e3b4b0ce`, was also preserved under
`spatial_model/stgnn/pm25/fixed_split/`; it is not a completed full run.

Original run manifests, notebooks, logs, and source hashes inside the relocated
artifacts retain their historical values. Old paths in those provenance records
describe the original run; they are not current output locations. The local
file-by-file relocation receipt is `.cache/checkpoint-relocation-20261103.json`.

The maintained training notebooks now default to this checkpoint tree. CLI training
uses the explicit `--out` path supplied by the caller.

## XGBoost artifacts relocated from Graphify output

The complete local PM2.5 LOSO comparison run is preserved at
`spatial_model/xgboost/pm25/loso/loso_pm25_gpu_seed42_20261014_202942_555758/`,
including fold `best.ubj` models, predictions, runner snapshots and manifests.
All 294 files retained their SHA-256 hashes during relocation on 2026-11-03.
The maintained comparison runner now defaults to this checkpoint location.
Historical path strings inside preserved artifacts describe their original location.
The updated live runners have different source hashes, so `--resume` against this
completed historical XGBoost LOSO run is rejected by the existing provenance check.
Use a fresh run directory for new training; keep the saved runner and manifest unchanged.

## PM2.5 held-out XGBoost run on 2026-11-03

`spatial_model/xgboost/pm25/fixed_split/pm25_gpu_seed42_20261103_202859_026791/`
contains the completed CUDA training run, `best.ubj`, six validation candidate
scores, validation/test predictions, evaluation metrics and a run manifest.
Validation selected the IDW residual target with max depth 4 and best iteration 0
(one boosting tree). Test RMSE: 18.478240; MAE: 13.786947 ug/m3 over 689 rows.
This exploratory seed-42 run used the existing fixed split on a previously
inspected dataset. It completed in the dev workspace before transfer to TI-AI-05;
all transferred artifact hashes were verified. No retraining occurred during transfer.

## Notebook training started on 2026-11-03

At the time of this inventory on 2026-11-03, training had been started for these
combinations supported by the maintained notebooks:

| Family | Target | Protocol | Scope |
| --- | --- | --- | --- |
| LightGBM | PM2.5 | Held-out | Fixed split |
| LightGBM | NO2 | LOSO | All 14 defined folds |
| LightGBM | PM2.5 | LOSO | All 103 defined folds |
| ST-GNN + IDW correction | NO2 | LOSO | All 14 defined folds |

The LightGBM jobs execute sequentially; ST-GNN executes in a separate process.
The notebook sources are unchanged. Parameterized and executed notebook copies,
source hashes, settings, and live status JSON are saved under
`.cache/full-training-20261103/`; completed copies are also saved inside each run.
Training uses the frozen snapshot, seed 42 and maintained configurations.
ST-GNN uses CPU, 100 maximum epochs and the configured validation early stopping.
Final test evaluation follows checkpoint selection. Registration and promotion
remain disabled. Missing test labels are represented in the notebooks' coverage
reports; selecting all folds does not imply every fold has evaluable test labels.

NO2 fixed held-out is unsupported by the maintained notebooks. No XGBoost training
notebook exists, so NO2 XGBoost has not been launched using a substitute trainer.
These are workflow limitations, not completed or queued runs.

On the original workstation, inspect `.cache/full-training-20261103/lightgbm-status.json` and
`.cache/full-training-20261103/stgnn-status.json`. A queued or running job is not an
available completed checkpoint set. Historical LightGBM results above remain
separate from these new full-feature fixed-configuration fits.

## See also

- [`notebooks/spatial_model/README.md`](../notebooks/spatial_model/README.md): notebooks that write runs into this folder
- [`notebooks/spatial_model/NAMING_CONVENTION.md`](../notebooks/spatial_model/NAMING_CONVENTION.md): run and model naming convention
- [`docs/experiments/model-comparison-ds-v0.3.0/README.md`](../docs/experiments/model-comparison-ds-v0.3.0/README.md): model comparison these runs belong to
- [`docs/experiments/TI-AI-05/README.md`](../docs/experiments/TI-AI-05/README.md): evidence for the deployed spatial model
