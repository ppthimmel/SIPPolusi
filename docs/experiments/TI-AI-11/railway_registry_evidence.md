# Railway development: registry and registered models (3 November 2026)

Environment `development` of project `humble-endurance`. Checks were run by connecting to the `model_registry`
container (it is reachable only over private networking and has no public domain). Secrets are not shown here.

## Registry: artifacts through the proxy

After PR #84 was merged, `model_registry` was configured for the bucket (`MLFLOW_ARTIFACT_ROOT=mlflow-artifacts:/`,
`MLFLOW_ARTIFACTS_DESTINATION` on the bucket's S3 name, the bucket endpoint, region `auto`, and the bucket's own keys)
and redeployed from `dev`. The bucket uses the `virtual-host` URL style; no extra client setting was needed.

| Check | Result |
|---|---|
| Client with no S3 endpoint or keys logs a 200 KB artifact | `artifact_location: mlflow-artifacts:/1`, stored in the bucket (object count 0 to 1, 200,000 bytes) |
| Download of the same artifact | SHA-256 equal to the uploaded one |
| Public domain on `model_registry` | none (Networking tab shows only `modelregistry.railway.internal`) |

The registry has no authentication; it is acceptable only while it stays on private networking.

## Registered models

Both were staged in the bucket, downloaded inside the registry container, verified against their manifests, registered
through the proxy (client without S3 access), and resolved again through the alias `production` with every file hash
matching.

| Registered model | Version | Source | Checked |
|---|---|---|---|
| `morl-route-policy` | 1 | the TI-AI-09 set, 23 files + `package.json` (`package_sha256` 4ad8f8a927c335f8...) | 23 files match after resolving `production` |
| `spatial-downscaling-pm25` | 1 | PM2.5 ST-GNN bundle uploaded by its PIC, 13 files + `package.json` (`bundle_sha256` 723c4558fd2f1685..., `package_sha256` ce84b2d719560416...) | 13 files match after resolving `production` |

Both carry the aliases `candidate` and `production`. The team decided to give both `production` so the development
environment can serve them, and to reassess both in TI-AI-12.

## What the PM2.5 model is

- Fixed spatial-and-temporal held-out checkpoint of run `fcst_calendar_gpu_seed42_20261014_847559c6`
  (training commit `1f3f9e47...`, dataset ds-v0.3.0). It is not a LOSO fold and not an all-station refit.
- 13 epochs, validation-selected best epoch 1. Test RMSE 18.372 ug/m3 (R2 0.175, 689 observations), against 18.745 for IDW.
- The test split was inspected during development; `run_manifest.json` records this as an exploratory evaluation limit.
- The PIC did not promote it. `production` here is the team's decision for the development environment, not a
  statement that the model is production-grade.

## PM2.5 test metrics recomputed from the weights

`scripts/recompute_pm25_test_metrics.py` runs the repository's evaluator (`python -m spatial_model.evaluation stgnn`
logic) on the test split of `docs/experiments/ds-v0.3.0` with the uploaded `best.pt`
([`spatial_pm25_recomputed_metrics.csv`](spatial_pm25_recomputed_metrics.csv)):

| Method | n | RMSE | MAE | R2 | Bias |
|---|---|---|---|---|---|
| IDW | 689 | 18.7448 | 13.9508 | 0.1409 | -8.2723 |
| IDW + ST-GNN (the checkpoint) | 689 | 18.3721 | 13.7040 | 0.1747 | -7.4394 |

The checkpoint's numbers equal the ones its PIC recorded in `metrics.json` (RMSE 18.372117, n 689).

One caveat: the evaluator refuses a checkpoint whose recorded snapshot hashes differ from the checkout's, and 13 of
the snapshot's files hash differently here. Nine are text files that differ only by CRLF line endings (a Windows
checkout). Four (`README.md`, `config.toml`, `manifest.json`, `stgnn/manifest.json`) were edited after the model was
trained. Every data file (`.parquet`, `.csv`: labels, splits, station and graph inputs) is byte-identical to the
training-time hash. The script checks exactly this before it overrides the recorded hashes, and refuses otherwise.
The test split is the one the PIC had already looked at during development, so this confirms reproducibility, not
an unbiased estimate.

## Load checks

- PM2.5: `scripts/verify_model_packages.py spatial` on the downloaded bundle passes (`spatial_pm25_smoke_test.json`):
  package verified, checkpoint loaded by `load_model`, compatibility `compatible`. Python 3.12, torch 2.15 CPU.
- NO2: the LightGBM serving fit uploaded to `incoming/spatial/no2/serving_no2_lightgbm_seed42_20261103_8e1d6724/`
  (commit `8e1d672`, no fold) packages as `ds-v0.3.0-f658e5d2feb9` and loads the same way (`spatial_no2_package.json`,
  `spatial_no2_smoke_test.json`). Its `metrics.json` says `not_evaluated` (a serving fit; evaluation is on separate
  LOSO checkpoints). It is **not registered** yet.
- Route policy: the registry-side check compares file hashes through the registry. The container has no PyTorch,
  so the weights themselves are reloaded by `route_smoke_test.json` (identical routes), not by this check.

## Not covered

- Registering the NO2 bundle (it is packaged and loads, but is not in the registry).
- No component reads `morl-route-policy` yet; the backend still uses a route stub (tracked in #90).
  Note 2026-11-09: superseded by TI-SE-07b; the Cache API's `morl` solver provider now loads it
  ([solver README](../../../src/backend/solver/README.md#penyedia-morl-ti-se-07b)).
- Whether the Data Worker uses the PM2.5 model in a cycle depends on its inputs being available
  (`model_metadata.pm25.source`); this was not checked here.

## See also

- [`src/backend/solver/README.md`](../../../src/backend/solver/README.md#penyedia-morl-ti-se-07b): later: the Cache API now reads `morl-route-policy` (TI-SE-07b)
- [`src/model_registry/README.md`](../../../src/model_registry/README.md): registry service
- [`scripts/railway/README.md`](../../../scripts/railway/README.md): registration procedure
- [`docs/devops/private-networking.md`](../../devops/private-networking.md): network exposure on Railway
