# Documentation map

Where each topic is documented. Guides describe how things work now. Evidence and verification files
record a ticket's result on a given date. They are kept as history and are not updated afterwards.

## Running and deploying

| Topic | Guide | Evidence |
|---|---|---|
| Local stack matching Railway development | [devops/local-railway-parity.md](devops/local-railway-parity.md) | |
| Build, health checks, logging, smoke test | [devops/build-runtime.md](devops/build-runtime.md) | [devops/smoke-test-TI-DO-02.md](devops/smoke-test-TI-DO-02.md) |
| Operator runbook and E2E stack | [software/technical-runbook.md](software/technical-runbook.md) | [ti-se-11/VERIFICATION.md](ti-se-11/VERIFICATION.md) |
| Private networking on Railway | [devops/private-networking.md](devops/private-networking.md) | [devops/network-test-TI-DO-04.md](devops/network-test-TI-DO-04.md) |
| API gateway (NGINX) | [../src/gateway/README.md](../src/gateway/README.md) | [devops/gateway-TI-DO-05.md](devops/gateway-TI-DO-05.md) |
| CI/CD | [devops/ci-cd.md](devops/ci-cd.md) | [devops/ci-cd-evidence-TI-DO-06.md](devops/ci-cd-evidence-TI-DO-06.md) |
| Data Worker schedule | [devops/data-worker-schedule.md](devops/data-worker-schedule.md) | [devops/verification-TI-DO-07.md](devops/verification-TI-DO-07.md) |
| Observability | [devops/observability-TI-DO-08.md](devops/observability-TI-DO-08.md), [../src/observability/README.md](../src/observability/README.md) | |
| Model registry (MLflow) | [../src/model_registry/README.md](../src/model_registry/README.md), [../scripts/railway/README.md](../scripts/railway/README.md) | [experiments/TI-AI-11/railway_registry_evidence.md](experiments/TI-AI-11/railway_registry_evidence.md) |

## Backend (Cache API) and public API

| Topic | Guide | Evidence |
|---|---|---|
| Data contracts | [software/data-contracts-TI-SE-01.md](software/data-contracts-TI-SE-01.md) | [software/verification-TI-SE-01.md](software/verification-TI-SE-01.md) |
| Cache API and road graph | [../src/backend/osm/README.md](../src/backend/osm/README.md) | [../src/backend/osm/VERIFICATION.md](../src/backend/osm/VERIFICATION.md) |
| Pollution cache reader | [../src/backend/pollution/README.md](../src/backend/pollution/README.md) | [../src/backend/pollution/VERIFICATION.md](../src/backend/pollution/VERIFICATION.md) |
| Route planner | [../src/backend/planner/README.md](../src/backend/planner/README.md) | [../src/backend/planner/VERIFICATION.md](../src/backend/planner/VERIFICATION.md) |
| Route solver and providers | [../src/backend/solver/README.md](../src/backend/solver/README.md) | [../src/backend/solver/VERIFICATION.md](../src/backend/solver/VERIFICATION.md), [../src/backend/solver/VERIFICATION_TI-SE-07.md](../src/backend/solver/VERIFICATION_TI-SE-07.md) |
| Public API | [../src/backend/public_api/README.md](../src/backend/public_api/README.md) | [../src/backend/public_api/VERIFICATION.md](../src/backend/public_api/VERIFICATION.md) |
| Geocoding | [ti-se-12-geocoding.md](ti-se-12-geocoding.md) | |
| End-to-end tests | [../tests/e2e/README.md](../tests/e2e/README.md) | [ti-se-10/LAPORAN.md](ti-se-10/LAPORAN.md), [ti-se-11/VERIFICATION.md](ti-se-11/VERIFICATION.md) |

## Frontend

| Topic | Document |
|---|---|
| States, errors, legends (TI-SE-09) | [ti-se-09-verifikasi-frontend.md](ti-se-09-verifikasi-frontend.md) |
| Place search (TI-SE-12) | [ti-se-12-geocoding.md](ti-se-12-geocoding.md) |
| GPS navigation (TI-SE-13) | [ti-se-13-navigasi.md](ti-se-13-navigasi.md) |

## Data Worker and features

| Topic | Document |
|---|---|
| Data Worker overview | [../src/data_worker/README.md](../src/data_worker/README.md) |
| Ground truth (SPKU) data | [../src/data_worker/docs/DATA.md](../src/data_worker/docs/DATA.md) |
| Feature store and connectors | [../src/data_worker/feature_store/README.md](../src/data_worker/feature_store/README.md), evidence [software/verification-TI-SE-02.md](software/verification-TI-SE-02.md) |
| Offline FeatureMatrix | [../src/data_worker/feature_matrix/README.md](../src/data_worker/feature_matrix/README.md) |
| Per-source feature specs (TI-AI-01 dataset) | [GEOS-CF](../src/data_worker/docs/GEOS_CF_FEATURE_SPEC.md), [Landsat](../src/data_worker/docs/LANDSAT_FEATURE_SPEC.md), [Open-Meteo](../src/data_worker/docs/OPEN_METEO_FEATURE_SPEC.md), [Sentinel-5P](../src/data_worker/docs/SENTINEL5P_FEATURE_SPEC.md), [VIIRS](../src/data_worker/docs/VIIRS_FEATURE_SPEC.md), [OSM export](../src/data_worker/docs/OSM_FEATURE_SPEC.md) |
| Road graph writer | [../src/data_worker/osm_cache/README.md](../src/data_worker/osm_cache/README.md) |
| SQLite to Railway migration (Sep 2026) | [../src/data_worker/docs/RAILWAY.md](../src/data_worker/docs/RAILWAY.md) |
| Running the tests in Docker | [../src/data_worker/tests/DOCKER.md](../src/data_worker/tests/DOCKER.md) |

## Spatial downscaling model

| Topic | Document |
|---|---|
| Specification of the active model | [SPATIAL_MODEL_SPEC.md](SPATIAL_MODEL_SPEC.md) |
| Evaluation protocol | [PROTOKOL_EVALUASI.md](PROTOKOL_EVALUASI.md) |
| Code: training, inference, cache | [../src/data_worker/spatial_model/README.md](../src/data_worker/spatial_model/README.md) |
| Dataset snapshots | [../src/data_worker/spatial_model/data/snapshot/README.md](../src/data_worker/spatial_model/data/snapshot/README.md), [experiments/ds-v0.3.0/README.md](experiments/ds-v0.3.0/README.md) |
| Training notebooks | [../notebooks/spatial_model/README.md](../notebooks/spatial_model/README.md), [../notebooks/spatial_model/NAMING_CONVENTION.md](../notebooks/spatial_model/NAMING_CONVENTION.md), [../checkpoints/README.md](../checkpoints/README.md) |
| IDW baseline | [../src/data_worker/spatial_model/evaluation/idw/README.md](../src/data_worker/spatial_model/evaluation/idw/README.md), [experiments/TI-AI-04/README.md](experiments/TI-AI-04/README.md) |
| Model comparison | [experiments/model-comparison-ds-v0.3.0/README.md](experiments/model-comparison-ds-v0.3.0/README.md) |
| Experiment evidence | [TI-AI-02](experiments/TI-AI-02/README.md), [TI-AI-05](experiments/TI-AI-05/README.md), [TI-AI-05 source cache](experiments/TI-AI-05/source_reader_cache.md) |

## Route model

| Topic | Document |
|---|---|
| Time and exposure cost | [route-time-exposure.md](route-time-exposure.md), evidence [ti-ai-06-verification.md](ti-ai-06-verification.md) |
| Fastest-route A* baseline | [../src/backend/route_model/README.md](../src/backend/route_model/README.md), evidence [experiments/TI-AI-07/README.md](experiments/TI-AI-07/README.md) |
| MORL training environment | [../src/backend/route_model/TRAINING_ENV.md](../src/backend/route_model/TRAINING_ENV.md), evidence [experiments/TI-AI-08/README.md](experiments/TI-AI-08/README.md) |
| MORL policies | [../src/backend/route_model/morl/README.md](../src/backend/route_model/morl/README.md), evidence [experiments/TI-AI-09/README.md](experiments/TI-AI-09/README.md), [experiments/TI-AI-09/scalarized/README.md](experiments/TI-AI-09/scalarized/README.md) |
| Evaluation against the fastest route | [experiments/TI-AI-10/PROTOCOL.md](experiments/TI-AI-10/PROTOCOL.md), [experiments/TI-AI-10/README.md](experiments/TI-AI-10/README.md) |
| Model packaging and registry | [experiments/TI-AI-11/README.md](experiments/TI-AI-11/README.md) |
| Serving (`morl` provider) | [../src/backend/solver/README.md](../src/backend/solver/README.md#penyedia-morl-ti-se-07b) |

## Design history

Design notes and implementation plans written while building, kept for their reasoning:
[shared data contracts](superpowers/specs/2026-10-27-shared-data-contracts-design.md)
([plan](superpowers/plans/2026-10-27-shared-data-contracts.md)),
[source reader cache plan](superpowers/plans/2026-10-27-source-reader-cache.md),
[manual training notebooks](superpowers/specs/2026-11-02-manual-training-notebooks-design.md)
([plan](superpowers/plans/2026-11-02-manual-training-notebooks.md)).
