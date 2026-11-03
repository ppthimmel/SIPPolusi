# Multi-policy MORL (TI-AI-09, PIC B)

One independent Actor-Critic per preference on a fixed grid of `alpha`
(`beta = 1 - alpha`), trained with PPO on the TI-AI-08 environment. A request is served
by the member with the nearest `alpha`; a tie goes to the lower one, which weighs
exposure more. A preference between grid points gets the closest policy, not an
interpolation.

**Deviation from the design document.** The design document and issue #20 describe one
preference-conditioned policy with a Preference Encoder, which needs no retraining when
the preference changes. This approach has no Preference Encoder, and a new grid point
needs a new member. TI-AI-09 is divided by approach, and this module is PIC B.
PIC A, the preference-conditioned policy the design describes, shares this package; see
[Scalarized MORL](#scalarized-morl-pic-a).

```shell
python -m pip install -r src/backend/requirements-train.txt
python -m src.backend.route_model.morl.fields --out src/backend/route_model/runs/fields
python -m src.backend.route_model.morl.multi_policy \
    --config src/backend/route_model/morl/configs/multi-policy-bike-v1.json \
    --fields src/backend/route_model/runs/fields --out src/backend/route_model/runs
python -m unittest src.backend.tests.test_morl_policy
```

Continue a stopped or interrupted set with `--resume <set dir>` in place of `--out`.
Finished members are kept, and an unfinished one continues from its `last.pt`; a resumed
member is reproducible but not bit-identical to an uninterrupted run. On a GPU server,
add `--device cuda`. Training and test data stay out of git: `runs/` is ignored.

Results, evidence and limitations: [`docs/experiments/TI-AI-09`](../../../../docs/experiments/TI-AI-09/README.md).

## Modules

| File | Role |
|---|---|
| `fields.py` | Hourly edge exposure from ds-v0.3.0, through the Data Worker's own IDW and edge aggregation. Keeps the dataset's time blocks |
| `features.py` | What the policy sees at a step: an ego-graph around the current node, and two cost-to-go potentials |
| `policy.py` | Graph Encoder and Actor-Critic |
| `train.py` | One member: warm start, PPO, validation, checkpoints, run record |
| `multi_policy.py` | The set: members in sequence on shared data, set record, evaluation |
| `infer.py` | Checkpoint format, member selection, greedy route decoding, exact optimum for comparison |
| `augment.py` | Training-only perturbation of exposure fields |
| `resources.py` | Memory, disk and thread limits |
| `evaluate_candidate.py` | TI-AI-10: the set against the fastest route on the test split ([report](../../../../docs/experiments/TI-AI-10/README.md)) |
| `verify_set.py` | Checks a trained set from its files: checksums, identical routes after reload, inference time |

## Choices the results depend on

- **Cost-to-go potentials.** Reverse Dijkstra from the destination gives each node's
  least remaining time and exposure, and each arc's regret against them. Without them, a
  policy that only sees nearby roads reached the destination in about 40% of validation
  episodes and ended the rest in dead ends: 26% of the graph's nodes have a single arc.
  The potentials are recomputed per request, also at inference.
- **Warm start.** The actor first imitates the exact optimal route for its preference,
  because a random policy almost never reaches the destination. PPO then refines it.
- **Critic warm-up and isolation.** The critic trains alone for the first updates and never
  trains the encoder. Without this, an untrained critic's advantages and value loss
  undid the warm start: validation success fell from 98% to 90%.
- **Perturbed fields.** IDW fields have almost no time-exposure trade-off (the exact
  minimum-exposure route is 0.3% cleaner than the fastest for 0.5-3 km trips). So 75% of
  training episodes multiply the field by a smooth random pattern, labelled synthetic in
  the manifest. No checkpoint is selected on perturbed fields.

## Data separation

Each member's checkpoint is selected by mean return of the greedy policy at its own
`alpha`, on the real validation fields. Test windows are named in the fields manifest
for the split check, but their files are never opened, and no test OD pair is sampled.

## Run records and limits

`set_manifest.json` records each member's outcome; every member also has its own
`manifest.json` with identity, configuration, code commit, data hashes, status and
`stop_reason`. A failed, interrupted or limit-stopped run keeps its checkpoints.

A run stops cleanly when RSS exceeds 16 GB, when the lowest RSS keeps rising by more than
512 MB across 150 updates (temporary peaks do not count), or when under 2 GB of disk is free. Two cores are always left
free. Only `best.pt`, `last.pt` and the last three periodic checkpoints stay.

The limits and the module's other settings are in the configuration file.

## Scalarized MORL (PIC A)

One preference-conditioned policy maximises the environment's scalar reward
`r = alpha * r_time + beta * r_exposure`. Each training episode draws `alpha ~ U[0, 1]`
(`beta = 1 - alpha`), so a new preference needs no retraining. A Preference Encoder turns
`(alpha, beta)` into a vector that modulates the Graph Encoder's output and joins the
Actor's and the Critic's input. An arc's score subtracts its scalarized regret times a
learned scale, because without this the policy ignored the preference; the MLP learns the
correction, and `verify` reports the policy against this prior alone.

Features, Graph Encoder, warm start, PPO, run record and data are those of PIC B, and both
configs have the same OD and environment settings (a unit test checks this). `scalarized.py`
passes its own `Scalarized` hooks to `train.train`; `FixedPreference` holds the hooks of a set
member.

**The arc score is not shared, and it decides the numbers.** PIC B has no regret path, so
comparing the two checkpoints mostly measures that path, not one preference-conditioned
policy against a set of fixed-preference policies. TI-AI-10 must separate the two effects;
what it has to report is in the [experiment report](../../../../docs/experiments/TI-AI-09/scalarized/README.md#what-ti-ai-10-must-report).

| File | Role |
|---|---|
| `scalarized_policy.py` | Preference Encoder, preference-conditioned Actor-Critic, `BoundPreference` for inference |
| `scalarized.py` | `train`, `verify` and `export` commands; episode stream with a preference per episode; checkpoint loader |
| `scalarized_report.py` | `training_curves.png` and `summary.md` from a run's files |
| `configs/scalarized-bike-v1.json`, `configs/scalarized-bike-smoke.json` | The real run, and a 3-minute pipeline check |
| `configs/scalarized-bike-mlp-only-v1.json` | The same run without the regret path, for the comparison with PIC B |

How to run it, results and limitations: [`docs/experiments/TI-AI-09/scalarized`](../../../../docs/experiments/TI-AI-09/scalarized/README.md).

The package also runs on Windows: `resources.py` reads memory through the Win32 API, and
`replace_file` retries while an antivirus or indexer holds a file just written. Building
fields needs `pyproj`; where Smart App Control blocks its DLL, build them in the
Data Worker's Docker image (see the link above). Training itself does not need `pyproj`.
