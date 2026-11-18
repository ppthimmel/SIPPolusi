# SIPPolusi on Google Cloud: deployment status

Status as of **18 November 2026**. Project `sippolusi` (number 87531247158), region `asia-southeast2` (Jakarta).
Deployed from branch `TI-DO-10b`. Commands, URLs and resource details: [`scripts/gcp/README.md`](../../scripts/gcp/README.md).

**Website:** https://frontend-87531247158.asia-southeast2.run.app

**In short:** the app works end to end on GCP. Routes are served by the route model, and the heatmap shows pollution
data that updates every hour. The PM2.5 and NO₂ downscaling models aren't on GCP yet, so pollution values use the
simpler IDW estimate. Railway is unchanged and still stopped by its usage limit.

---

## Deployed and working

### Services

| Component | GCP resource | Access | Status |
|---|---|---|---|
| Frontend | Cloud Run `frontend` | public | ✅ loads; its `/v1/` proxy to the gateway works |
| Gateway (NGINX) | Cloud Run `gateway` | public | ✅ forwards to the Cache API; rate limits and closed paths work |
| Cache API (routes, heatmap, geocoding) | Cloud Run `cache-api` | internal only | ✅ loads `morl-route-policy:1` from the registry |
| Model registry (MLflow) | Cloud Run `model-registry` | internal only | ✅ model files stored in Cloud Storage through the registry's proxy |
| Data Worker (hourly cycle) | Cloud Run Job `data-worker` + Cloud Scheduler `data-worker-hourly` (`15 * * * *` UTC) | — | ✅ first cycle `complete` |
| Model registration (TI-DO-10b) | Cloud Run Job `model-register` | run by hand | ✅ registered, promoted, and skipped a repeat correctly |
| Road graph seed | Cloud Run Job `seed-graph` | run once | ✅ 299,067 nodes, 373,352 edges |
| Route model packaging | Cloud Run Job `package-route` | run once | ✅ 23 files + `package.json` |

### Infrastructure

| Resource | Details |
|---|---|
| Database | Cloud SQL `sippolusi-db`: PostgreSQL 18 + PostGIS, `db-f1-micro`, 10 GB SSD, private IP only, no public IP |
| Model file bucket | `gs://sippolusi-mlflow-87531247158` (private): MLflow artifacts under `artifacts/` |
| Seed bucket | `gs://sippolusi-seed-87531247158` (private): road graph, TI-AI-09 route set, seed scripts |
| Image repository | Artifact Registry `sippolusi` (Docker): `model-registry`, `cache-api`, `gateway`, `frontend`, `data-worker` |
| Secrets | Secret Manager: `database-url`, `mlflow-backend-store-uri`. Password generated randomly; never written to chat or git |
| Network | VPC `default` with Private Google Access, VPC peering for Cloud SQL, private DNS zone `run-app-private` so internal Cloud Run services are reached through the VPC |
| Service accounts | `sippolusi-registry` (only one with access to model files), `sippolusi-app` (app services and jobs), `sippolusi-tools` (seed jobs, scheduler) |

### Data and models

| Item | Status |
|---|---|
| Road graph `jakarta-20261005` | ✅ seeded from the repo, same edge IDs as Railway |
| Route model `morl-route-policy` | ✅ version 1, aliases `candidate` and `production`, `package_sha256 7774d98c…` (TI-AI-09 set, contract 1.1.0) |
| Pollution cache | ✅ filled by the Data Worker, first window 2026-11-17 05:00 UTC, refreshed hourly |
| PM2.5 model `spatial-downscaling-pm25` | ❌ not registered (IDW fallback) |
| NO₂ model `spatial-downscaling-no2` | ❌ not registered (IDW fallback) |

### Verification results

- `python scripts/smoke_test.py --gateway https://gateway-87531247158.asia-southeast2.run.app --health-path /gateway-health`:
  **6/6 pass**.
- `POST /v1/routes` (bike, Monas → Bundaran HI) through the gateway: HTTP 200 in about 1.3 s,
  `route_source: policy`, `solver: morl`, `route_model: morl-route-policy:1`, `estimation.source: idw`.
- `GET /v1/exposure-surface` through the gateway and through the frontend: HTTP 200, 1,384 cells.
- Cache API and registry called directly from the internet: 404, as intended.
- TI-DO-10b job on GCP: `registered: morl-route-policy version 1 aliases candidate`, then
  `RESOLVED … all 23 files + package.json match. RESULT: PASS`; a repeat run reported `already registered`;
  `PROMOTE=route:1` reported `promoted: morl-route-policy:1 is production`.

---

## Not done yet

| Item | Why | Needed to finish |
|---|---|---|
| PM2.5 and NO₂ models | Their bundles exist only in the Railway bucket, which is locked by the Railway usage limit | Raise the Railway limit (or wait for 27 Nov), download the bundles, then register and promote them on GCP |
| Old Railway data | Same lock. GCP started with an empty database | Optional: `pg_dump` of the `pollution`, `ground_truth` and `mlflow` schemas, restore into Cloud SQL |
| CI/CD to GCP | Built, but not active until its PR is merged into `dev`; the deployer account's permissions are untested until that first run | Merge the PR, then check the first `deploy / gcp` run |
| Observability (Grafana / Prometheus) | Not set up; logs are in Cloud Logging | Use Cloud Monitoring dashboards, or run Grafana on Cloud Run |
| Budget alert | GCP has no hard spending cap like Railway | Account owner sets a budget alert in Billing (for example $20/month) |
| Custom domain | Using the default `*.run.app` URLs | Optional: domain mapping or a load balancer |
| Old backend API (`src/backend/Dockerfile`) | Not deployed; Railway doesn't run it either | Nothing, unless the team needs it |

### Known issues

- **Gateway `/healthz` doesn't work on Cloud Run.** Google reserves URL paths ending in `z`. Use `/gateway-health`.
- **First request after idle is slow.** Cloud Run scales to zero; the first request after a few idle minutes waits a
  few seconds for startup. Setting `--min-instances=1` on `cache-api` and `gateway` removes this but costs more.
- **`gcloud` on Windows + Git Bash** mis-parses arguments that contain spaces. Use PowerShell or the YAML job files.
- **Data Worker status is `degraded`.** Expected while PM2.5 and NO₂ fall back to IDW; it clears once those
  models are registered.

---

## To-do list

### Priority 1: finish the app

> **Attempted 18 November 2026; all items are still open.**
> - **Budget alert:** the deploying account has no billing permission; a Billing Account Administrator must create
>   the budget.
> - **Bundles:** listing the Railway bucket returns `AccessDenied` with the bucket's own credentials. The workspace is
>   still over its hard limit ($10.03 of $10; the period ends 27 November), which is the likely cause.
> - **Register, promote and verify** depend on the bundles, so they haven't started.

- [ ] **Account owner:** set a budget alert in GCP Billing.
- [ ] Get the PM2.5 and NO₂ bundles from the Railway bucket (`packaged/spatial/pm25/…`, `incoming/spatial/no2/…`)
      once Railway's limit is raised.
- [ ] Upload each to `gs://sippolusi-mlflow-87531247158/artifacts/incoming/spatial/<pm25|no2>/<version>/`.
- [ ] Register:
      `gcloud run jobs execute model-register --region asia-southeast2 --update-env-vars REGISTER_PREFIX=incoming/spatial/pm25/<version>/ --wait`
- [ ] Team decision, then promote: `--update-env-vars PROMOTE=pm25:<version>` (same for `no2`).
- [ ] Check the next Data Worker cycle reports `source` other than `idw` for both pollutants.

### Priority 2: make it maintainable

- [x] Commit the GCP scripts and build fixes on branch `chore/gcp-deployment`; open a PR to `dev`.
- [x] Add a gateway health path that works on Cloud Run (`/gateway-health`); `scripts/smoke_test.py --health-path`.
- [x] Add a GitHub Actions deploy job to GCP using Workload Identity Federation.
- [ ] Apply `scripts/gcp/wif-provider.yaml` to the provider `sippolusi-dev` before the first automatic deploy, so only
      `deploy-gcp.yml` can sign in as `sippolusi-deployer` (command in `scripts/gcp/README.md`).
- [ ] Merge PR #105 (TI-DO-10b) first, then the GCP PR; the `model-register` job needs the code in #105.
- [ ] Check that the first `deploy / gcp` run on `dev` succeeds.
- [ ] Decide whether to keep Railway, keep both, or shut Railway down (shutting down is irreversible; owner's decision).

### Priority 3: optional

- [ ] Move historical data from Railway (`pg_dump` / `pg_restore`).
- [ ] Monitoring dashboard and alerts (Cloud Monitoring, or Grafana on Cloud Run).
- [ ] Custom domain.
- [ ] `--min-instances=1` on `cache-api` and `gateway` for demos, if the startup delay matters.

---

## Cost

| Resource | Behaviour |
|---|---|
| Cloud SQL `db-f1-micro` | Always on; the main fixed cost |
| Cloud Run services | Scale to zero; pay only while handling requests |
| Data Worker job | Pay per hourly execution |
| Cloud Storage, Artifact Registry, Secret Manager | Small at this size |

Exact prices aren't listed here; check the Billing page after a few days of running. To pause the database cost,
stop the instance with `gcloud sql instances patch sippolusi-db --activation-policy=NEVER` (the app stops working until
it is started again with `--activation-policy=ALWAYS`).

## See also

- [`scripts/gcp/README.md`](../../scripts/gcp/README.md): commands, URLs, network and access details
- [`src/model_registry/README.md`](../../src/model_registry/README.md#mendaftarkan-model-dari-bucket-railway-ti-do-10b): model registration (TI-DO-10b)
- [`docs/devops/verification-TI-DO-10b-register-from-bucket.md`](verification-TI-DO-10b-register-from-bucket.md): TI-DO-10b evidence
