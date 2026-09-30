# SIPPolusi
SIPPolusi: Sistem Informasi Pemandu Perjalanan dalam Polusi

## Layout

- [src/frontend](src/frontend) — web app (TypeScript, MapLibre GL JS).
- [src/backend](src/backend) — real-time critical path: request validation,
  cache reads, and Multi-Objective Route Model inference
  ([src/backend/route_model](src/backend/route_model)), served from one
  FastAPI process to stay within the routing latency budget.
- [src/data_worker](src/data_worker) — background phase: secondary data
  ingestion, road graph construction, and Spatial Downscaling Model inference
  ([src/data_worker/spatial_model](src/data_worker/spatial_model)).
- [src/model_registry](src/model_registry) — MLflow tracking server and model
  registry.
- [src/observability](src/observability) — Prometheus, Pushgateway, and
  Grafana configuration.
- [db](db) — schema migrations for the single PostgreSQL/PostGIS instance
  (schemas: `pollution`, `osm`, `mlflow`).
- [k3s](k3s) — Kubernetes manifests for a self-hosted alternative to the
  Railway deployment.

There is no standalone API Gateway service: routing, TLS termination, and
rate limiting are handled by Railway's built-in gateway (with NGINX
configured on top of it in production, per the design document).

## Environment variables

Each service has its own `src/<service>/.env.example` (postgres, minio,
model_registry, backend, data_worker, frontend, observability) listing its
variables and owner. There is no global env file. Copy each one before the
first run and fill in the values (`.env` is gitignored):

```bash
for d in postgres minio model_registry backend data_worker frontend observability; do
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
Data Worker, Frontend, and the observability stack (Prometheus, Pushgateway,
Grafana).

| Service         | URL                          |
|------------------|-------------------------------|
| Frontend         | http://localhost:8080         |
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
