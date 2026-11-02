# Manual training notebooks implementation plan

> Execute inline using the executing-plans skill; the user explicitly requested implementation on TI-AI-05.

**Goal:** Both models train PM2.5 with fixed split or LOSO and NO2 with LOSO, through readable manual notebooks.

**Architecture:** Parameterize shared training/evaluation APIs, preserve legacy defaults, record model family separately from target in bundles, and keep production serving restricted to its established pairings. Notebooks orchestrate APIs and display artifacts.

**Spec:** ../specs/2026-11-02-manual-training-notebooks-design.md

## Constraints and review focus

- No NO2 fixed split; no held-group sensor inputs in LOSO; fixed split uses training stations only as sources.
- Existing CLI/serving behavior remains compatible. Alternative model/target bundles are research artifacts.
- Missing test labels must not prevent fitting an otherwise valid fold or be reported as successful evaluation.
- Run All must never register/promote by default. Output reuse must be explicit.
- Pooled metrics come from prediction rows, not averages of fold scores; incomplete fold coverage is visible.

## Tasks

- [x] Add regression tests for target/protocol support, exclusions, missing test data, bundle identity, and notebook side effects; observe failures.
- [x] Extend ST-GNN prepare/train/evaluate for target choice and missing test labels; preserve validation/checkpoint selection.
- [x] Extend LightGBM train/evaluate for target and fixed-split PM2.5; preserve legacy NO2 serving defaults.
- [x] Export model-family-aware bundles and reject incompatible production serving pairings.
- [x] Rebuild notebooks with setup, configuration, checks, single/all-fold execution, result inspection, pooled metrics, and guarded registry steps; add README.
- [x] Run focused and Data Worker tests, actual bounded training/evaluation, notebook validation, diff review, and Graphify update. Report limits.

## Execution record

The user subsequently requested pulling dev first. The implementation was stashed with explicit pathspecs, TI-AI-05 fast-forwarded to origin/dev (`2fe332f`), and the work reapplied without conflicts; ci-reports was preserved. No push or implementation commit was made.

Independent review found and regression tests reproduced three issues: NO2 ST-GNN export target, per-fold resume for untouched folds, and stale prediction summaries. All were fixed. Six real Jupyter smoke runs completed, followed by six saved-checkpoint workflow runs after the dev update. ST-GNN used one epoch; LOSO used fold 0. Full LOSO and full convergence are outside this smoke verification.

Final Data Worker suite on updated dev base: 296 passed, 86 skipped, 33 warnings. Three earlier environment failures were resolved by installing declared runtime dependencies locally and exposing the dependency path to child processes. No unrelated worker fixes were made. Final diff whitespace and notebook schema/HTML checks passed.
