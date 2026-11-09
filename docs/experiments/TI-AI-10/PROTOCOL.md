# TI-AI-10 evaluation protocol

Written and committed before the test split was read. The configuration is
[`evaluation-ti-ai-10.json`](../../../src/backend/route_model/morl/configs/evaluation-ti-ai-10.json);
`evaluate_candidate.py` refuses to read the test split from a tree with uncommitted
changes, and its output records the commit. Any later rerun or change must be disclosed in
the report.

## What is evaluated

The multi-policy set of TI-AI-09 PIC B, unchanged: five members from
[`docs/experiments/TI-AI-09/set`](../TI-AI-09/set/set_manifest.json), checkpoints verified by
SHA-256. PIC A and PIC C are outside this evaluation.

## Scenarios

A scenario is one OD pair, one time window and one preference. Within a scenario every method
uses the same graph (`jakarta-20261005`, bike), the same exposure table (with missing edges at the
median, as in `RouteEnv`), and the same references.

| Stratum | OD pairs | Trip distance | Role |
|---|---|---|---|
| `trained-range` | 200 | 0.5-3 km | **Primary**: the decision uses this one |
| `longer-trips` | 50 | 3-8 km | Secondary: beyond the training range, descriptive only |

- **OD pairs** come from the test blocks of the same spatial split as training (`split_seed` 42).
  Their endpoints were never an origin or destination in training or validation.
- **Time windows** are the 7 test windows of ds-v0.3.0 (29-30 September), which training and
  validation never read. Each OD pair uses one window, in rotation.
- **Preferences** are 11 values of α from 0 to 1 in steps of 0.1. Each goes to its nearest member, as a
  request would.
- **Fields**: real IDW fields are primary. Each scenario is repeated on a synthetically perturbed
  field, reported separately, because real fields hold almost no trade-off. Perturbed results
  never enter the decision.

## Methods

| Method | Role |
|---|---|
| `policy` | The candidate: greedy route of the nearest member |
| `fastest` | Baseline: A* on travel time (TI-AI-07) |
| `optimal` | Bound: exact minimum of α·T/T_ref + β·E/E_ref (Dijkstra, the objective of TI-AI-06) |

## Metrics

For each route: travel time `T` (s), cumulative exposure `E = Σ I_e·t_e` (index·s), and length (m).
Changes are paired percentages against the fastest route of the same scenario, reported as
median, quartiles, range, and a bootstrap 95% interval of the median, per α.

- **Route success:** the share of decoded routes reaching the destination. Failures are `dead_end`,
  `step_limit` or `no_route`.
- **Route validity (EAI-03):** each returned route is connected from origin to destination using arcs this
  mode may travel.
- **Inference time:** wall time of one decode, one thread, including the A* reference and two reverse
  searches. Reported beside the time of A* alone.

**How failures are treated.** They count against route success in every case. Changes are reported
twice: `policy` leaves failed routes out, and `policy_with_fallback` counts each as the fastest route (0%
change), as the service would answer. Neither hides them.

## Decision rule

Applied mechanically by `decide()` to `trained-range` on real fields.

**Technical gates** (decide whether it can be integrated):

| Gate | Threshold | Source |
|---|---|---|
| Route validity | 100% of returned routes | EAI-03 |
| Route success | ≥ 95% of decoded routes | This protocol: failures cost a fallback, and 95% keeps those rare |
| Inference time p95 | ≤ 4,500 ms | Policy budget of UT-MRM (design Table 5.5) |

**Research targets** (decide whether it does what the design asks):

| Target | Threshold | Measured at |
|---|---|---|
| EAI-04 | Median exposure reduction ≥ 4%, with median added time ≤ 20% | α = 0.5, the design's default preference, `policy` |
| EAI-05 | Preference fulfilled on ≥ 80% of OD pairs | See below |

The design's Table 5.1 reads "tambahan waktu ≥20%". Added time is a cost, so this protocol reads it as
an upper limit (≤ 20%). The design text should be confirmed by the lead researcher.

**EAI-05 definition.** An OD pair is fulfilled when, moving from α = 1 to α = 0, exposure does not rise and
time does not fall. Identical routes satisfy this without responding to anything, so `shifted`, the
share with a different route in the right direction, is reported beside it. A high fulfilled rate with
a low shifted rate does not demonstrate a response.

**Outcome**

| Outcome | When | Meaning |
|---|---|---|
| `candidate` | All gates and targets pass | Integrate |
| `conditional_candidate` | All gates pass, some target fails | Safe to connect to the service behind the A* fallback, but it does not deliver the research target. The failure is stated with the decision |
| `not_a_candidate` | Any gate fails | Do not integrate |

## Questions the report must answer

Is the policy better than the fastest route on exposure, at what cost in time, and at which α? How much of
the optimum's saving does it capture? Could the target be met at all on these fields, judged by
`optimal`? The answer comes from the table across α, not from one mean and not from one example route.

## Reuse

`results/candidate.json` tells TI-SE-07 and TI-AI-12 what to load and its constraints.
`results/scenario_od_pairs.json` and `results/manifest.json` let TI-AI-12 repeat the same scenarios.

## See also

- [`docs/experiments/TI-AI-06/README.md`](../TI-AI-06/README.md): time and exposure definitions
- [`docs/PROTOKOL_EVALUASI.md`](../../PROTOKOL_EVALUASI.md): spatial model evaluation protocol (counterpart)
