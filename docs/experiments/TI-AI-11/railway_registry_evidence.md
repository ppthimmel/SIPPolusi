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

## Load checks

- PM2.5: `scripts/verify_model_packages.py spatial` on the downloaded bundle passes (`spatial_pm25_smoke_test.json`):
  package verified, checkpoint loaded by `load_model`, compatibility `compatible`. Python 3.12, torch 2.15 CPU.
- Route policy: the registry-side check compares file hashes through the registry. The container has no PyTorch,
  so the weights themselves are reloaded by `route_smoke_test.json` (identical routes), not by this check.

## Not covered

- NO2 (LightGBM): no bundle has been uploaded yet.
- Reproducing the PM2.5 RMSE from the weights: the dataset test split is not in the repository, so the metrics are
  the ones recorded by the PIC.
- No component reads `morl-route-policy` yet; the backend still uses a route stub (TI-SE-07).
- Whether the Data Worker uses the PM2.5 model in a cycle depends on its inputs being available
  (`model_metadata.pm25.source`); this was not checked here.
