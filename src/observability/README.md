# Observability Service

Prometheus, Pushgateway, and Grafana (PNF-04, PNF-06 traceability; used in
load/stress/endurance testing in Bab V). Not a custom application — this
directory only holds configuration, mirroring how the API Gateway is
Traefik/NGINX configuration rather than bespoke source code.

- `prometheus/prometheus.yml` — scrape targets: Backend (which also serves
  Multi-Objective Route Model inference in-process) via `GET /metrics`, and
  Data Worker (which also runs Spatial Downscaling Model inference) via
  Pushgateway, since it is a short-lived periodic job.
- `grafana/provisioning/` — Prometheus datasource provisioning.
- `docker-compose.yml` — local/CI stack (see Bab IV integration testing:
  Docker Compose on GitHub Actions).

The five components on `IMetricsSink` are: Backend, Multi-Objective Route
Model, Spatial Downscaling Model, Data Worker, and (when deployed) the API
Gateway layer. Since API Gateway is Railway's built-in gateway rather than a
component this repo builds, its metrics come from Railway's own observability
rather than this stack.
