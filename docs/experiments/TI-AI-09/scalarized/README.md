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
| Actor-Critic | Actor scores each outgoing arc, with invalid arcs masked. The score subtracts the arc's scalarized regret, `α·time regret + β·exposure regret`, times a learned scale; the MLP learns the correction. Critic estimates the scalarized return and does not train the encoder | Same structure, plus the regret path |
| Warm start | Imitates the exact weighted-optimal route for each episode's preference | Same code, same 400 episodes |
| PPO | Same hyperparameters | Same code |
| Data | Same fields (31 of 31 window hashes identical), same OD configuration, same environment settings | Same |

## Design decision: the direct regret path

Warm start only, 400 episodes, 60 validation pairs, seed 42 (development numbers, not a result):

| Head | Real: reached, return | Perturbed: reached, return | Perturbed α=0: T/T_ref, E/E_ref | Perturbed α=1: T/T_ref, E/E_ref |
|---|---:|---:|---:|---:|
| MLP only | 96.7%, −1.102 | 95.0%, −1.150 | 1.011, 0.989 | 1.010, 0.989 |
| MLP + regret path | 100%, −1.001 | 99.4%, −1.011 | 1.024, 0.981 | 1.001, 1.000 |
| Regret path alone, no learning | 99.4%, −1.016 | 97.8%, −1.061 | 1.030, 0.979 | 1.000, 1.000 |
| Exact optimum | | | 1.030, 0.979 | 1.000, 1.000 |

With the MLP alone the policy ignored the preference: α=0 and α=1 took nearly the same routes. The
last row but one is why `verify` always reports the prior next to the policy: **most of the
performance comes from the two Dijkstra potentials**, and training's contribution is mainly fewer dead ends at
mid-range preferences (93.3% → 98.3% reached at α=0.5 on perturbed fields).

## Results

No reportable run is committed yet. After step 5 of the guide below, each run has its own
folder here, with a generated `summary.md`.

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
Data Worker's, which already has `pyproj`.

```powershell
docker run --rm -v "${PWD}:/repo" -w /repo sipci-data_worker:latest sh -c "pip install -q networkx; python -m src.backend.route_model.morl.fields --out src/backend/route_model/runs/fields"
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
