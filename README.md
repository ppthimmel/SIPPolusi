# SIPPolusi

[![CI](https://github.com/ppthimmel/SIPPolusi/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/ppthimmel/SIPPolusi/actions/workflows/ci.yml?query=branch%3Adev)

SIPPolusi: Sistem Informasi Pemandu Perjalanan dalam Polusi

## Layout

- [src/frontend](src/frontend) — web app (TypeScript, MapLibre GL JS).
- [src/backend](src/backend) — real-time critical path: request validation,
  cache reads, and Multi-Objective Route Model inference
  ([src/backend/route_model](src/backend/route_model)), served from one
  FastAPI process to stay within the routing latency budget.
  Baseline routing, interface examples and cost assumptions:
  [routing guide](src/backend/route_model/README.md).
- [src/gateway](src/gateway) — API Gateway: NGINX custom reverse proxy,
  the public entry point for `/v1/*` in front of the backend.
- [src/data_worker](src/data_worker) — background phase: secondary data
  ingestion, road graph construction, and Spatial Downscaling Model inference
  ([src/data_worker/spatial_model](src/data_worker/spatial_model)).
- [src/model_registry](src/model_registry) — MLflow tracking server and model
  registry.
- [src/observability](src/observability) — Prometheus, Pushgateway, and
  Grafana configuration.
- [database migration](src/data_worker/database/spatial_migration.sql) — additive model provenance migration for PostgreSQL/PostGIS
  (schemas: `pollution`, `osm`, `mlflow`).
- [docs/experiments](docs/experiments) — frozen dataset snapshots and model
  experiment results (e.g. the TI-AI-04 IDW baseline).
- [Spatial model training](src/data_worker/spatial_model/README.md) — ds-v0.3.0,
  PM2.5 IDW + ST-GNN correction and NO₂ LightGBM; Python and
  [notebook workflows](notebooks/spatial_model), MLflow/MinIO registration and Worker inference.
- [Spatial model specification](docs/SPATIAL_MODEL_SPEC.md) — GEOS-CF feature policy,
  pollutant-specific fallback, and mandatory cached grid-to-road provenance.

Railway's edge terminates TLS; the NGINX API Gateway ([src/gateway](src/gateway))
behind it is the single public entry point and forwards `/v1/*` to the backend
over Railway's private network (design document, Table 3.2). Build/start
definitions, health checks, logging and the smoke test for the gateway,
backend and data worker are in
[docs/devops/build-runtime.md](docs/devops/build-runtime.md).
CI/CD (build, test, regression, and the rule that Railway deploys a commit
pushed to `dev` only after the pipeline passes) is in
[docs/devops/ci-cd.md](docs/devops/ci-cd.md).

## Spatial models and experiment results (TI-AI-05)

The production-serving contract uses **IDW + ST-GNN correction for PM2.5** and
**full-feature LightGBM for NO₂**. It does not use LightGBM for both pollutants.
IDW is also the independent fallback for each pollutant when its model or required
inputs are unavailable. See the [active spatial specification](docs/SPATIAL_MODEL_SPEC.md)
and [training/inference guide](src/data_worker/spatial_model/README.md).

The following are historical **ds-v0.3.0** test results recorded in the
[TI-AI-05 report](docs/experiments/TI-AI-05/README.md), not results of the new
notebook smoke tests. RMSE is in µg/m³; R² is dimensionless.

### PM2.5

| Protocol | Method | RMSE | R² |
|---|---|---:|---:|
| Fixed split: 689 observations | IDW | 18.745 | 0.141 |
| Fixed split | IDW + XGBoost | 18.478 | 0.165 |
| Fixed split | IDW + ST-GNN | 18.372 | 0.175 |
| LOSO: 4,358 observations, 95 folds | IDW | 23.995 | -0.040 |
| LOSO | IDW + XGBoost | 23.288 | 0.021 |
| LOSO | IDW + ST-GNN | 23.740 | -0.018 |

ST-GNN is an implementation choice; these results do not establish consistent
superiority over tuned interpolators. The [full comparison](docs/experiments/model-comparison-ds-v0.3.0/README.md)
also reports tuned IDW, kriging, kernel methods, and learned alternatives, with
their evaluation populations and uncertainty estimates.

### NO₂

LOSO test results cover 604 observations from 13 stations. These are the
full-feature experiment results and the IDW comparator; the feature representations
are not identical across every architecture. In particular, Ridge uses IDW and
sensor geometry, and ST-GNN uses its temporal/local feature representation.

| Method | RMSE | R² |
|---|---:|---:|
| IDW | 46.992 | 0.141 |
| IDW + ST-GNN | 47.168 | 0.134 |
| LightGBM | 27.429 | 0.707 |
| Random forest | 31.312 | 0.618 |
| XGBoost | 34.820 | 0.528 |
| Extra trees | 38.428 | 0.425 |
| MLP | 65.938 | -0.692 |
| Ridge (IDW + sensor geometry) | 69.979 | -0.906 |

The NO₂ gain is sensitive to sensor-network geometry and bias; station-level
results do not establish accuracy across the entire grid. Confidence remains
provisional. The [detailed report](docs/experiments/TI-AI-05/README.md)
separately lists interpolation-feature experiments, full-feature comparisons,
and historical versus registered-model integration evidence.

The [manual notebooks](notebooks/spatial_model/README.md) support both model
families for research: PM2.5 fixed held-out testing and LOSO, and NO₂ LOSO only.
Their one-epoch/fold-0 smoke checks verify the workflow, not model convergence
or new benchmark results, and do not change the production-serving pairing.

## Environment variables

Each service has its own `src/<service>/.env.example` (postgres, minio,
model_registry, backend, gateway, data_worker, frontend, observability) listing its
variables and owner. There is no global env file. Copy each one before the
first run and fill in the values (`.env` is gitignored):

```bash
for d in postgres minio model_registry backend backend/osm gateway data_worker frontend observability; do
  cp src/$d/.env.example src/$d/.env
done
```

`src/backend/osm/.env` is the Cache API's, which serves the public API and loads the MORL route model.
Credentials repeated across services (Postgres user/password inside
`DATABASE_URL`, MinIO root user/password as the registry's `AWS_*`) must match the
`postgres` and `minio` env files. Only `model_registry` holds object-storage keys; the
other services reach models through its artifact proxy. To get the stack close to
Railway development, with the road graph and models seeded, follow
[docs/devops/local-railway-parity.md](docs/devops/local-railway-parity.md).

## Running everything locally

For architecture, configuration boundaries, reproducible fixture-based startup,
health checks and troubleshooting, see the [technical runbook](docs/software/technical-runbook.md).
Current stabilization evidence and remaining verification gaps are recorded in
[TI-SE-11](docs/ti-se-11/VERIFICATION.md).

```bash
docker compose up --build
```

This starts, in dependency order: Postgres/PostGIS (schemas applied
the data worker creates the PostGIS extension and schemas/tables on startup via SQLAlchemy), MinIO (creates the
`mlflow` and `feature-store` buckets), the Model Registry (MLflow), Backend,
API Gateway, Data Worker, Frontend, and the observability stack (Prometheus, Pushgateway,
Grafana).

| Service         | URL                          |
|------------------|-------------------------------|
| Frontend         | http://localhost:8080         |
| API Gateway (NGINX) | http://localhost:8081      |
| Backend API      | http://localhost:8000         |
| Model Registry (MLflow UI) | http://localhost:5000 |
| MinIO console    | http://localhost:9001 (credentials from `src/minio/.env`) |
| Prometheus       | http://localhost:9090         |
| Grafana          | http://localhost:3000 (admin/admin) |

To wipe state and start fresh (e.g. after changing a schema file):

```bash
docker compose down -v
```

For frontend-only iteration with hot reload, run the backend via Compose
(`docker compose up postgres cache_api gateway`) and
the frontend separately:

```bash
cd src/frontend
npm install
npm run dev
```

Set `BACKEND_URL=http://localhost:8081` in `src/frontend/.env` for this workflow.
The default `http://localhost:8000` is for the mock API workflow below.
An empty database returns `503 cache_empty` until the Data Worker has populated
the graph and pollution cache; use the fixture workflow in the runbook for a controlled demo.

### Frontend against the real API

`POST /v1/routes` and `GET /v1/exposure-surface` are served by the Cache API
behind the API Gateway (TI-SE-08). Point the dev proxy at the gateway of the
Compose stack:

```bash
cd src/frontend
BACKEND_URL=http://localhost:8081 npm run dev
```

`src/frontend/src/contract.test.ts` checks the UI's parser against real
responses of that API (`src/frontend/src/fixtures/ti-se-08/`).

### Frontend with the mock API

Every state (fallback, stale data, partial data, every error code, timeouts)
can be reproduced on demand with a mock of the same contract (subbab 3.5.2:
`POST /v1/routes`, `GET /v1/exposure-surface`, error codes of Tabel 3.16):

```bash
cd src/frontend
npm run mock     # mock API on :8000 (stop the Compose backend first)
npm run dev      # http://localhost:5173, proxies /v1 to the mock
npm test         # unit tests (Vitest)
```

The mock serves one scenario at a time (fallback, stale data, partial data,
every error code, timeouts, ...). List them and switch with
`GET /__mock/scenario` and `GET /__mock/scenario?name=<scenario>`; the dev
server proxies `/__mock`, so `http://localhost:5173/__mock/scenario?name=fallback`
works too. See [docs/ti-se-09-verifikasi-frontend.md](docs/ti-se-09-verifikasi-frontend.md)
for the states that were exercised.

The map background is OpenStreetMap raster tiles by default (development use
only); set `VITE_BASEMAP_TILES` in `src/frontend/.env` (or as a Docker build
arg) to use another tile service.
