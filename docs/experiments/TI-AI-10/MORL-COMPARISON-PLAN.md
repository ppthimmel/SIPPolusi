# MORL comparison setup

Status: the paired evaluator is implemented; the protocol and decision rule remain proposed, and no comparison has been run. Evaluate the existing selected checkpoints without retraining. Do not treat this document as a completed experiment or a model selection.

## Candidates and controls

- PIC A: selected scalarized checkpoint in docs/experiments/TI-AI-09/scalarized/20261103T081938Z-scalarized-bike-v1-6eab87d4/checkpoints/best.pt. This is the warm-start checkpoint, not an improved PPO checkpoint.
- PIC B: the five selected members in docs/experiments/TI-AI-09/set/set_manifest.json; use the nearest member for each requested alpha.
- RegretPrior: untrained direct-regret baseline, with the same features and preference input as PIC A.
- PIC A without regret: not included in this comparison because no selected trained checkpoint is documented. This ablation is only needed to investigate the cause of any difference.
- Fastest route and exact weighted optimum: retain the TI-AI-10 baselines.

PIC A differs from PIC B in its regret path as well as preference conditioning. Direct results compare the complete candidates; they do not isolate the effect of preference conditioning. The regret prior is an additional control, but the unavailable ablation is required to isolate the regret path.

## Scenarios and metrics

Apply the existing TI-AI-10 protocol to all methods with identical graph, OD pairs, windows, fields, preferences, mode, missing-data treatment, and limits. Preserve real and perturbed results separately, and include both the trained-range and longer-trip strata. Use paired results for each request. Report valid and successful routes, failures by reason, travel time, exposure, route length, preference fulfilment and actual shifted routes, and decode latency including reference searches. Report failed requests separately and with marked fastest-route fallback, without treating failures as policy successes. Do not compare latency from different machines as equivalent.

The existing TI-AI-10 test scenarios and PIC B results have already been inspected; repeated comparisons are exploratory, not a fresh blind test. A new independent held-out evaluation is needed for stronger final evidence. Verify training and validation exclusions and freeze scenario identities and all candidates before that test. Preserve code revision, configuration, graph, field and checkpoint hashes, run environment and raw per-scenario outputs.

## Proposed decision rule (requires team agreement before test)

Reuse the TI-AI-10 gates: 100% validity among returned routes, at least 95% success, and p95 decode latency at most 4500 ms on the agreed environment. Apply these per candidate on real, trained-range scenarios; report the longer and synthetic strata separately. Retain EAI-04 and EAI-05 exactly as specified in TI-AI-10, including the separate shifted-route measure, and disclose that unchanged routes count toward fulfilment. Passing gates does not establish the research targets. Compare eligible candidates on paired exposure and travel-time outcomes, reliability, preference response and runtime; record a tie-break and the team decision before looking at a fresh test set. If no model meets the research targets, report that rather than implying a winner meets them.

## Running later

The fields must first be generated or supplied separately; see `src/backend/route_model/morl/fields.py`. From the repository root, using the dedicated environment on Windows:

```powershell
docs/experiments/TI-AI-10/.venv/Scripts/python.exe -m src.backend.route_model.morl.compare_candidates --config src/backend/route_model/morl/configs/evaluation-ti-ai-10.json --fields src/backend/route_model/runs/fields --out src/backend/route_model/runs/comparison-val --split val
```

For an agreed test run, use a new empty output directory and `--split test`. Test runs refuse a dirty or unidentifiable Git revision, so commit the protocol, evaluator and agreed rule first. `--scalarized-dir` and `--graph-dir` override the defaults. The evaluator checks all selected checkpoint hashes, requires matching graph version and mode, and writes paired scenario records, per-method summaries and TI-AI-10 classifications, OD pairs, and a provenance manifest. It never automatically chooses a winner or enforces the latency budget as a timeout. Failure-inclusive fallback statistics assume the fastest-route fallback; they are not a service integration test.

The clean-tree safeguard may require Git to trust the worktree on the machine running it. The generated exposure fields are not committed and must be available for the chosen split. Before any test use, agree on the final decision rule and held-out scenarios. Results on the previously inspected TI-AI-10 scenarios must be labelled exploratory.
