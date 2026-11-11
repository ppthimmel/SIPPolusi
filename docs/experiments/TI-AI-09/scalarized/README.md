# TI-AI-09 scalarized MORL (PIC A)

One preference-conditioned policy, trained with PPO on environment `route-env-1`, graph
`jakarta-20261005`, bike mode. The agent maximises the weighted reward

```
r = α·r_time + β·r_exposure,   β = 1 − α
```

and each training episode draws its own `α ~ U[0, 1]`. A reached route returns
`−(α·T/T_ref + β·E/E_ref)`, so the fastest route scores −1 at every preference. Code:
[`route_model/morl`](../../../../src/backend/route_model/morl/README.md).

| Component | Implementation | Shared with PIC B |
|---|---|---|
| Graph Encoder | Ego-graph of up to 64 nodes within 4 hops, 3 layers of edge-conditioned message passing | Same class |
| Preference Encoder | MLP on `(α, β)` and Fourier features of α; modulates the graph representation (FiLM) and joins the Actor's and Critic's input | PIC A only |
| Actor-Critic | Actor scores each outgoing arc, with invalid arcs masked. Critic estimates the scalarized return and does not train the encoder | Same structure |
| Arc score | The score subtracts the arc's scalarized regret, `α·time regret + β·exposure regret`, times a learned scale; the MLP learns the correction | **Not shared: PIC B has no regret path** |
| Warm start | Imitates the exact weighted-optimal route for each episode's preference | Same code, same 400 episodes |
| PPO | Same hyperparameters | Same code |
| Data | Same fields (31 of 31 window hashes identical), same OD pairs, same environment settings | Same |

The data are the same, but the two approaches do not differ only in how they handle the preference: the arc score
differs too, and the [next section](#what-ti-ai-10-must-report) says what follows from that.

## What TI-AI-10 must report

A comparison of the two selected checkpoints mostly measures the regret path. Validation return on real fields, with
the conditions that differ between these rows:

| Policy | Return | Conditions |
|---|---:|---|
| Selected PIC A checkpoint (warm start, regret path) | −1.0003 | 150 pairs, 5 preferences |
| PIC B members, best checkpoints after PPO | −1.025 to −1.047 | 150 pairs, each member at its own α |
| PIC A without the regret path, warm start only | −1.102 | 60 pairs, 3 preferences; development number from an earlier revision |
| Regret prior alone, no learning | −1.0061 | 150 pairs, 3 preferences |

So that the effect of the architecture is separated from the effect of the approach, TI-AI-10 must report these four
side by side, on the same test pairs, windows and preferences:

1. the selected PIC A checkpoint;
2. the PIC B set;
3. PIC A without the regret path, trained with `configs/scalarized-bike-mlp-only-v1.json`;
4. the regret prior (`RegretPrior`), which needs no training.

**1 against 3** is the effect of the regret path. **3 against 2** is the effect of the approach, one conditioned policy
against a set, with neither using the path. **1 against 4** is what learning adds to the path. Run 3 has not been
trained yet, and `evaluate_candidate.py` loads only policy sets so far; both are TI-AI-10 work. Status after
[TI-AI-10](../../TI-AI-10/README.md): both still open. Do not conclude from 1
against 2 alone that one approach is better.

## Design decision: the direct regret path

Warm start only, 400 episodes, 60 validation pairs, seed 42 (development numbers, not a result):

| Head | Real: reached, return | Perturbed: reached, return | Perturbed α=0: T/T_ref, E/E_ref | Perturbed α=1: T/T_ref, E/E_ref |
|---|---:|---:|---:|---:|
| MLP only | 96.7%, −1.102 | 95.0%, −1.150 | 1.011, 0.989 | 1.010, 0.989 |
| MLP + regret path | 100%, −1.001 | 99.4%, −1.011 | 1.024, 0.981 | 1.001, 1.000 |
| Regret path alone, no learning | 99.4%, −1.016 | 97.8%, −1.061 | 1.030, 0.979 | 1.000, 1.000 |
| Exact optimum | | | 1.030, 0.979 | 1.000, 1.000 |

With the MLP alone the policy ignored the preference: α=0 and α=1 took nearly the same routes. The
regret-only row is why `verify` always reports the prior next to the policy: **most of the
performance comes from the two Dijkstra potentials**, and training's contribution is mainly fewer dead ends at
mid-range preferences (93.3% → 98.3% reached at α=0.5 on perturbed fields).

## Results

Run [`20261103T081938Z-scalarized-bike-v1-6eab87d4`](20261103T081938Z-scalarized-bike-v1-6eab87d4/summary.md),
commit `6efe87f` with a clean tree, seed 42, 87,043 parameters. It took 2 h 49 min on CPU. Each further run gets its
own folder here, with a generated `summary.md`.

**The selected checkpoint is the warm start (update 0): PPO on the scalarized reward did not improve validation
return.** The run stopped early at update 150, after 5 evaluations without improvement. The teacher it imitates
optimises the same α/β objective, but the selected weights have had no PPO update.

| Validation, selected checkpoint | Real fields | Perturbed fields (synthetic, not used for selection) |
|---|---:|---:|
| Routes reached (11 preferences, 1,650 episodes) | 100% | 98.4% |
| Mean return | −1.0003 | −1.0432 |
| Optimality gap on reached routes | 0.08% | 0.19% |
| α=0, T/T_ref and E/E_ref (exact optimum) | 1.003, 0.999 (1.002, 0.998) | 1.021, 0.982 (1.025, 0.981) |
| α=1, T/T_ref and E/E_ref (exact optimum) | 1.001, 1.001 (1.000, 1.000) | 1.001, 0.999 (1.000, 1.000) |

- **Preference:** on perturbed fields one network moves along the trade-off as α changes. On real IDW fields there is
  almost nothing to trade: even the exact optimum saves only 0.2% exposure.
- **Against the regret prior** (greedy on the scalarized regret, no learned part): the policy is better at α=0.5, with 97.3%
  reached against 94.0% on perturbed fields and 100% against 99.3% on real ones. The prior is slightly better at α=0 and
  α=1, where it follows an exact single-objective route.
- **Reload:** `best.pt` matches its SHA-256, and 750 routes were identical across two loads. Median 0.45 s per route on CPU
  (p95 1.77 s, maximum 12.9 s).
- These numbers come from the episodes that selected the checkpoint. Unbiased numbers belong to TI-AI-10.
- **Serving:** the 12.9 s maximum is above the design's 8 s limit, so a request like that would fall back to the baseline
  route. TI-AI-10 and the serving work should measure how often this happens.

**The selection metric is nearly flat on real fields.** Every reasonable policy scores about −1 there, so update 0 won by
0.0001 over update 30. "PPO did not improve validation return" is true of this metric, but the metric can barely
move. The perturbed fields do not show a clear PPO gain either:

| Update | Real: return, dead ends | Perturbed: return, dead ends | Perturbed return, excluding dead ends |
|---:|---:|---:|---:|
| 0 (selected) | −1.0003, 0 | −1.0391, 11 | −0.9951 |
| 30 | −1.0004, 0 | −1.0269, 8 | −0.9949 |
| 60 | −1.0102, 2 | −1.0208, 6 | −0.9968 |
| 90 | −1.0132, 3 | −1.0405, 11 | −0.9965 |
| 120 | −1.0211, 5 | −1.0442, 12 | −0.9961 |
| 150 | −1.0012, 0 | −1.0290, 8 | −0.9970 |

A dead end costs about 0.004 of the mean return over 750 episodes, so the perturbed return follows the dead-end count,
which rises and falls without a trend. Reached routes do not get better. The selection rule stays as it was declared
before the run, because changing it after seeing these numbers would select on them. A future run could declare a
tolerance on real fields with ties broken on perturbed validation fields.

`checkpoints/update_00150.pt` in the run folder is kept as evidence of a checkpoint that PPO did update, so TI-AI-10 can
measure what RL training adds (SHA-256 `d43df02a...`). It was **not selected**. It is the only one of the retained
checkpoints (updates 90, 120 and 150; `keep_checkpoints` is 3) with no dead ends on real fields. Updates 30 and 60 were pruned
during the run.

## Running it locally (Windows, PowerShell, from the repo root)

Windows notes that apply to this machine: **Smart App Control blocks the `pyproj` DLL**, so
step 2 runs in Docker. Training does not need `pyproj`. There is no GPU here, and none is
needed, because the environment is pure Python.

**1. Environment.** The venv reuses the global Python's torch, numpy, pandas, matplotlib and
networkx, and adds the only missing package.

```powershell
python -m venv --system-site-packages .cache\venv-morl
.cache\venv-morl\Scripts\Activate.ps1
python -m pip install pyarrow
python -m unittest src.backend.tests.test_morl_scalarized
```

**2. Exposure fields.** Skip this if `src\backend\route_model\runs\fields\manifest.json`
already exists. With Docker Desktop running, this takes about 10 minutes. The image is the
Data Worker's, which already has `pyproj`. Build it first with `docker compose build data_worker`;
Compose names it `<project>-data_worker`, where the project is the checkout folder's name in lower
case (`sippolusi-data_worker` for a `SIPPolusi` folder).

```powershell
docker compose build data_worker
docker run --rm -v "${PWD}:/repo" -w /repo sippolusi-data_worker:latest sh -c "pip install -q networkx; python -m src.backend.route_model.morl.fields --out src/backend/route_model/runs/fields"
```

The result should have 17 train, 7 val and 7 test windows, and the `values_sha256` of each window
should equal those in [`../fields_manifest.json`](../fields_manifest.json). Other hashes in that manifest can
differ because of line endings on Windows. Without Docker, ask PIC B for the `fields` folder.

**3. Pipeline check (about 3 minutes).**

```powershell
python -m src.backend.route_model.morl.scalarized train --config src/backend/route_model/morl/configs/scalarized-bike-smoke.json --fields src/backend/route_model/runs/fields --out src/backend/route_model/runs/smoke
```

It should end with `"status": "completed"`. Its low success rate is expected, since it trains for only
a moment. Delete `src\backend\route_model\runs\smoke` afterwards.

**4. Real training.** Commit the code first, so the manifest names a commit with a clean tree.
Keep the terminal open and the machine awake.

```powershell
python -m src.backend.route_model.morl.scalarized train --config src/backend/route_model/morl/configs/scalarized-bike-v1.json --fields src/backend/route_model/runs/fields --out src/backend/route_model/runs/scalarized
```

Add `--max-hours 6` to stop cleanly after a set time. After any stop (Ctrl-C, time limit, crash),
continue with the same config and the run folder printed at the start:

```powershell
python -m src.backend.route_model.morl.scalarized train --config src/backend/route_model/morl/configs/scalarized-bike-v1.json --fields src/backend/route_model/runs/fields --resume src/backend/route_model/runs/scalarized/<run_id>
```

Watch progress in the terminal (`update 20: val return ..., success ...`), or in
`curves.csv` and `metrics.jsonl` in the run folder.

**5. Verify and export the evidence.**

```powershell
python -m src.backend.route_model.morl.scalarized verify --run src/backend/route_model/runs/scalarized/<run_id> --fields src/backend/route_model/runs/fields
python -m src.backend.route_model.morl.scalarized export --run src/backend/route_model/runs/scalarized/<run_id>
```

`verify` takes about 20 minutes. It must report `"checkpoint_matches_manifest": true` and `"identical": true`,
and it also measures the regret prior. `export` writes
`training_curves.png` and `summary.md` and copies the evidence to this folder, about 0.5 MB, ready to commit.
Periodic checkpoints and `last.pt` stay in `runs/`, which git ignores.

**Notebook instead of the terminal.** [`notebooks/route_model/train_morl_scalarized.ipynb`](../../../../notebooks/route_model/train_morl_scalarized.ipynb)
calls the same functions and shows the curves inline. Set `CONFIG_NAME` in its first code cell, and use the
`.cache\venv-morl` kernel. For a long run the terminal is safer; you can train there and then set `EXISTING_RUN` in the notebook
to report on the finished run.

## How the acceptance criteria are met

| Criterion | Evidence in the run folder |
|---|---|
| A checkpoint reloads and gives the same inference on the same input | `verification.json`: `best.pt` matches the SHA-256 in the manifest; two separate loads give identical greedy routes on every validation OD pair at every selection preference. `evaluation.json → reload_check` holds the same check made during the run |
| Each run has an identity, a configuration and clear data and graph references | `manifest.json`: run ID (time, config name, config hash), configuration and its hash, seed, commit and whether the tree was clean, library versions, graph file hashes, per-window field hashes, OD set hash. `config.json` and `od_pairs.json` are saved beside it |
| Checkpoints are selected on validation data, not the test split | `manifest.json → checkpoint_selection`: the highest mean greedy return over α ∈ {0, 0.25, 0.5, 0.75, 1} on real validation fields and validation OD pairs. No test OD pair is sampled. Test windows are named for the split check, but their files are never opened; a unit test proves this by training with those files absent |
| A failed or early-stopped run is recorded with its reason and remaining artifacts | `manifest.json → status`, `stop_reason`, `artifacts`, and `traceback` for a crash. Statuses: `completed`, `early_stopped`, `stopped` (time, memory or disk limit), `interrupted`, `failed`. Unit tests cover a time stop with resume, and divergence |

## Reading the results honestly

- **Check `summary.md` for which checkpoint was selected.** If it is update 0, the selected policy is the warm
  start: PPO on the scalarized reward did not improve it, and the report must say so.
- **Expect little response to the preference on real fields.** PIC B found that the exact optimum saves
  only about 0.2% exposure on these IDW fields. The perturbed-field tables, labelled synthetic and not used for selection,
  show whether the policy can follow the preference where a trade-off exists.
- **Report the policy next to the regret prior** (`summary.md` has the table). It shows what training added beyond
  the two cost-to-go searches, which may be little.
- **Validation numbers are slightly optimistic**, because the same episodes selected the checkpoint. Unbiased
  numbers belong to TI-AI-10, which samples test pairs from the same split rule.
- **Exact search remains the benchmark.** The features already run two Dijkstra searches per request, and the
  weighted A* of TI-AI-06 never dead-ends. TI-AI-10 should compare with it as well.
- **Fields are IDW only**, trips are 0.5–3 km, and only bike mode is trained.

## See also

- [`docs/experiments/TI-AI-09/README.md`](../README.md): PIC B multi-policy set
- [`docs/experiments/TI-AI-10/README.md`](../../TI-AI-10/README.md): TI-AI-10 evaluation (PIC B only)
- [`docs/experiments/TI-AI-11/README.md`](../../TI-AI-11/README.md): packaging (no packager for PIC A yet)
