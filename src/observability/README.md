# Observability Service

Prometheus, Pushgateway, Alertmanager and Grafana (IMetricsSink; PNF-01, PNF-02, PNF-10; EDO-02, EDO-03, EDO-04,
EDO-07). Not a custom application: this directory holds the configuration of stock images, the same way the API
Gateway ([src/gateway](../gateway)) is a custom NGINX configuration rather than bespoke source code. On Google Cloud
it runs as described in [docs/devops/observability-gcp.md](../../docs/devops/observability-gcp.md) (issue #112).

| Path | Image | What it holds |
|---|---|---|
| `prometheus/` | `prom/prometheus:v3.15.1` | `prometheus.yml` (scrape jobs), `entrypoint.sh` (writes target files from env), `rules/sippolusi.rules.yml` (recording and alerting rules), `tests/` (promtool unit tests) |
| `alertmanager/` | `prom/alertmanager:v0.34.2` | `alertmanager.yml` (default: alerts are kept and shown, nobody is notified), `alertmanager.example.yml` (email receiver to copy into a secret) |
| `pushgateway/` | `prom/pushgateway:v1.11.4` | none; holds the last push of each Data Worker cycle |
| `grafana/` | `grafana/grafana:13.0.12` | Prometheus and Alertmanager datasources, dashboard `dashboards/sippolusi-overview.json` (home dashboard) |
| `Dockerfile` | `grafana/grafana:13.0.12` | Railway `observability` service (Root Directory `src/observability`): same Grafana image |
| `docker-compose.yml` | | standalone stack (no application targets); the root `docker-compose.yml` runs the same images next to the application |

blackbox_exporter (`prom/blackbox-exporter:v0.29.0`, default `http_2xx` module) and, on Google Cloud,
stackdriver_exporter (`prometheuscommunity/stackdriver-exporter:v0.19.0`) run as stock images without configuration
files.

## Prometheus targets

`entrypoint.sh` writes one `file_sd` file per job from these variables; an empty value leaves that job without
targets. The scheme and port come from the URL (`https://...run.app` becomes `host:443` with scheme `https`).

| Variable | Job | Compose default | Google Cloud |
|---|---|---|---|
| `BACKEND_URL` | `backend` (`GET /metrics`) | `http://backend:8000` | empty (Backend is not deployed) |
| `CACHE_API_URL` | `cache_api` (`GET /metrics`) | `http://cache_api:8000` | empty; `CACHE_API_SCRAPE=1` on deploy sets it (see below) |
| `PUSHGATEWAY_URL` | `pushgateway` (`honor_labels`) | `http://pushgateway:9091` | `https://pushgateway-<number>.asia-southeast2.run.app` |
| `ALERTMANAGER_URL` | `alertmanager`, and alert delivery | `http://alertmanager:9093` | `http://localhost:9093` |
| `STACKDRIVER_URL` | `stackdriver` (60 s) | empty | `http://localhost:9255` |
| `BLACKBOX_URL` + `PROBE_URLS` | `probe` (60 s) | `http://blackbox:9115`, `gateway=http://gateway:8080/healthz` | `gateway=<gateway>/gateway-health,frontend=<frontend>/` |

## Signals per component (IMetricsSink)

| Component | Latency | Errors | Status | Other |
|---|---|---|---|---|
| API Gateway (NGINX) | route latency from its access log: log-based distribution `sippolusi_gateway_route_latency` (p50/p95/p99 of `POST /v1/routes`); Cloud Run `request_latencies` per service | Cloud Run `request_count` by status class | probe `gateway`, Cloud Run `instance_count` | |
| Backend / Cache API | Cloud Run `request_latencies`; `/metrics` histogram `sippolusi_http_request_duration_seconds` per route | `request_count` 5xx; `sippolusi_http_requests_total{status}` | `up{job="cache_api"}` when scraped | CPU, memory |
| Multi-Objective Route Model (in the Cache API) | `sippolusi_route_solver_duration_seconds` (`/metrics`) | log-based `sippolusi_route_inference{outcome="failed"}` | | fallback frequency: `sippolusi_route_inference{outcome="fallback", fallback_reason}`, `sippolusi_route_solver_requests_total` |
| Data Worker | `sippolusi_job_duration_seconds`, `sippolusi_job_step_duration_seconds` | `sippolusi_job_status{status="failed"}`, Cloud Run Job `completed_execution_count{result="failed"}` | `sippolusi_job_status`, `sippolusi_job_last_success_timestamp_seconds`, `sippolusi_job_step_status` | |
| Spatial Downscaling Model | `sippolusi_downscale_duration_seconds` | | `sippolusi_downscale_fallback{pollutant}` | coverage `sippolusi_downscale_coverage_ratio`, `sippolusi_downscale_model_source`, `sippolusi_downscale_confidence_edges` |
| Frontend | Cloud Run `request_latencies` | `request_count` 5xx | probe `frontend` | |
| Cloud SQL | | | `database/up` | CPU, connections |

Metric names from Cloud Monitoring are prefixed by stackdriver_exporter, e.g.
`stackdriver_cloud_run_revision_run_googleapis_com_request_latencies_bucket` (milliseconds). The recording rules
(`sippolusi:route_latency_seconds:p95_10m`, `sippolusi:cloudrun_5xx_ratio:rate10m`, ...) give them short names
in seconds and ratios; the dashboard and alerts use those. Cloud Run's `request_latencies` has no path label, so for the
gateway and frontend it includes the 60 s synthetic probes; route latency (EDO-02) therefore comes from the gateway's
access log, where `/gateway-health` is not logged. Log-based metrics: `scripts/gcp/monitoring/`.

The Data Worker pushes once per cycle (`src/data_worker/metrics_push.py`, `PUSHGATEWAY_URL`) with POST, so a failed
cycle keeps `sippolusi_job_last_success_timestamp_seconds` of the last good one. A failed push is only a warning.

**Why the Cache API is not scraped on Google Cloud by default.** Cloud Run keeps an instance alive while it receives
requests; a 15 s scrape would keep the Cache API (2 vCPU, 4 GiB, CPU always allocated) running all month. Its latency,
errors and fallback frequency come from Cloud Monitoring instead (request metrics and the log-based metric). For load
tests (issue #111), deploy with `CACHE_API_SCRAPE=1` to add the per-route and solver histograms, then deploy again
without it. With more than one Cache API instance, each scrape reaches one instance; Cloud Monitoring figures are the
complete ones.

## Alerts

`prometheus/rules/sippolusi.rules.yml`, thresholds from the design document:

| Alert | Severity | Condition |
|---|---|---|
| `ScrapeTargetDown` | critical | `up == 0` for 5 min (a missing scrape raises an alert, IMetricsSink) |
| `EndpointProbeFailed` | critical | gateway or frontend probe not 2xx for 3 min (EDO-03) |
| `HighErrorRate` / `ErrorRateAboveTarget` | critical / warning | 5xx > 5 % / > 1 % of at least 20 requests in 10 min (EDO-03) |
| `RouteLatencyP95AboveTarget` | critical | p95 of `POST /v1/routes` at the gateway > 60 s for 10 min (EDO-02) |
| `RouteLatencyP95High` | warning | same p95 > 8 s for 15 min with at least 5 routes in 10 min (integration-test budget) |
| `RouteFallbackRateHigh` | warning | > 20 % of at least 10 bike routes in 30 min served by the baseline (the deployed policy is bike-only, so walk always falls back) |
| `DataWorkerCycleFailed` | warning | last cycle `failed` or `skipped` (EDO-04) |
| `DataWorkerResultsStale` | critical | last successful cycle > 2 h ago |
| `DataWorkerMetricsMissing` | critical | no successful cycle pushed for 2 h |
| `DataWorkerExecutionFailed` | warning | a failed Cloud Run Job execution in the last hour |
| `DownscaleFallbackActive` | warning | a pollutant on the IDW fallback for 2 h |
| `DownscaleCoverageLow` | warning | coverage < 90 % of road edges |
| `CloudSqlDown` | critical | `database/up == 0` for 5 min |
| `CloudMonitoringReadFailing` | warning | stackdriver_exporter cannot read Cloud Monitoring for 10 min |
| `Watchdog` | none | always firing; proves the Prometheus to Alertmanager path; dropped by Alertmanager |

Alertmanager groups by alert and service, waits 30 s, repeats every 12 h, and suppresses 5xx/latency alerts while the
probe of the same service fails. The baked-in config notifies nobody; a config with a receiver is mounted from the
secret `alertmanager-config` on Google Cloud (`alertmanager.example.yml`); there it sends email through Gmail SMTP,
with the app password read from a second secret (`alertmanager-smtp-password`, `smtp_auth_password_file`).

## Testing

```bash
cd src/observability/prometheus
docker run --rm -v "$PWD:/w" -w /w/tests --entrypoint promtool prom/prometheus:v3.15.1 test rules sippolusi.rules.test.yml
cd ../alertmanager
docker run --rm -v "$PWD:/w" --entrypoint amtool prom/alertmanager:v0.34.2 check-config /w/alertmanager.yml
```

CI runs both (`test / observability rules`). Code-side tests: `src/backend/tests/test_observability.py`
(histogram), `src/backend/solver/tests/test_fallback.py` (route metrics), `src/data_worker/tests/test_metrics_push.py`.

## See also

- [`docs/devops/observability-gcp.md`](../../docs/devops/observability-gcp.md): GCP deployment, initial state, gaps, test report
- [`docs/devops/observability.md`](../../docs/devops/observability.md): log schema, health checks and metrics
- [`docs/devops/private-networking.md`](../../docs/devops/private-networking.md): Grafana public domain exception
