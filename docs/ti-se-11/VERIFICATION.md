# TI-SE-11 stabilization evidence

Date: 2026-11-03 (Asia/Jakarta). Reviewed base: `7d446fc`, branch `TI-SE-11`.
Status: fixes and fresh controlled verification recorded; 64/64 E2E checks pass; dependency and real-model limitations remain documented.

## Changes and defect disposition

- DOC-01: root frontend startup instructions referenced removed `minio-init`
  and started the separate Backend instead of the public Cache API/gateway.
  Corrected the service list and documented the Vite proxy target.
- Added a technical runbook linking architecture, shared/API contracts,
  configuration, deterministic startup, live startup, health checks and recovery.
- WIN-01: MORL RSS monitoring invoked Unix `ps` on Windows. Existing
  `ResourceGuardTests.test_current_rss_is_a_plausible_size` failed before the fix
  and passes with native `GetProcessMemoryInfo`, without a new dependency.
- WIN-02: training artifact paths used Windows backslashes. Existing
  `RunRecordTests.test_completed_run_identifies_itself_and_its_data` and
  `test_divergence_is_recorded_and_artifacts_stay` failed before the fix and pass
  with POSIX path serialization.
- E2E-START-01: Compose 2.24.5 rejected `up --wait` for the Route Model stub
  because it explicitly disabled its inherited health check. Added an HTTP check
  against its existing `/__mode` endpoint on port 8100. Original startup failed;
  the corrected container now reports healthy.
- FIXTURE-01: CRLF checkout changed frozen CSV hashes and prevented fixture
  loading. `.gitattributes` now pins fixture CSVs to LF. The original loader
  rejected `graph_version.csv`; after correction all five hashes match and the
  loader imports 21,987 edges, 17,334 nodes, 129 stations and 705 observations.
- D-01 through D-07 remain documented in the historical
  [TI-SE-10 report](../ti-se-10/LAPORAN.md); its 63/63 result is not fresh evidence.

## Fresh checks

| Check | Result |
|---|---|
| `npm test` in `src/frontend` | PASS: 9 files, 117 tests |
| `npm run build` in `src/frontend` | PASS; Vite warns about a JS chunk above 500 kB |
| `python -m pytest src/backend/tests -q --tb=short` | PASS: 147 tests after WIN-01/WIN-02; [output](backend-tests.txt) |
| OSM suite with disposable PostGIS | 122 passed, 15 skipped: imported real-graph tests require separate `DATABASE_URL`; [output](osm-tests.txt) |
| Pollution suite with disposable PostGIS | 42 passed, no skips; [output](pollution-tests.txt) |
| Planner suite with disposable PostGIS | 38 passed, no skips; [output](planner-tests.txt) |
| Solver suite with disposable PostGIS | 237 passed, no skips; includes cache-flow cases and corrected connection-refusal test; [output](solver-tests.txt) |
| Complete controlled E2E run | 64/64 passed after timeout correction; [results](hasil/results.json), [versions](hasil/versions.json), [run output](e2e-run.txt) |
| `docker info` | PASS after Docker Desktop started: Engine 25.0.3; Prior controlled E2E stack built and executed; no full build log is claimed |

## Acceptance and remaining work

1. Backend, frontend and database-backed cache suites pass (15 real-graph OSM
   tests remain skipped). The controlled E2E run completed with 64/64 checks passing.
2. Existing regressions reproduced WIN-01/WIN-02 before their fixes and pass
   afterward. Fixture checksum/loading and Compose readiness failures were also
   reproduced and corrected without disabling integrity checks.
3. The fixture stack starts from empty volumes using the documented procedure.
   Full live acquisition, real-model integration and Railway deployment remain unverified.
4. The two classification mismatches are resolved by the production database
   fix and transport-specific test expectations. Dependency and coverage deferrals are reviewed below; production deployment
   and real-model correctness are not claimed.

### Timeout classification corrections

The original [61/63 baseline](hasil/before-timeout-fix/results.json) is preserved.

- DB-TIMEOUT-01: `OsmDatabase.connection()` previously classified timeouts
  during both pool acquisition and query execution as `query_timeout`. The query
  handler now wraps only the acquired connection's use; acquisition/reconnection
  timeouts map to `database_unavailable`. The new acquisition regression failed
  before the change (observed `query_timeout`) and passes afterward. The separate
  client-query regression still requires `query_timeout`.
- Stopped Route Model: connection refusal and connect timeout map to
  `solver_unreachable`; read/write/pool timeouts map to `solver_timeout`.
  Connection establishment has a shorter timeout than the overall request so
  the outer deadline does not mask this distinction. The stopped-container E2E
  requires `model_unavailable`, bounded baseline fallback, and no false success.

Fresh database-backed suites: OSM 122 passed/15 real-graph skips, pollution
42 passed, planner 38 passed, solver 237 passed. The full E2E rerun passes 64/64 checks. The database-outage assertion still
requires `database_unavailable` and now passes; one additional stopped-model
no-false-success check accounts for the increase from 63 to 64 checks.

Model coverage remains limited: the E2E harness uses IDW and a route stub,
disables secondary features, and does not load registered spatial models or
trained MORL. MORL training code now exists on this branch; the historical
statement that it did not exist describes the older tested revision. Real-model
integration needs separate artifacts, configuration and evidence. Deployment
verification on Railway is also outside this local audit.

## Dependency audit disposition

`npm ci` reports 7 vulnerable packages (3 moderate, 1 high, 3 critical); raw
evidence: [npm-audit.json](npm-audit.json). These are pre-existing lockfile
findings. Major upgrades proposed by npm are deferred pending compatibility
validation; no `npm audit fix --force` was applied. This is an open security
follow-up, not a claim that the dependencies are safe.

- MapLibre sanitizer advisory: app popups use `setDOMContent`, not `setHTML`;
  retain DOM construction and avoid passing untrusted HTML. This is a limited
  exposure observation, not proof that every library path is unaffected.
- Vite/Vitest/mocker/esbuild/tinypool: keep development and test servers local,
  do not expose Vitest UI, and run only trusted test code. Upgrade and retest
  the toolchain before exposing development tooling to other users.
- Dependency upgrades remain deferred under the restrictions below. All disposable-database
  cache-flow cases and the controlled runtime matrix pass.

## Windows integration test correction

The unavailable-service integration test used a 1.6 s deadline, shorter than
Windows TCP refusal handling (observed about two seconds). It therefore exercised
timeout behavior instead of the intended `ConnectError` path. The test now sets
a 4 s solver budget and a 5 s elapsed bound for this scenario only. Production
defaults and the separate timeout scenarios are unchanged. It failed in the
original isolated run; the corrected suite output is linked above.

## Reviewed deferrals and scope of acceptance

| Item | Disposition and reason | Mitigation / completion condition |
|---|---|---|
| Dependency upgrades | Deferred: npm proposes breaking Vite/Vitest/MapLibre upgrades that need compatibility validation. Vulnerabilities remain open. | Keep dev/test tooling loopback-only, do not expose Vitest UI, and execute trusted tests only. Upgrade and rerun audit, UI tests/build and browser checks before expanding exposure. |
| MapLibre sanitizer | Current popups use `setDOMContent`; the only application `innerHTML` found constructs static icons. This limits the observed exposed path, not every library path. | Preserve DOM construction; follow up with a patched MapLibre version and compatibility verification. This is not security sign-off. |
| Registered spatial models and real MORL | Deferred: the fixture intentionally uses IDW and an HTTP route stub; trained artifacts are outside it. | Validate with versioned registry artifacts and feature inputs before claiming learned-model integration. |
| Secondary/live acquisition and full imported graph | Deferred: external data/services are outside the deterministic fixture. Fifteen real-graph tests require a separate imported database. | Run against recorded sources and a dedicated graph; verify completeness, provenance and outage handling. |
| Railway deployment | Deferred: no rollout was performed in this local stabilization task. | Merge through CI, deploy the resulting revision, and run API smoke/readiness/recovery checks. |

The fixes and technical documentation are ready for integration review with
these explicit limitations. Local controlled reproducibility is demonstrated;
production security, live acquisition and learned-model behavior are not.
Issue closure should reference the integrated revision and retain these
limitations. The remote issue has not been closed by this local change.

## PR review correction

ConnectTimeout now maps to solver_unreachable, with a connect budget of at most
five seconds and half the request timeout so the outer solve deadline cannot
mask connection failure. Read/write/pool timeouts remain solver_timeout; pool
contention does not establish a remote outage. The new ConnectTimeout regression
failed before the fix. Stopped-container E2E again requires model_unavailable.
Captured test logs retain original machine paths as raw evidence; commands in
this report are portable (use python3 where needed).

### Verification of the review revision

`python -m pytest src/backend/osm/tests/test_db.py src/backend/solver/tests -q`
passes 247 tests, with six database-dependent skips; see [review-tests.txt](review-tests.txt).
The ConnectTimeout case failed before the production fix and passes afterward.
PoolTimeout remains solver_timeout by design: local pool contention is not proof
of an unreachable remote service. The transport regression also checks that the
connection timeout is shorter than the read timeout. The Windows-only integration
budget exception preserves the tighter 2.5 s bound on other platforms.

The archived 64/64 E2E result is from the prior revision, not this review fix.
The strict rerun was attempted but blocked before build by Docker Engine returning
HTTP 500 on its named-pipe API, still present after launching Docker Desktop.
Rerun the complete matrix after Engine recovery before merging. The incomplete
rebuild log has been removed; it is not evidence of a full E2E build.
