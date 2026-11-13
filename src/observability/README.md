# Observability Service

Prometheus, Pushgateway, and Grafana (PNF-04, PNF-06 traceability; used in
load/stress/endurance testing in Bab V). Not a custom application: this
directory only holds configuration of stock images, the same way the API Gateway
([src/gateway](../gateway)) is a custom NGINX configuration rather than bespoke
source code.

| Path | Image | Configuration |
|---|---|---|
| `prometheus/` (`Dockerfile`, `prometheus.yml`) | `prom/prometheus` | `BACKEND_TARGET` (default `backend:8000`) and `PUSHGATEWAY_TARGET` (default `pushgateway:9091`), substituted into `prometheus.yml` at start. On Railway: `<service>.railway.internal:<port>` |
| `pushgateway/Dockerfile` | `prom/pushgateway` | none |
| `grafana/` (`Dockerfile`, `provisioning/`) | `grafana/grafana` | `PROMETHEUS_URL` (default `http://prometheus:9090`) for the provisioned Prometheus datasource; admin login from `.env` (`GF_SECURITY_ADMIN_USER`, `GF_SECURITY_ADMIN_PASSWORD`, see `.env.example`) |
| `Dockerfile` | `grafana/grafana` | The Railway `observability` service (Root Directory `src/observability`): Grafana only, same provisioning and `PROMETHEUS_URL` |
| `docker-compose.yml` | | Standalone local stack of the three images above. The root `docker-compose.yml` also starts `prometheus`, `pushgateway` and `grafana` from these folders; CI uses neither for observability |

What is scraped today:

- **`backend` job**: `GET /metrics` on `BACKEND_TARGET`. The default points at
  the Backend container. Route inference (the `morl` solver provider) runs in
  the Cache API, which serves its own `GET /metrics`; to scrape it, point
  `BACKEND_TARGET` at it (locally `cache_api:8000`). Metric names and the reason
  `/metrics` has no authentication:
  [docs/devops/observability.md](../../docs/devops/observability.md#metrics).
- **`data_worker` and `pushgateway` jobs**: the Pushgateway. Nothing in
  `src/data_worker` pushes metrics yet, so the Data Worker (which also runs
  Spatial Downscaling Model inference) is observed through its structured logs
  and `python main.py status` / `health` instead.

The five components on `IMetricsSink` are: Backend, Multi-Objective Route
Model, Spatial Downscaling Model, Data Worker, and the API Gateway layer. The
gateway ([src/gateway](../gateway)) exposes no Prometheus endpoint; it writes a
JSON access log with `request_id`, status and durations to stdout, read through
Railway's logs.

## See also

- [`docs/devops/observability.md`](../../docs/devops/observability.md): log schema, health checks and metrics
- [`docs/devops/private-networking.md`](../../docs/devops/private-networking.md): Grafana public domain exception
