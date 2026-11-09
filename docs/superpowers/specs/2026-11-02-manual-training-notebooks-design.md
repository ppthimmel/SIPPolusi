# Manual model training notebooks

## Intent and scope

Make the two existing spatial-model notebooks readable and usable by a person running cells manually on TI-AI-05. Keep training and evaluation in the existing Python modules; do not duplicate model implementations or change their scientific protocols.

## Approaches

Recommended: expand the existing notebooks into guided workflows around the shared APIs. This preserves one implementation while exposing settings, checks, and results.

Alternative: embed the training implementations in the notebooks. This makes the algorithms visible in one document but duplicates maintained code and risks divergence.

Alternative: document CLI commands only. This is simpler to maintain but does not meet the requested manual notebook experience.

Approved scope: both ST-GNN and LightGBM support PM2.5 fixed held-out testing and LOSO; both support NO2 LOSO only. Extend shared training/evaluation code without changing production model selection.

## Required evaluation modes

- PM2.5: expose `fixed_split` for the frozen spatial/temporal held-out test and `loso` for a selected group, selected groups, or all available groups. Train a separate model per LOSO fold and keep outputs separate.
- NO2: expose selected-fold and all-fold LOSO using the frozen group definitions. Reject fixed-split NO2 evaluation. Report missing test-period labels explicitly.
- Show available fold IDs and station groups before execution. Explain that LOSO excludes station groups, which can contain multiple related stations.
- Collect per-fold results and pooled out-of-fold test metrics from saved prediction rows, reporting fold coverage and unevaluable folds explicitly. Never label a subset of folds as complete LOSO or average fold RMSE as pooled RMSE.
- Keep final test evaluation separate from model selection. Explain that ST-GNN LOSO validates on non-held groups, whereas the existing LightGBM workflow reports validation on the held group; do not imply identical validation protocols.

## Notebook workflow

1. Explain the model, target, units, protocol, expected outputs, and limitations. Include environment creation, dependency installation, Jupyter kernel setup, and the Windows native-library import-order note. Do not install packages automatically from a cell.
2. Provide a clearly marked settings cell: repository and snapshot paths, seed, unique output directory, and relevant training parameters. Default ST-GNN to CPU/fixed split and LightGBM to LOSO fold 0. Explain that one fold is not a complete LOSO benchmark and one epoch is only a smoke test.
3. Check paths and snapshot checksums, show Python/library versions and model configuration, and summarize the available split/fold information before training.
4. Run the shared training function with explicit settings. Explain output-directory reuse and how to restart an interrupted ST-GNN run using its existing resume API.
5. Display saved metrics and relevant artifacts: ST-GNN history and paired IDW comparison; LightGBM held-out validation/test metrics. Explain epoch-zero selection and unevaluable folds. Explain that test results are for final reporting, not parameter selection.
6. Provide an optional saved-checkpoint evaluation section using the existing evaluator. Allow users to inspect an existing output directory without retraining.
7. Put registration and promotion in separate optional sections, disabled by default through explicit flags. Explain required environment variables without printing credentials. Running all cells with defaults must never contact the registry or promote a model.

Add a short README beside the notebooks explaining launch instructions, run order, output files, and how to run the remaining NO2 folds manually. Keep a serving-only NO2 fit distinct from LOSO evaluation and do not present fold checkpoints as an all-station serving fit.

## Reproducibility boundaries

Retain snapshot verification, seed settings, deterministic model configuration, and existing bundle provenance. Explain that exact reproduction also requires matching the recorded environment and hardware; this change does not promise a fully locked portable ML environment or reproduce the entire historical benchmark.

## Verification

Validate notebook schema and code syntax. Update the existing notebook tests so they locate cells by stable identifiers rather than numeric position, and check optional registry cells remain inactive by default. Execute notebook setup/check/result-view cells and bounded training/evaluation smoke runs where the installed environment permits. Report exactly which real training runs were executed and any limits. Run relevant Data Worker checks, review the diff, and update Graphify after changes.

## Success criteria

A reader can set paths and training parameters, verify inputs, train either model, inspect actual evaluation results, and find its saved bundle without editing implementation modules or configuring MLflow. Default execution has no registration or promotion side effects.

## See also

- [`docs/superpowers/plans/2026-11-02-manual-training-notebooks.md`](../plans/2026-11-02-manual-training-notebooks.md): implementation plan
- [`notebooks/spatial_model/README.md`](../../../notebooks/spatial_model/README.md): result: notebook guide
- [`docs/PROTOKOL_EVALUASI.md`](../../PROTOKOL_EVALUASI.md): evaluation protocol
