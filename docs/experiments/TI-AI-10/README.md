# TI-AI-10 evaluation of the multi-policy set against the fastest route

The set of [TI-AI-09 PIC B](../TI-AI-09/README.md), on unseen OD pairs and time windows, under the
[protocol](PROTOCOL.md) committed before the test split was read (`2c2813d`). Run once, from a clean tree;
the evaluation took 6 minutes on one CPU thread of an Apple M4. PIC A and PIC C are outside this evaluation.

## Decision: `conditional_candidate`

**The set is technically safe to connect behind the A* fallback, but it misses both research targets, for
different reasons: these fields leave almost no exposure to save (EAI-04), and the policy does not follow the
preference even where these fields allow it (EAI-05).**

| Check | Target | Set | | Exact optimum |
|---|---|---:|---|---:|
| Route validity (EAI-03) | 100% | 100% of 1,000 routes | Pass | |
| Route success | ≥ 95% | 100% of 1,000 routes | Pass | |
| Inference p95 | ≤ 4,500 ms | 135 ms | Pass | |
| EAI-04: median exposure reduction, at α = 0.5 | ≥ 4% | 0.00% (95% interval: 0.00% to +0.08% change) | **Fail** | 0.00%: out of reach |
| EAI-04: median added time, at α = 0.5 | ≤ 20% | +0.10% | Pass | +0.00% |
| EAI-05: preference fulfilled | ≥ 80% of OD pairs | 54.5% (109 of 200) | **Fail** | 100% (200 of 200): reachable |
| EAI-05: pairs with a shifted route | Reported, no target | 8% (16 of 200) | | 24% (48 of 200) |

The optimum shows what these fields allow; `decide()` does not read it, and the outcome rests on the set alone.

The design text reads "tambahan waktu ≥20%"; the protocol reads it as an upper limit, which the lead
researcher should confirm. The outcome is the same either way, because the exposure part fails.

**EAI-04 is limited by the fields.** The exact optimum also has a median exposure change of 0.00% at every α. It
saves 4% or more on only 3 of 200 OD pairs at the decision's α = 0.5, and on 5 of 200 at α = 0. No routing method can
reach a median of 4% on these IDW fields at these trip lengths, so EAI-04 cannot separate good policies from bad
ones until the fields have more structure.

The set is still behind what these fields allow. At α = 0.5 it takes a different route from the fastest on
110 of 200 pairs, and 92 of those are both slower and more exposed. Only 18 lower exposure. On average it adds
0.64% time and 0.25% exposure, while the optimum saves 0.23% exposure.

**EAI-05 is limited by the policy.** The optimum fulfils it on all 200 pairs, because α = 0 minimises exposure and
α = 1 is the fastest route. That result follows from its construction, and it shows the target is reachable on these
fields, so the set's 54.5% is a shortfall of the set itself:

- Most misses have nothing to do with a trade-off. On 152 pairs the optimum takes one route at both α = 0 and α = 1, and
  the set still takes two different routes on 71 of them. These hold 70 of its 91 misses.
- On the 48 pairs where the optimum does shift, the set fulfils 27 and shifts on 15.
- The α = 1 member's objective is time alone, yet it reproduces the fastest route on only 82 of 200 pairs, and 109 of
  its routes are both slower and more exposed.
- Fields with more structure do not close the gap by themselves. On perturbed fields the optimum shifts on about twice
  as many pairs (93), and the set still fulfils only 61.6%.

**Recommendation**

- TI-SE-07 may connect the set to prove the integration path, with A* as the fallback. Users should not be told
  it lowers their exposure, or that their preference reliably changes the route, because on these fields neither holds.
- **For EAI-04, test new fields.** Before TI-AI-12 fixes a final model, retrain and repeat this evaluation on fields
  from the registered models (ST-GNN, LightGBM). This shows whether a 4% median exists to be won; IDW fields cannot.
  It does not settle EAI-05.
- **For EAI-05, improve the policy.** Close the gap to the optimum, starting with the routes that differ when there
  is nothing to trade, or compare against the weighted A* of TI-AI-06 and fall back to it. That A* targets the same
  objective as the `optimal` column below and cannot end in a dead end; it was not itself run in this evaluation.

## Comparison on the primary scenarios

200 OD pairs of 0.5-3 km from the test blocks, 7 test windows (29-30 September), real IDW fields.
Values are paired % changes against the fastest route of the same scenario; lower is better. The
table shows 5 of the 11 values of α; [`summary.json`](results/summary.json) has them all.

| α (member) | Reached | Time, median (quartiles) | Exposure, median (quartiles) | Length, median | Mean time / exposure | Optimal mean time / exposure |
|---|---:|---|---|---:|---:|---:|
| 0 (0) | 200/200 | +0.12 (+0.00 to +0.70) | +0.00 (+0.00 to +0.53) | +0.12 | +0.74 / +0.11 | +0.46 / −0.38 |
| 0.2 (0.25) | 200/200 | +0.16 (+0.00 to +0.82) | +0.00 (+0.00 to +0.60) | +0.16 | +0.89 / +0.28 | +0.23 / −0.34 |
| 0.5 (0.5) | 200/200 | +0.10 (+0.00 to +0.72) | +0.00 (+0.00 to +0.52) | +0.10 | +0.64 / +0.25 | +0.08 / −0.23 |
| 0.8 (0.75) | 200/200 | +0.11 (+0.00 to +0.63) | +0.02 (+0.00 to +0.54) | +0.11 | +0.52 / +0.28 | +0.01 / −0.10 |
| 1 (1) | 200/200 | +0.10 (+0.00 to +0.62) | +0.06 (+0.00 to +0.56) | +0.10 | +0.53 / +0.49 | +0.00 / +0.00 |

Optimal medians are 0.00% for both time and exposure at every α shown.

| α | Same as fastest | Different route | Less exposure | More exposure and slower | Worst exposure | Worst time |
|---|---:|---:|---:|---:|---:|---:|
| 0 | 83 | 117 | 17 | 100 | +6.2% | +17.4% |
| 0.2 | 82 | 118 | 19 | 99 | +7.3% | +19.8% |
| 0.5 | 90 | 110 | 18 | 92 | +5.5% | +11.2% |
| 0.8 | 84 | 116 | 15 | 101 | +6.2% | +6.1% |
| 1 | 82 | 118 | 9 | 109 | +15.9% | +6.5% |

**Trade-off.** Real fields offer almost no trade-off, and the set does not use what there is. Moving from α = 1 to
α = 0, its median exposure stays near zero. EAI-05 is 54.5% fulfilled, and only 8% of pairs (16 of 200) move to a different
route in the right direction, against 100% and 24% for the optimum. Most routes that differ from the fastest are worse than it. The worst single cases are not small: +15.9% exposure at α = 1 and
+19.8% time at α = 0.2.

**Inference.** The set's median is 50 ms (p95 135 ms, maximum 300 ms), including its own A* reference and two reverse
searches. A* alone takes a median of 1.1 ms. None exceeded the 4,500 ms budget.

## Failures

Failures count against route success everywhere. `summary.json` also reports every change with failures
left out (`policy`) and with each counted as the fastest route (`policy_with_fallback`).

| Scenarios | Decoded routes | Reached | Failures | Fields |
|---|---:|---:|---|---|
| Primary, 0.5-3 km | 1,000 | 1,000 | None | Real |
| Primary, 0.5-3 km | 1,000 | 994 | 6 dead ends | Perturbed |
| Longer, 3-8 km | 250 | 233 | 17 dead ends | Real |
| Longer, 3-8 km | 250 | 230 | 20 dead ends | Perturbed |

The primary result of 100% is better than TI-AI-09's 98.8% on validation, with no failure among 1,000 routes.
It is one sample of OD pairs, so failures should still be expected in service.

Decoded routes are 5 members x OD pairs: α values served by one member share a route. All 2,200 primary scenarios are
in [`scenarios.jsonl.gz`](results/scenarios.jsonl.gz), one record per OD pair, window and α.

## Beyond the decision

These results are descriptive; the decision does not use them.

**Longer trips (3-8 km, real fields, 50 OD pairs).** Outside the training range the set degrades: success is
93.2%, below the 95% gate. Median inference is 292 ms. EAI-05 is 30% fulfilled (12 of the 40 pairs reached at both α = 0 and
α = 1), against 100% of 50 for the optimum.

| α (member) | Reached | Time, median (quartiles) | Exposure, median (quartiles) | Length, median | Mean time / exposure | Optimal mean time / exposure |
|---|---:|---|---|---:|---:|---:|
| 0 (0) | 44/50 | +1.76 (+0.46 to +3.67) | +0.32 (−1.01 to +0.99) | +1.76 | +3.08 / −0.97 | +3.93 / −2.59 |
| 0.2 (0.25) | 48/50 | +1.91 (+0.89 to +5.60) | +0.56 (−0.08 to +1.78) | +1.91 | +4.02 / −0.38 | +2.27 / −2.45 |
| 0.5 (0.5) | 46/50 | +1.63 (+0.71 to +3.94) | +1.00 (+0.31 to +2.60) | +1.63 | +3.33 / +1.41 | +0.95 / −1.85 |
| 0.8 (0.75) | 49/50 | +1.16 (+0.20 to +2.20) | +0.50 (+0.00 to +1.42) | +1.16 | +1.31 / +0.25 | +0.09 / −0.73 |
| 1 (1) | 46/50 | +1.43 (+0.67 to +3.44) | +1.31 (+0.37 to +3.20) | +1.43 | +2.61 / +2.18 | +0.00 / +0.00 |

**Perturbed fields (synthetic, primary scenarios).** A trade-off exists here, so this shows whether the set can respond
to the preference. It responds in the right direction but weakly. Medians stay near zero because many pairs have
no trade-off even here. EAI-05 is 61.6% fulfilled, with 19% shifted (122 and 38 of the 198 pairs reached at both ends),
against 100% and 46.5% for the optimum.

| α (member) | Reached | Time, median (quartiles) | Exposure, median (quartiles) | Length, median | Mean time / exposure | Optimal mean time / exposure |
|---|---:|---|---|---:|---:|---:|
| 0 (0) | 198/200 | +0.37 (+0.00 to +1.21) | +0.00 (−0.10 to +0.31) | +0.37 | +2.22 / −1.45 | +2.92 / −2.12 |
| 0.2 (0.25) | 198/200 | +0.28 (+0.00 to +1.30) | +0.00 (+0.00 to +0.32) | +0.28 | +1.70 / −0.95 | +1.24 / −1.94 |
| 0.5 (0.5) | 200/200 | +0.17 (+0.00 to +0.87) | +0.00 (+0.00 to +0.37) | +0.17 | +1.04 / −0.59 | +0.45 / −1.56 |
| 0.8 (0.75) | 198/200 | +0.15 (+0.00 to +0.63) | +0.00 (+0.00 to +0.35) | +0.15 | +0.57 / −0.53 | +0.04 / −0.82 |
| 1 (1) | 200/200 | +0.10 (+0.00 to +0.76) | +0.00 (+0.00 to +0.61) | +0.10 | +0.64 / +0.18 | +0.00 / +0.00 |

At α = 0 the set saves a mean of 1.45% exposure against the optimum's 2.12%, about two-thirds of it, while at α = 1 it
adds 0.18%. These fields do not measure real exposure savings.

## Limitations

- **One candidate.** Only PIC B was evaluated, so this compares it with the baselines, not with PIC A or C. A choice among
  PICs needs those results under this same protocol.
- **Fields.** Exposure comes from IDW on ds-v0.3.0, not from the registered downscaling models.
- **Scope.** Bike mode only. Each OD pair uses one time window, so windows are covered but not crossed with OD pairs.
- **Timing.** Measured on one CPU thread of an Apple M4; a Railway service will differ.
- **EAI-05 definition.** It compares only α = 1 and α = 0, and an identical route counts as fulfilled. This is why the shifted rate
  is reported beside it.

## Changes after the run

As the protocol requires, changes made after the test run are listed here. All of them follow the QA review of this report.
The test split was not read again and no policy was run.

- `summary.json` and `decision.json` also report the optimum's EAI-04 and EAI-05. This comparison was not in the protocol:
  it explains the result and has no part in the decision. Both files were rebuilt from the recorded routes with `--rebuild`,
  and every value already published is unchanged, including the rule in `decide()` and the outcome.
- `scenarios.jsonl` is now compressed. Uncompressed, it matches the original of `1e070e8` byte for byte
  (SHA-256 `b37d79f5…`, in `summaries_rebuilt_from` of `manifest.json`).
- `manifest.json` keeps `2c2813d` as the commit that produced the routes.

## Reuse

| For | File |
|---|---|
| TI-SE-07: what to load, how to call it, and its constraints | [`candidate.json`](results/candidate.json) |
| TI-AI-12: repeat the same scenarios | [`scenario_od_pairs.json`](results/scenario_od_pairs.json), [`manifest.json`](results/manifest.json), [evaluation config](../../../src/backend/route_model/morl/configs/evaluation-ti-ai-10.json) |
| Decision and criteria values | [`decision.json`](results/decision.json) |
| Every scenario | [`scenarios.jsonl.gz`](results/scenarios.jsonl.gz) |

The Multi-Objective Route Model service does not exist yet. `candidate.json` names the functions a service
would wrap behind the `IRouteSolve` contract of TI-SE-07.

## Reproduce

```shell
python -m src.backend.route_model.morl.fields --out src/backend/route_model/runs/fields
python -m src.backend.route_model.morl.evaluate_candidate \
    --config src/backend/route_model/morl/configs/evaluation-ti-ai-10.json \
    --fields src/backend/route_model/runs/fields --out docs/experiments/TI-AI-10/results
# Summaries only, from the recorded routes: reads no field and runs no policy
python -m src.backend.route_model.morl.evaluate_candidate --rebuild --out docs/experiments/TI-AI-10/results
```

## See also

- [`src/backend/solver/README.md`](../../../src/backend/solver/README.md#penyedia-morl-ti-se-07b): later: the set is now served by the `morl` provider (TI-SE-07b)
- [`docs/experiments/TI-AI-11/README.md`](../TI-AI-11/README.md): packaging and registry
- [`docs/ti-se-10/LAPORAN.md`](../../ti-se-10/LAPORAN.md): end-to-end report citing these results
