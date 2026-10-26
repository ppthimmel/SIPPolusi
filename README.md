# SIPPolusi
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

Railway's edge terminates TLS; the NGINX API Gateway ([src/gateway](src/gateway))
behind it is the single public entry point and forwards `/v1/*` to the backend
over Railway's private network (design document, Table 3.2). Build/start
definitions, health checks, logging and the smoke test for the gateway,
backend and data worker are in
[docs/devops/build-runtime.md](docs/devops/build-runtime.md).

## Environment variables

Each service has its own `src/<service>/.env.example` (postgres, minio,
model_registry, backend, gateway, data_worker, frontend, observability) listing its
variables and owner. There is no global env file. Copy each one before the
first run and fill in the values (`.env` is gitignored):

```bash
for d in postgres minio model_registry backend gateway data_worker frontend observability; do
  cp src/$d/.env.example src/$d/.env
done
```

Credentials repeated across services (Postgres user/password inside
`DATABASE_URL`, MinIO root user/password as `AWS_*`) must match the
`postgres` and `minio` env files.

## Running everything locally

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
| MinIO console    | http://localhost:9001 (minioadmin/minioadmin) |
| Prometheus       | http://localhost:9090         |
| Grafana          | http://localhost:3000 (admin/admin) |

To wipe state and start fresh (e.g. after changing a schema file):

```bash
docker compose down -v
```

For frontend-only iteration with hot reload, run the backend via Compose
(`docker compose up postgres minio minio-init model_registry backend`) and
the frontend separately:

```bash
cd src/frontend
npm install
npm run dev
```

`vite.config.ts` proxies `/v1/*` requests to `http://localhost:8000`.
