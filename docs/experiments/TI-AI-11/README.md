# TI-AI-11: model packages and versions

Both models are shipped as a directory with a `package.json` at its root. The manifest lists the
SHA-256 of every other file, the contract the model was built for, where it came from, the
environment it needs, and its evaluation. A loader verifies all of it before the model is used.

| | Spatial Downscaling Model | Route model (multi-policy MORL) |
|---|---|---|
| `model_kind` / `model_id` | `spatial_downscaling` / `spatial-downscaling-pm25`, `spatial-downscaling-no2` | `route_policy` / `morl-route-policy` |
| Package contents | the `export_bundle` bundle (`bundle.json`, `best.pt` or `model.txt` + `metadata.json`, `stations.parquet`, metrics) + `package.json` | `set_manifest.json`, `od_pairs.json`, each member's selected `best.pt`, `manifest.json`, `config.json`, `evaluation.json` + `package.json` |
| Made by | `spatial_model.artifacts.packaging.package_bundle` | `route_model.morl.packaging.package_policy_set` |
| Loaded by | `spatial_model.inference.serving.load_model(dir, target, runtime_contract)` | `route_model.morl.packaging.load_packaged_policy_set(dir, runtime_contract)` (`policy_family: fixed_preference_set`) |
| Registry name | `spatial-downscaling-pm25`, `spatial-downscaling-no2` | `morl-route-policy` |

The shared code is `model_package.py`. The backend and the Data Worker are separate containers, so
the file exists twice (`src/backend/route_model/` and `src/data_worker/spatial_model/artifacts/`);
a test fails if the copies differ. Edit one and copy it over the other.

## Manifest (`model-package-1`)

```
package_format, model_kind, model_id, model_version, created_at
contract:    data_contracts_version, [grid_spec_id, feature_matrix_version]            (spatial: no graph version)
             data_contracts_version, graph_version, feature_version, mode             (route policy)
lineage:     dataset, snapshot/graph/field hashes, code_revision, config hashes, upstream_model (route -> spatial)
environment: python, libraries (pinned versions)
inference:   entrypoint, policy_family (route), device, inputs/outputs, fallback
evaluation:  headline metrics, protocol, decision
artifacts:   { relative path: sha256 }   # every file, including inner manifests
```

Example: [`route_package.json`](route_package.json) (built from the committed TI-AI-09 set).
The PM2.5 spatial example is [`spatial_pm25_package.json`](spatial_pm25_package.json), made from the bundle its PIC
uploaded (fixed-split checkpoint, not an all-station refit; see the Railway notes). The NO2 serving fit is
[`spatial_no2_package.json`](spatial_no2_package.json).

## Loading

```python
# Route model (backend)
from route_model.morl.packaging import load_packaged_policy_set, runtime_contract
policies, package, compatibility = load_packaged_policy_set(
    "path/to/package", runtime_contract(graph_version, data_contracts_version))

# Spatial model (Data Worker): the pipeline does this on every run
from spatial_model.artifacts.packaging import runtime_contract
from spatial_model.inference.serving import load_model
loaded = load_model(bundle_dir, "pm25", runtime_contract(grid_spec))
loaded.compatibility  # compatible | flagged | rejected is raised
```

`data_contracts_version` is `CONTRACT_VERSION` in `backend/public_api/data_contracts.py`
(the worker's copy, `DATA_CONTRACTS_VERSION`, is checked equal by a test). The HTTP API version in
`openapi.json` is a different number and is not used.

## Compatibility matrix

| Package built for | Runtime provides | Result |
|---|---|---|
| same contract (and, for a route policy, same graph) | same | compatible |
| contract 1.x.y | 1.a.b (a.b differs) | **flagged**: loads, status recorded |
| contract 1.x.y | 2.x.y or another major | **rejected** (`PackageIncompatible`) |
| route policy on graph `jakarta-A` | graph `jakarta-B` | **rejected**: its actions are edge ids of one graph |
| spatial model, any graph | any graph | **compatible**: it predicts per grid cell; the Worker aggregates onto whichever graph is active, so a new OSM graph does not take it out of service |
| grid spec A | grid spec B | flagged |
| route policy on spatial model X | spatial model Y in production | flagged |
| unknown `package_format`, changed file, unlisted file, path outside the package | any | rejected (`PackageError`) |
| credentials or key files in the package | any | build and verification refuse |
| spatial bundle without `package.json` (made before TI-AI-11) | any | loads, flagged as unpackaged |

What a rejection does: in the Data Worker the pollutant falls back to IDW and `model_metadata`
records `fallback_reason = load_bundle:PackageIncompatible` and the requested model version (the
reasons go to the log). A flagged load adds `compatibility` to that pollutant's `model_metadata`.
In the backend a loader error is raised to the caller, which already falls back to A*.

## Verification

```bash
python scripts/verify_model_packages.py route --set docs/experiments/TI-AI-09/set --out docs/experiments/TI-AI-11/route_smoke_test.json --decision "<TI-AI-10 outcome of this set>"
python scripts/verify_model_packages.py spatial --bundle <bundle dir>
python -m pytest src/backend/tests/test_model_package.py        # from the repo root
python -m pytest tests/test_model_package.py                     # from src/data_worker
```

[`route_smoke_test.json`](route_smoke_test.json): the TI-AI-09 set was packaged (23 files, 2.1 MB), then the
original set and the package were each loaded in a fresh interpreter. For 20 validation OD pairs at all
5 preferences, 100 routes, the edge sequences are identical (99 reach the destination; one is a
`dead_end`, the same in both).

Limits of this evidence:
- The exposure field is a fixed synthetic one: the real fields are not in git. It shows that the package
  reproduces the set, not route quality (TI-AI-10 covers that).
- "Clean environment" here is a fresh Python process on the same machine, not a fresh container.
  The same check should run in CI or on Railway once the registry is fixed.
- Spatial: the PM2.5 and NO2 bundles package and load (`spatial_pm25_smoke_test.json`, `spatial_no2_smoke_test.json`).
  The PM2.5 test RMSE was recomputed from the weights with the repository's evaluator and equals the PIC's value
  (18.372, `spatial_pm25_recomputed_metrics.csv`; see the caveats in the Railway notes). The NO2 serving fit has no
  evaluation of its own. The code path is also covered with a hand-made bundle in `tests/test_model_package.py`.
- Railway: registry through the proxy, and both models registered, are recorded in
  [`railway_registry_evidence.md`](railway_registry_evidence.md).

## Decision field and policy families

`evaluation.decision` is passed in for the set being packaged (`--decision`); it is never inherited, and is `null`
for a set that has not been evaluated. The example carries the TI-AI-10 outcome of the committed set.
`inference.policy_family` tells the backend which loader a registered version needs. The committed set is
`fixed_preference_set` (`load_packaged_policy_set`). The scalarized policy on `dev` (TI-AI-09 PIC A,
`load_scalarized_policy`) is a different family and has no packager yet; `package_policy_set` refuses it because
it has no `set_manifest.json`. If TI-AI-12 picks it, the follow-up is `package_conditioned_policy` and a second
`policy_family` value.

## Registry (MLflow)

`register` tags a version with `package_sha256` when the bundle has a `package.json`, and `resolve`
refuses a download whose `package.json` does not match the tag. Registering the route model uses the
same pattern and still needs the registry fix below. Production serving of the registry on Railway:
see [`src/model_registry/README.md`](../../../src/model_registry/README.md).

## Paired MORL comparison

The exploratory comparison of the existing scalarized and fixed-preference route policies, including the regret-prior baseline, is saved in [`morl-comparison/`](morl-comparison/). Open [`comparison-results.ipynb`](morl-comparison/comparison-results.ipynb) for the results, protocol, and limitations. The original test scenarios had already been inspected; these results are not a fresh blind test.
