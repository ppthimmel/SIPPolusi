# Shared Data Contracts Implementation Plan

**Goal:** Complete TI-SE-01 with a versioned, executable description of existing exchanges.
**Architecture:** Keep deployed domain models and payload shapes. Generate a JSON Schema bundle from the actual API models, document the cache mappings, and validate data before cache writes.
**Tech stack:** Python, Pydantic v2, JSON Schema 2020-12, pytest, existing TypeScript/Vitest frontend.
**Spec:** ../specs/2026-10-27-shared-data-contracts-design.md (approved in chat).

## Constraints and review focus

- Work on TI-SE-01 as requested; no migration or new required payload envelope.
- Preserve nullable pollutants and zero; reject nonfinite/negative measurements before writes.
- Window timestamps must be timezone-aware, UTC-normalized, hourly, and consistent across a batch.
- Graph serialization must retain IDs, units, coordinate order, topology, and counts.
- JSON Schema handles structural rules; document and test semantic rules explicitly.
- Keep generated schemas synchronized with runtime models and real payload examples.

## Task 1 — Validate existing exchanges

- [x] Add regression tests to planner/osm/pollution/public_api and Data Worker tests for invalid numeric types, preference sum, graph topology, window duration and cache numeric/batch metadata rules. Run and record expected failures.
- [x] Tighten existing Pydantic schemas; introduce TimeWindowOut for the existing start/end envelope, normalize worker windows, validate cache rows and batch metadata before a transaction.
- [x] Run affected tests and existing suites; preserve missing values and existing request/response shapes.

## Task 2 — Versioned schema and examples

- [x] Add public_api/data_contracts.py with build/render/CLI --write; generate data-contracts-v1.schema.json from PointModel, RouteRequestIn, RoadGraphOut, EdgeWeightOut, TimeWindowOut, EdgeWeightsOut, SolverInputOut and RouteFeatureCollection.
- [x] Add a valid/invalid example corpus for the six required types; validate structural cases with JSON Schema and semantic cases with runtime models.
- [x] Test real graph/weight serializers and cached public route examples against the same bundle; strengthen the existing worker-to-cache-to-backend integration test.

## Task 3 — Documentation and evidence

- [x] Write docs/software/data-contracts-TI-SE-01.md covering all fields through schema links, units, null/default policy, serialization, versioning, cache mappings and semantic rules.
- [x] Run appropriate backend/Data Worker/frontend suites, record commands and actual outcomes in verification-TI-SE-01.md, and link the central contract from component docs.
- [x] Review the diff, regenerate public OpenAPI if affected, and run Graphify update.

## Execution decisions and results

Implementation proceeds inline under the user's approval. No further design approval is needed for these routine implementation choices. Keep changes uncommitted for review.


Results: versioned schema/corpus and central documentation implemented; 60 contract/OpenAPI tests passed, 94 affected worker tests passed with local PostGIS, 285 full worker tests passed (76 skipped), frontend 102 passed and build succeeded. Independent review findings were reproduced and fixed with regression tests. Full backend run exposed two failures reproduced on original HEAD; see verification-TI-SE-01.md. Graphify AST graph updated. No commit or remote issue mutation performed.

Final component regression: 362 passed, 15 skipped, one pre-existing example mismatch deselected. Solver cancellation timing assertions were intermittent in broad/concurrent runs; the final serial module passed 8/8 on both current code and original HEAD. Disposable PostGIS container/network removed.
