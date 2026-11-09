# Technical runbook (TI-SE-11)

## Architecture and contracts

The browser sends `/v1/*` to the frontend proxy, then the NGINX gateway.
The gateway exposes exactly `POST /v1/routes` and `GET /v1/exposure-surface`
from the Cache API (`osm.app:app`). The separate Backend (`main:app`) is not
the public routing endpoint. The Cache API reads OSM and pollution caches,
builds solver input, validates the solver result and computes response metrics.
The Data Worker acquires and validates data, performs spatial inference and
writes versioned cache windows outside the request path. MLflow and MinIO
support model registration and artifacts.

- [Shared contracts](data-contracts-TI-SE-01.md)
- [Public API, errors, coordinates and units](../../src/backend/public_api/README.md)
- [OpenAPI snapshot](../../src/backend/public_api/openapi.json)
- [Solver settings and fallback](../../src/backend/solver/README.md)
- [Build/runtime and deployment](../devops/build-runtime.md)
- [Observability](../devops/observability-TI-DO-08.md)

## Reproduce a controlled local pipeline

Prerequisites: Git checkout, Docker Engine running with Docker Compose v2,
Python 3.9+ for the standard-library E2E runner, available ports 18090, 18080,
18001, 18100 and 55433. Run from the repository root. Docker must be able to
pull base images and install build dependencies on the first build.

Use `python3` instead of `python` on systems where that is the Python 3 command.

```sh
docker info
docker compose -f tests/e2e/docker-compose.yml --profile tools build
python tests/e2e/run_e2e.py
```

The runner resets only its E2E Compose stack, loads checksum-verified fixtures,
and runs success and failure scenarios. It writes results, application/image
versions and redacted correlations under `tests/e2e/out/`. A nonzero exit is
a failure; inspect individual results rather than treating a partial run as a pass.
Open http://localhost:18090 while the stack is running. Fixture data is dated
and may correctly show a stale-data warning.

This stack requires no `.env` files, private credentials or production database.
It uses a simulated SPKU portal, IDW fallback and an HTTP route-model stub.
It does not validate trained MORL or registered spatial-model inference.
See [scenario definitions](../../tests/e2e/README.md).

After saving evidence, remove the disposable stack and its test volumes:

```sh
docker compose -f tests/e2e/docker-compose.yml down -v
```

`python tests/e2e/report.py` replaces the historical TI-SE-10 report artifacts;
for a new stabilization run, retain `out/` separately with its tested revision
and do not relabel the historical report as fresh evidence.

## Full development stack

Copy each `src/<service>/.env.example` to `.env` as described in the root
README. In PowerShell, preserve existing files:

```powershell
'postgres','minio','model_registry','backend','gateway','data_worker','frontend','observability' | ForEach-Object {
  $target = "src/$_/.env"
  if (-not (Test-Path $target)) { Copy-Item "src/$_/.env.example" $target }
}
docker compose config --quiet
docker compose up --build
```

Use matching Postgres and MinIO credentials across consuming services. Example
credentials are local development values. This stack uses live acquisition;
starting containers does not guarantee that a usable graph and pollution window
already exist. Use the fixture stack above for deterministic verification.
Root Compose uses PostGIS 16-3.4; E2E/CI use 18-3.6. Record the actual version
when reporting results rather than assuming these environments are identical.

`src/backend/.env` configures both Backend and Cache API. Solver defaults in
`solver/config.py` select `ROUTE_SOLVER_PROVIDER=mock`. Set it explicitly to
`baseline` for fastest-path routing, or `http` with `ROUTE_SOLVER_URL` for a
compatible IRouteSolve service. `ROUTE_SOLVER_FALLBACK=baseline` is the default;
use `none` to propagate solver failure. A running mock is not trained-model evidence.

For Vite against the real API, start `postgres cache_api gateway`, set
`BACKEND_URL=http://localhost:8081` in `src/frontend/.env`, then run `npm ci`
and `npm run dev` from `src/frontend`. An empty cache returns `503 cache_empty`
until the Worker populates it; use the fixture stack for a controlled demo. For mock UI development, use port 8000
and `npm run mock` in a separate terminal. No `minio-init` service exists;
MinIO creates the configured buckets via `MINIO_DEFAULT_BUCKETS`.

## Verification and recovery

- Frontend: `npm ci`, `npm test`, `npm run build` in `src/frontend`.
- Complete service dependency/test commands: [CI workflow](../../.github/workflows/ci.yml).
- Gateway liveness: `GET http://localhost:8081/healthz`.
- Cache API: `GET http://localhost:8001/health/live` and `/health/ready`.
  Liveness alone does not establish cache availability or successful routing.
- Empty/incompatible cache: expect structured `503 cache_empty`; inspect Worker
  status, graph version and completed pollution windows. Do not insert zero
  exposure to make the request succeed.
- Gateway 502: inspect Cache API connectivity and logs; 504: inspect timeout
  budgets. Keep frontend route timeout above gateway timeout and account for
  solver/fallback budgets (see solver and gateway guides).
- Docker named-pipe/connection error: start Docker Desktop/Engine and wait for
  `docker info` to succeed before building. This is not an application test result.

Keep request IDs, Worker run IDs, artifact versions and redacted logs with a
verification report. Never include private DSNs or travel coordinates in logs.

## See also

- [`docs/devops/local-railway-parity.md`](../devops/local-railway-parity.md): local stack matching Railway development (newer than the full-stack section here)
- [`src/gateway/README.md`](../../src/gateway/README.md): current public paths
- [`docs/devops/ci-cd.md`](../devops/ci-cd.md): CI/CD pipeline
- [`src/model_registry/README.md`](../../src/model_registry/README.md): model registry
- [`docs/ti-se-11/VERIFICATION.md`](../ti-se-11/VERIFICATION.md): ticket that introduced this runbook
