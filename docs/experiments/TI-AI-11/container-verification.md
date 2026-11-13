# TI-AI-11: clean-container reload verification

**Date:** 13 November 2026. The check used the local Railway-parity Compose stack from PR #94, in the isolated `ti-ai11-verify-20261113` project, with its own PostgreSQL, MinIO and MLflow artifact proxy. No Railway service or alias was changed. The selected existing model packages were registered locally; the local route `production` alias pointed to PIC A. The evidence below is local verification, not a Railway deployment or an independent accuracy evaluation.

## Results

| Check | Result |
|---|---|
| PIC A registry resolve and reload in a fresh Cache API container | PASS: local `morl-route-policy:2`, `preference_conditioned`; package SHA-256 `564c693081968cd1750c7a996b5301e34148200b21cc8fde7c5f6f1b626fed52`; compatible with graph `jakarta-20261005` and contract `1.1.0` |
| PIC A reference inference | PASS: six successful routes, two previously recorded OD pairs at α = 0, 0.5 and 1, have the same edge-sequence hashes as the recorded 9 November comparison. The original exposure field's file SHA-256 was checked against that comparison's manifest. The two pairs happen to produce the same route across these preferences; this is a reload check, not a preference-response evaluation. |
| PIC A compatibility in the clean container | PASS: another graph and another major contract were rejected; minor contract drift was flagged. |
| PM2.5 spatial package | PASS: two finite predictions `[22.1244736, 22.0846939]` µg/m³ from the source package matched the independently registry-resolved package exactly. The package's 1.0.0 contract was correctly flagged against runtime 1.1.0. |
| NO2 spatial package | PASS: two finite predictions `[92.5238699, 88.2337523]` µg/m³ from the source package matched the independently registry-resolved package exactly; compatibility was `compatible`. |
| Local registry | PASS: both spatial packages and PIC A were registered and resolved through the artifact proxy with matching package and artifact hashes. |
| Cache API | PASS: the rebuilt local service loaded `morl-route-policy:2`, `policy_family=preference_conditioned`, with the expected package hash. |
| Regression tests | PASS: 142 local tests across scalarized training/packaging, model packaging, solver and public data contracts. |

The recorded route cases are in [`container-route-reload.json`](container-route-reload.json); spatial predictions, package and snapshot hashes, and tolerance are in [`container-spatial-reload.json`](container-spatial-reload.json). The spatial reference is a fresh inference from the original staged package on the same fixed `ds-v0.3.0` input, compared against a separate load from the local registry. It is **not** an independently recorded historical output; the two-cell sample verifies reload consistency rather than model quality or full-city behavior. PIC A reference data had already been inspected during the 9 November exploratory comparison.

The initial Cache API image exposed a real defect: the PIC A serving import pulled in training code that assumed a deeper repository directory and raised `IndexError` inside `/app`. The serving-only loader is now separated from training and the rebuilt image loads the package successfully; a regression test prevents importing the training module from the serving module.

Images used: Cache API `sha256:6d92228daaea4328b24b9f47d88fd990534e068c89b365c384d0521ddf664d0a`, Data Worker `sha256:83ae1e188eafc769b102da3984628bc3e6e59d578ae1b8d372ea6d251c8a3eb1`; Python 3.11.18. The Cache API used CPU PyTorch 2.15.0. Clean means fresh one-off containers using these built service images and a fresh local registry/cache, not a new operating system installation.

## Reproducing the checks

1. Follow [`local-railway-parity.md`](../../devops/local-railway-parity.md) using an isolated Compose project. Stage the selected PM2.5, NO2 and PIC A packages under `.local-models/`; make the local route alias resolve to PIC A. The local registry must be available to the containers.
2. Prepare the route fixture with `python scripts/prepare_ti_ai11_route_fixture.py --fields <original-comparison-fields> --out .local-models/_route-reference`. It refuses a field whose hash differs from the recorded comparison. The original comparison fields are generated artifacts, not in Git.
3. Run `scripts/verify_scalarized_container_reload.py` inside a fresh Cache API image with the fixture mounted at `/reference`, the `src/data_worker/dataset_processed/OSM` graph at `/graph`, `PYTHONPATH=/app`, and `MLFLOW_TRACKING_URI` pointing to the local registry.
4. Run `scripts/verify_spatial_container_reload.py` inside a fresh Data Worker image with `.local-models` mounted read-only at `/reference`, `docs/experiments/ds-v0.3.0` at `/snapshot`, `PYTHONPATH=/app`, and the local registry. It compares successful predictions at a fixed historical window with tolerance `rtol=1e-6, atol=1e-6`.

The full local stack's live-data worker reported a degraded acquisition step because a GEOS-CF forecast response did not match the expected format; Sentinel-5P and VIIRS were unavailable in that cycle. Before a completed pollution window exists, the healthy gateway correctly returns HTTP 503 `cache_unavailable` / `cache_empty` for a route request. This does not invalidate the deterministic model reload checks above, but it is not a successful live-data end-to-end route test.

## Follow-up: worker performance and forecast retry

The original worker had completed feature writing and was interrupted during spatial inference; the unavailable sources did not stop that step. A replay uses the already collected 2026-11-13 09:00–10:00 UTC data and local services, without fetching a new live window. New stage and per-pollutant progress logs show feature preparation, model inference, road aggregation and cache writing separately.

The forecast connector now retries a malformed time axis within its existing two-attempt limit, while continuing to reject a persistently malformed response. Grid-ID validation no longer rebuilds a city-wide set for every tile. On one local sample, 100 validation lookups fell from 2.645 s to 0.013 s; the old and new readers produced identical feature frames for 256 cells over two hours. This microbenchmark is not an end-to-end speedup measurement.

NO2's last-hour features use at most two lags and a three-hour rolling mean. The inference path still reads and hashes the full 24-hour inputs, but computes the quadratic spatial features only for the final three hours. A regression test compares every resulting feature against the 24-hour calculation, including missing observations; on the same two recorded cells both pollutant models reproduced their prior predictions exactly. The unoptimized NO2 replay processed its first 100 tiles in 128.1 s; the optimized replay took 37.19 s for its first 100 tiles on the same local machine and input. These are observed timings, not a performance guarantee.

The optimized replay completed: model inference took 314.98 s (PM2.5 113.78 s, NO2 196.61 s), road aggregation took 79.37 s, and the cache write took 37.79 s. It wrote 372,069 edges, coverage 0.996564, with `spatial-downscaling-pm25:1` and `spatial-downscaling-no2:1`, zero fallback cells and no pollutant fallback. PM2.5's contract minor-version flag remained present. The subsequent request through the local gateway returned HTTP 200, PIC A `morl-route-policy:2` as the policy, no route or pollutant fallback, and the requested preference applied. At 12:46 UTC the response marked the 09:00–10:00 UTC window not stale under the service's three-hour threshold. The recommended and Dijkstra baseline routes had equal reported travel time and exposure for this one request. See [`gateway-replay.json`](gateway-replay.json).

This replay combines the earlier live acquisition and the separately restarted downstream step; it does not prove a new uninterrupted acquisition cycle or resolve the upstream availability issues. One successful route does not establish model superiority or broad routing accuracy. The route is not a deployment to Railway.
