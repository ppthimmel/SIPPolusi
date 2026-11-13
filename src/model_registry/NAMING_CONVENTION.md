# Model and training-run naming convention

Agreed on 2026-11-03. This document specifies the convention for future runs;
it does not rename existing models, change the training code, or start training.

## Training-run name

```text
<target>-<model>-<evaluation>-<scope>-cfg<hash>-s<seed>-<UTC_timestamp>
```

| Field | Convention |
|---|---|
| `target` | `pm25` or `no2` |
| `model` | `stgnn`, `lightgbm`, or another implemented family such as `xgboost` |
| `evaluation` | `heldout`, `loso`, or `none` |
| `scope` | `fixed` for the fixed held-out split; `fold-NNN` for a LOSO fold; `all-stations` for a separate fit using all eligible station groups |
| `hash` | First 8 lowercase hexadecimal characters of the SHA-256 of the effective training configuration |
| `seed` | Integer random seed, for example `42` |
| `UTC_timestamp` | Run creation time in UTC, formatted as `YYYYMMDDTHHMMSSZ` |

Hash the effective configuration as canonical JSON: sorted keys, compact separators,
UTF-8 encoding, and no non-finite numbers. Include model hyperparameters,
preprocessing choices, target, protocol, fold, seed, and training settings. Exclude
output paths and timestamps. Retain the full configuration and full digest with
the run; the short hash is a readable identifier, not an integrity guarantee.

Examples (illustrative hashes and timestamps):

```text
pm25-stgnn-heldout-fixed-cfg8a12bc34-s42-20261103T120000Z
pm25-lightgbm-loso-fold-000-cfg45de6789-s42-20261103T120000Z
no2-lightgbm-loso-fold-003-cfg67ab8901-s42-20261103T120000Z
no2-lightgbm-none-all-stations-cfg12cd3456-s42-20261103T120000Z
```

Each LOSO fold gets its own run name and checkpoint. A single fold is not a complete
LOSO result. `none` means the checkpoint has no independent held-out evaluation;
it does not imply that separate evaluation runs do not exist. `all-stations` means
all eligible station groups under the recorded training rules, not all time periods
or all available labels.

## Evaluation and deployment are separate

Held-out and LOSO describe evaluation protocols. Serving describes intended use,
so it does not belong in the evaluation field. The existing Python protocol value
`fixed_split` maps to the display label `heldout`; a separate current `serving`
fit maps to `none` and `all-stations`. This is a naming mapping, not an API change.

The agreed evaluation matrix remains:

| Target | ST-GNN | LightGBM |
|---|---|---|
| PM2.5 | Held-out and LOSO | Held-out and LOSO |
| NO2 | LOSO only | LOSO only |

The naming format can accommodate XGBoost, but this does not add an XGBoost trainer
to the maintained notebook workflow.

## MLflow registered models

Use stable registered-model names:

```text
<target>-<model>
```

Examples: `pm25-stgnn` and `no2-lightgbm`. MLflow versions identify successive
checkpoints. Use `candidate` and `production` aliases for deployment status;
do not encode deployment status, timestamps, configuration hashes, or seeds in
the stable registered-model name.

Record `model_family`, `target`, `evaluation_protocol`, `training_scope`, `fold`,
`config_hash`, `seed`, dataset identity, and code revision as run metadata. Store
full hyperparameters as MLflow parameters and retain the configuration artifact.
Use an explicit absent value for `fold` on non-LOSO runs.

LOSO checkpoints remain evaluation runs with artifacts in MinIO; the existing
production registration and promotion guards must remain in place. Logging an
evaluation run is distinct from registering a deployable model. A production alias
alone does not establish model quality.

Existing names such as `spatial-downscaling-pm25` and `spatial-downscaling-no2`
remain unchanged until a separate implementation updates registration, resolution,
and deployment configuration consistently. Do not migrate existing aliases as part
of documenting this convention.

## See also

- [`src/model_registry/README.md`](README.md): registry service
- [`checkpoints/README.md`](../../checkpoints/README.md): checkpoint layout
- [`docs/experiments/TI-AI-11/README.md`](../../docs/experiments/TI-AI-11/README.md): registered model names in use today
