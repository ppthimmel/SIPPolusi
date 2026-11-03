# TI-AI-09 multi-policy MORL (PIC B)

Five independent policies, one per `alpha` (0, 0.25, 0.5, 0.75, 1; `beta = 1 - alpha`),
trained with PPO on environment `route-env-1`, graph `jakarta-20261005`, bike mode. Code:
[`route_model/morl`](../../../src/backend/route_model/morl/README.md). Set
`20261102T145533Z-multi-policy-bike-v1-201e7d41`, commit `7c0df73` with a clean tree.

**The set reaches the destination on 98.8% of validation episodes, but on the real IDW
fields it gives no benefit over the fastest route.** Its routes are 0.5-0.8% slower and
0.4-0.6% more exposed. Those fields hold almost no trade-off to exploit: the exact optimum
saves only 0.2% exposure. On synthetically perturbed fields the policies do respond to the
preference. No test data was used; the comparison with A* on test belongs to TI-AI-10.

## Acceptance criteria

| Criterion | Result | Evidence |
|---|---|---|
| A checkpoint reloads and gives the same inference on the same input | Met | [`verification.json`](verification.json): all five SHA-256 match the manifest; two separate loads give identical results on 750 route requests |
| Each run has an identity, a configuration and clear data and graph references | Met | [`set_manifest.json`](set/set_manifest.json) and each member's `manifest.json`: run ID, configuration and its hash, seed, code commit, library versions, graph file hashes, per-window field hashes, OD set hash |
| Checkpoints are selected on validation data, not the test split | Met | Each member's `best` is the highest mean return on the real validation fields. No test OD pair is sampled. A unit test names a test window with no file, and training still succeeds, so test data is never read |
| A failed or early-stopped run is recorded with its reason and remaining artifacts | Met | α=0.25 stopped early here. [`stopped-run/`](stopped-run/set_manifest.json) is a real stop by the memory guard (see below). Divergence, crashes, interruption and resume are covered by unit tests |

## Results on validation

150 OD pairs (0.5-3 km) in validation blocks, 7 time windows from 25-27 September, none
used in training. Ratios are relative to the fastest route; below 1 is better than it.
"Optimal" is exact Dijkstra on the same objective.

These numbers are slightly optimistic: they come from the same 150 pairs that chose each
checkpoint. Unbiased numbers will come from TI-AI-10's test split.

| α | Update selected | Warm-start success | Selected success | Mean return | Optimality gap |
|---|---|---:|---:|---:|---:|
| 0 | 120 of 200 | 96.0% | 99.3% | −1.025 | 0.71% |
| 0.25 | 60 of 160 (stopped early) | 98.0% | 98.7% | −1.045 | 0.56% |
| 0.5 | 160 of 200 | 98.0% | 98.7% | −1.046 | 0.60% |
| 0.75 | 120 of 200 | 96.0% | 98.7% | −1.045 | 0.50% |
| 1 | 120 of 200 | 95.3% | 98.7% | −1.047 | 0.70% |

The gap is measured on reached routes. One validation pair is 0.67% of success, so the
0.6-point difference among members is one pair, and the α=0.25 and α=0.5 gains over
warm start are one pair each.

The whole set, each α served by its nearest member ([`evaluation.json`](set/evaluation.json)):
1,630 of 1,650 episodes reached the destination and 20 ended in a dead end. In service
those fall back to the A* route. Inference took a median of 0.07 s per route and at most 0.47 s
on CPU, against the design's 8 s limit.

**Response to the preference**

| α | Real IDW: time / exposure | Optimal | Perturbed: time / exposure | Optimal |
|---|---|---|---|---|
| 0 | 1.008 / 1.005 | 1.002 / 0.998 | 1.023 / 0.988 | 1.025 / 0.981 |
| 0.2 | 1.007 / 1.004 | 1.002 / 0.998 | 1.014 / 0.992 | 1.013 / 0.982 |
| 0.5 | 1.007 / 1.005 | 1.001 / 0.999 | 1.013 / 1.000 | 1.005 / 0.987 |
| 0.8 | 1.005 / 1.004 | 1.000 / 1.000 | 1.007 / 1.000 | 1.000 / 0.996 |
| 1 | 1.007 / 1.006 | 1.000 / 1.000 | 1.008 / 1.004 | 1.000 / 1.000 |

- **Real IDW fields:** no meaningful response. Every member is worse than the fastest route
  on both objectives, even α=0.
- **Perturbed fields (synthetic, not used for selection):** the direction is right. Moving from
  α=1 to α=0 accepts about 1.5% more time for about 1.5% less exposure, about 80% of the
  1.9% the optimum saves. The response comes in steps because a preference gets its
  nearest member. Between α=0.4 and α=0.8 exposure stays at 1.000 while the optimum is
  0.983-0.996, so the middle members use little of the available trade-off.

## Training

The five members ran one after another in 96 minutes on an Apple M4 CPU (6 threads), with
PyTorch 2.15 and 82,306 parameters per member. Each episode draws an OD pair from 2,000
train pairs and one of 17 train windows (13-23 September), and 75% run on a perturbed field. The
configuration is [`config.json`](set/members/) in each member's folder; `curves.csv` holds
per-update losses, memory and validation.

Memory peaked at 2.54 GB against a 16 GB limit, and its lowest level showed no upward trend
across members.

**The stopped run.** The first attempt (`stopped-run/`) stopped at update 165 of α=0.5, with
`Memory growth: RSS rose 644 MB in 50 updates`. It was a false alarm: on this machine RSS
rises by about 1 GB for about 25 updates and comes back. Commit `7c0df73` makes the guard
watch the lowest RSS instead, and replaying the stopped run's memory series through it
raises no alarm. The same commit raised validation from 60 to 150 pairs, so all members
were trained again.

## Limitations

- **Deviation from the design.** The design document and issue #20 specify one
  preference-conditioned policy with a Preference Encoder. TI-AI-09 is divided by approach
  and this is PIC B, so there is no Preference Encoder, a preference between grid points gets the
  nearest policy, and a new grid point needs a new member.
- **An exact search beats this policy on these numbers.** The cost-to-go features already run
  two Dijkstra searches per request, and the warm start imitates exact routes. The weighted
  A* of TI-AI-06 returns the exact optimum and never dead-ends. TI-AI-10 should compare with
  it as well as with the fastest route.
- **Fields are IDW only.** The fields come from `ds-v0.3.0` ground truth, not from ST-GNN or
  LightGBM. A finer field may offer more trade-off, and would need retraining. Perturbed
  fields are synthetic and say nothing about real exposure savings.
- **Short trips, one mode.** Trips are 0.5-3 km by bike. Walk mode has not been trained.
- **Training location.** The design places training on the Tambora GPU server; this ran locally
  on CPU. No Model Registry is involved: registration belongs to TI-AI-11.
- **Reproducibility.** Equal seeds give equal training in a unit test on a small graph. This
  set has not been trained twice.

## Reproduce

```shell
python -m pip install -r src/backend/requirements-train.txt
python -m src.backend.route_model.morl.fields --out src/backend/route_model/runs/fields
python -m src.backend.route_model.morl.multi_policy \
    --config src/backend/route_model/morl/configs/multi-policy-bike-v1.json \
    --fields src/backend/route_model/runs/fields --out src/backend/route_model/runs
python -m src.backend.route_model.morl.verify_set --set docs/experiments/TI-AI-09/set \
    --fields src/backend/route_model/runs/fields --out docs/experiments/TI-AI-09/verification.json
```

Field files are not committed; [`fields_manifest.json`](fields_manifest.json) holds their hashes.
Training machine paths in the copied manifests were made relative to the repo root.
