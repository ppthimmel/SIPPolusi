# TI-AI-08 training environment verification

Environment `route-env-1` ([specification](../../../src/backend/route_model/TRAINING_ENV.md)).
It runs on graph `jakarta-20261005` in bike mode: 299,067 nodes and 303,972
bike-allowed edges. The OSM export is the one from merged PR #44, and its file
hashes are in `manifest.json`. Reproduce from the repo root:

```shell
python -m unittest src.backend.tests.test_training_env -v
python -m src.backend.route_model.train_env_smoke
```

Two consecutive runs of the smoke script produced byte-identical
`od_pairs.json`, `manifest.json`, `episodes.jsonl` and `summary.json` (seed 42).

| File | Contents |
|---|---|
| [`od_pairs.json`](od_pairs.json) | Split rule, sampling configuration, rejection counts, and 40/10/10 train/val/test OD pairs |
| [`manifest.json`](manifest.json) | Environment version, graph file SHA-256, weight fingerprint and coverage, `EnvConfig`, OD set hash |
| [`episodes.jsonl`](episodes.jsonl) | One record per episode (20 episodes) |
| [`summary.json`](summary.json) | Aggregates overall and per scripted policy |

## OD configuration

The spatial split uses 0.02° blocks assigned 70/15/15 by `SHA-256(seed=42, block)`.
OD endpoints are 500–3,000 m apart and must be reachable in the directed graph.

| Split | Pairs | Attempts | Rejected: distance | Rejected: unreachable |
|---|---:|---:|---:|---:|
| train | 40 | 916 | 876 | 0 |
| val | 10 | 215 | 204 | 1 |
| test | 10 | 112 | 102 | 0 |

Only the 40 train pairs are passed to the training environment. The unit tests
check three things:

- A mixed OD set is refused (`split_violation`).
- `reset(od_id=<test pair>)` is refused (`unknown_od`).
- A weight snapshot from a test time window is refused.

Preferences are drawn as `α ~ U[0, 1]`, `β = 1 − α`.

## Smoke test result

The environment has `max_steps = 400`, `max_steps_factor = 3`, `failure_return = 4`
and `illegal_action_penalty = 0.1`. The weights are synthetic, so the time-window
split is explicitly opted out with `window_splits=None`. Two scripted policies ran
10 episodes each:

| Policy | Reached | Dead end | Truncated | Mean return | Mean steps | Illegal actions |
|---|---:|---:|---:|---:|---:|---:|
| `reference` (follows the fastest route) | 10 | 0 | 0 | −1.000 | 64.9 | 0 |
| `random` (uniform legal action, one injected illegal action) | 0 | 10 | 0 | −4.000 | 7.9 | 10 |

Every failed episode returns exactly −4, regardless of how far it got. The longest
reference route has 133 edges, below `max_steps`.

The `reference` policy reaches every destination with return −1 (within floating
point) for every sampled α. Its `time_ratio` and `exposure_ratio` are both 1. This
confirms that the summed step rewards equal `−(α·T/T_ref + β·E/E_ref)` on the real
graph. The `random` policy shows that illegal actions and dead-end terminations,
settled at the fixed failure return, are recorded per episode. Truncation is covered by the unit
test `test_truncation_at_step_limit`, because random walks on this graph hit a dead
end before the step limit.

## Limitations

- **Synthetic edge weights.** The OSM export carries no pollution snapshot, and the
  full TI-AI-05 `edge_weights.parquet` is not committed. The smoke test therefore
  uses a deterministic synthetic exposure index, `1.5 + 0.5·sin(300x)·cos(300y)` at
  each edge's midpoint. It deliberately leaves 2% of edges missing (`edge_id % 50 == 0`).
  Those 6,037 edges are imputed with the median, 1.500, and counted in the manifest.
  For a real snapshot, pass `--weights <run>/edge_weights.parquet --time-window <ISO window>
  --windows <all windows, comma-separated>`. The window must fall in the train split.
- **Simple-path routes.** The default `allow_revisit=False` restricts routes to simple
  paths, so a policy can trap itself in a dead end. This is reported as an explicit
  outcome, not hidden.
- **Training set size.** The 60 OD pairs here are a smoke-test sample. The pair count
  and distance range for actual training belong to TI-AI-09's configuration.
- **TI-AI-06 code not reused.** The cost helpers in TI-AI-06 (`costs.py`) are not
  merged into `main` yet. This environment implements the same objective,
  `α·T/T_ref + β·E/E_ref` with the fastest route as reference and a zero reference
  normalised by 1. It should switch to the shared functions once TI-AI-06 is merged.
