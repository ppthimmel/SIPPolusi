# SourceReader per-run source cache

**Goal:** Reuse source data across distinct serving tiles without changing feature values,
ordering, causal availability rules, sensor exclusions, or model predictions.

**Approach:** Keep a lazy source snapshot per SourceReader instance, separate from the existing
single-request preparation cache. Read projected Landsat scenes once and index compact IDs;
share coarse source tables/events and lookup metadata. Select only requested source rows when
preparing each tile. Do not cache complete tile/hour outputs or introduce a growing tile cache.
Worker already constructs a new reader per run, so a new instance reloads updated files.

**Files:** `feature_matrix/sources.py` owns loading and per-request selection;
`tests/test_source_reader_cache.py` pins I/O reuse and semantic boundaries;
`scripts/benchmark_source_reader.py` records representative real serving requests and parity.
Documentation/evidence belongs under `docs/experiments/TI-AI-05/`.

## Requirements

- Distinct tile requests on the same reader read each source Parquet at most once.
- Preserve exact requested row order, missing values, scene IDs, timestamps and feature dtypes.
- Invalid grid IDs fail before source loading; failed loads must not publish a partial cache.
- Returned frames/mappings cannot mutate source state. A new reader sees updated source files.
- Keep resident data independent of tile count; do not materialize citywide 24-hour feature frames.

## Review focus

- Missing Landsat cells: return missing land features rather than a lookup error.
- Unrelated invalid VIIRS manifest dates: preserve validation on the requested source cells.
- VIIRS tile/nearest-listed and weather delay rules: keep both existing options unchanged.
- Timestamp sorting and late scene publication: existing brute-force as-of tests must pass.
- Source reload lifecycle and aliasing: repeated and reordered requests remain identical.

## Tasks

- [x] **1. Reproduce:** add an I/O-count regression test, watch it fail, and capture real
  per-tile baseline feature frames/timings before production edits.
- [x] **2. Implement:** split source loading from tile selection in SourceReader; run the
  new tests and existing FeatureMatrix tests. Limit changes to this source-read boundary.
- [x] **3. Verify:** compare complete real feature frames with the baseline, report source
  read counts, timings and memory, run Worker regression checks, and refresh Graphify.

Execution is inline as continuation of the existing TI-AI-05 review; the clean current
checkout and branch are retained. No new model or training configuration is required.
