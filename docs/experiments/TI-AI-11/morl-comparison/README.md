# Paired MORL comparison

This folder records the exploratory comparison of the selected **scalarized policy (PIC A)**, the **fixed-preference policy set (PIC B)**, and an untrained **regret-prior baseline**. The same graph, origin–destination pair, exposure field and preference were supplied to every method. The run also calculated the exact weighted Dijkstra route as an objective benchmark and the fastest route as a reference.

The primary scenarios are 200 bike trips of 0.5–3 km on real IDW fields, with 11 requested preferences. Longer trips and synthetically perturbed fields are reported separately. These TI-AI-10 test scenarios were already inspected, so this is an **exploratory comparison**, not a fresh blind evaluation.

## Main results

| Primary real-field measure | Scalarized (PIC A) | Fixed set (PIC B) |
|---|---:|---:|
| Route success | 99.95% | 100% |
| Validity of returned routes | 100% | 100% |
| Local inference time, p95 | 377 ms | 362 ms |
| Median exposure change vs fastest, at α = 0.5 | 0% | 0% |
| Preference fulfilment | 88.5% | 54.5% |
| Pairs that actually shifted in the intended direction | 15% | 8% |

Both models pass the primary technical gates but miss at least one research target, so the TI-AI-10 rule classifies both as `conditional_candidate`. Preference fulfilment counts unchanged routes as fulfilled; the shifted rate is therefore needed to interpret it. Success and latency are calculated per decoded route: PIC B decodes a member once for preferences mapped to that member, whereas PIC A decodes separately for each preference.

For longer trips on real fields, PIC A succeeded on 98.5% of decodes and PIC B on 93.2%. These trips are outside the training distance range and are descriptive. Synthetic-field results are stress tests, not evidence of real-world exposure savings. The scalarized checkpoint is the warm-start checkpoint (update 0), and the two candidates differ in their regret path as well as their preference handling; the comparison does not isolate the cause of their differences.

## Comparison with weighted Dijkstra

At α = 0.5 on the 200 primary real-field scenarios, PIC A returned the same route as weighted Dijkstra in 141 cases and PIC B in 89. The mean weighted-objective gaps were approximately 0.04% and 0.53%, respectively; both returned a route in all 200 scenarios. These gaps were calculated from the paired records in `scenarios.jsonl.gz`.

For the **same graph, inputs, constraints and additive weighted objective**, exact weighted Dijkstra is the optimum: a MORL route can match its objective value but cannot improve on it. This does not rule out a benefit from MORL under other conditions or objectives.

**Important follow-up:** determine whether MORL offers a practical benefit over weighted Dijkstra for the intended application. In particular, benchmark end-to-end latency, resource use, reliability and behavior under the intended operating conditions, using equivalent inputs and hardware. The present run **did not measure weighted Dijkstra runtime**, so it cannot show whether MORL is faster. It also did not benchmark the project's weighted A* implementation. Compare any claimed advantage against the added packaging and serving complexity before choosing a production route method.

## Evidence

- [`comparison-results.ipynb`](comparison-results.ipynb) explains and plots the saved results without rerunning the models.
- [`manifest.json`](manifest.json) records the run configuration, revision and artifact identities.
- [`summary.json`](summary.json) and [`decision.json`](decision.json) contain group metrics and protocol classifications.
- [`scenario_od_pairs.json`](scenario_od_pairs.json) contains sampled pairs.
- [`scenarios.jsonl.gz`](scenarios.jsonl.gz) contains paired per-scenario measurements, failures and route hashes; it is not a full route geometry archive.
