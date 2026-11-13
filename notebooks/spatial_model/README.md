# Manual spatial-model training

Open `train_stgnn.ipynb` for IDW + ST-GNN correction or `train_lightgbm.ipynb`
for full-feature LightGBM. Both call the maintained Python training/evaluation APIs.

| Target | Fixed spatial/temporal held-out split | LOSO per station group |
|---|---|---|
| PM2.5 | Both models | Both models |
| NO₂ | Unsupported | Both models |

## What the PM2.5 held-out split means

`PROTOCOL="fixed_split"` holds out both station groups and time periods. It is
not a temporal-only split using all stations. Training labels come from training
stations in the training block; validation and test labels come from their own
station groups in later blocks. Sensor-derived inputs use eligible training
stations, with the target group excluded as required by the feature pipeline.

The ds-v0.3.0 PM2.5 fixed-split labels contain:

| Split | Distinct stations with labels | Label timestamps (UTC, inclusive) |
|---|---:|---|
| Train | 69 | 2026-09-13 01:00 to 2026-09-24 00:00 |
| Validation | 14 | 2026-09-25 01:00 to 2026-09-28 00:00 |
| Test | 15 | 2026-09-29 01:00 to 2026-09-30 23:00 |

These are counts of stations represented in each label table, not the configured
station-group split fractions. Station grouping and temporal gaps are defined in
[`split_spec.json`](../../docs/experiments/ds-v0.3.0/split_spec.json).

A temporal-only holdout would train on earlier labels from all eligible stations
and evaluate later labels from those stations. That is a different protocol and
is not the current PM2.5 `fixed_split` workflow.

## Start

The first section in each notebook gives terminal commands to create a Python 3.11
environment, install `src/data_worker/requirements.txt` plus JupyterLab/ipykernel,
register the kernel, and launch Jupyter from the repository root. Select that kernel.
No notebook cell installs packages. Use a dedicated ML environment to avoid native
DLL/OpenMP conflicts; never disable the runtime's duplicate-library check.

Run the numbered sections in order:

1. Setup and select the repository path if it cannot be found automatically.
2. Edit `TARGET`, `PROTOCOL`, `FOLDS`, and the training settings. Use a new `RUN_ROOT`.
3. Verify the frozen snapshot, inspect versions/configuration and label counts.
4. Train. Each fold gets a separate checkpoint and validation report.
5. Inspect validation results and ST-GNN history. One epoch is only a smoke test.
6. Evaluate saved checkpoints. Set `EVALUATE_TEST=True` only after freezing choices.
7. Inspect coverage, pooled metrics, predictions, and plots.
8. Optionally register/promote a reviewed production-compatible serving bundle.

`FOLDS=[0]` runs one LOSO fold; `[0, 1]` runs a subset; `"all"` explicitly runs all
defined groups. There are 103 PM2.5 groups and 14 NO₂ groups, but some have no labels
in the final test block. Coverage reports distinguish missing reports and unevaluable
splits. A subset is not a complete LOSO result. Grouping prevents related stations
from appearing on opposite sides of a fold.

## Reuse and resume

To inspect an existing run, set `RUN_ROOT` to its absolute path and
`RUN_TRAINING=False`; preserve its family, target, protocol, and folds. Run setup,
settings, preflight, the skipped training cell (which defines paths), and the review/
evaluation cells. Evaluation never refits preprocessing. Set `RUN_EVALUATION=False`
to read previously saved reports only. Evaluation overwrites reports for requested
splits under each checkpoint's `evaluation/` directory.

Incomplete runs can be inspected with all folds selected: missing completed bundles
are skipped during reuse/evaluation and retained in the coverage report. Existing
bundles must still match the chosen settings. Example plots use only reports marked
`available`, and `coverage.csv` is saved even when every selected fold is missing.

For interrupted ST-GNN training, set the same `RUN_ROOT`, `RUN_TRAINING=True`, and
`RESUME=True`, with the same target, protocol, fold, seed, batch size, threads,
device and configuration. It restores weights, optimizer, scheduler, and RNG state.
LightGBM has no checkpoint resume: use a fresh run directory. To reproduce an
uninterrupted run, match the recorded environment/hardware; extending a completed
early-stopped run does not reset early stopping.

## Outputs

By default, runs are saved inside the gitignored repository folder:

```text
checkpoints/spatial_model/<model_family>/<target>/<protocol>/<UTC-run-id>/
```

Each run retains its best checkpoint together with the configuration, preprocessing,
metrics and bundle needed to reload it. Separate folds have separate checkpoints;
there is no single best checkpoint across LOSO folds. ST-GNN selects `best.pt` on
validation; LightGBM saves its fitted fixed-configuration model as `model.txt`.

Under `RUN_ROOT/fixed-split` or `RUN_ROOT/fold-N`:

- `best.pt` (ST-GNN) or `model.txt` + `metadata.json` (LightGBM).
- `bundle/`: hashed model, metadata/preprocessing, configuration, source/snapshot
  provenance, library versions, and validation artifacts.
- `history.csv`, `idw_comparison.csv` for ST-GNN; station-level evaluation tables
  for both families. Epoch zero means plain IDW was selected.
- `evaluation/`: saved-checkpoint prediction Parquet files, metric tables and
  `evaluation.json`. Final test predictions appear only when requested.
- `RUN_ROOT/summary.csv` and `coverage.csv`: selected-fold and pooled results.

Pooled metrics use all available prediction rows, not means of per-fold RMSE/R².
Reports with stale checkpoint hashes or missing provenance are excluded until
evaluation is rerun. ST-GNN LOSO validation repeats non-held observations across
folds; its pooled validation score is not an out-of-fold estimate.
The ST-GNN summary also scores its paired IDW baseline. LOSO validation differs
between the existing model families: ST-GNN validates on non-held groups, while
LightGBM reports held-group validation. Compare their final held-group test results,
not validation scores as if those protocols were identical.

## Research versus serving

Both target choices are research/training options. Production remains PM2.5 ST-GNN
and NO₂ LightGBM. Alternative pairings carry explicit model-family provenance and
are rejected by production serving. LOSO checkpoints are not all-station serving
fits. NO₂ serving is fitted separately via LightGBM `protocol="serving", fold=None`,
which does not produce LOSO evaluation claims.

For upload or handoff, describe the PM2.5 ST-GNN fixed-split checkpoint as:
"Fixed spatial-and-temporal held-out checkpoint; not a LOSO fold and not an
all-station refit." A missing fold (`fold=None`) does not establish that all
stations were used for training. The registration guard rejects LOSO folds; it
does not require every accepted checkpoint to be an all-station fit. Upload and
registration do not themselves promote a model to production. See the
[naming convention](../../src/model_registry/NAMING_CONVENTION.md) for separate evaluation, training-scope,
and deployment labels.

Registration/promotion flags default to false. The shared `register()` API rejects
research target/model pairings and LOSO fold checkpoints before accessing MLflow.
Promotion also rejects LOSO checkpoints already present in the registry, before
updating the production alias. Publishing requires an explicit
bundle path and externally configured MLflow/MinIO environment variables; never
put credentials into the notebook. No registry is needed for training/evaluation.

The requirements are not a complete environment lock. Snapshot integrity and
deterministic settings support reproducibility within a matching environment;
neither this notebook nor station-level metrics establish citywide grid accuracy,
calibrated uncertainty, or historical benchmark equivalence.

Recorded workflow checks and their limits are in
[`notebook_verification.json`](../../docs/experiments/TI-AI-05/notebook_verification.json).

## See also

- [`src/data_worker/spatial_model/README.md`](../../src/data_worker/spatial_model/README.md): the Python training and inference API
- [`checkpoints/README.md`](../../checkpoints/README.md): checkpoint layout
- [`docs/PROTOKOL_EVALUASI.md`](../../docs/PROTOKOL_EVALUASI.md): evaluation protocol
- [`docs/superpowers/specs/2026-11-02-manual-training-notebooks-design.md`](../../docs/superpowers/specs/2026-11-02-manual-training-notebooks-design.md): design rationale
