# MORL training environment (TI-AI-08)

`training_env.py` defines routing as a multi-objective reinforcement learning
problem on the versioned OSM road graph. It has no ML dependency. The API follows
Gymnasium's convention: `reset()` returns `(obs, info)`, and `step(action)` returns
`(obs, reward, terminated, truncated, info)`.

```python
from src.backend.route_model.astar_osm import get_router, DEFAULT_GRAPH
from src.backend.route_model.training_env import (
    EnvConfig, EpisodeLogger, RouteEnv, load_od_pairs, load_weights, split_windows)

router = get_router(DEFAULT_GRAPH, "bike")
# edge_weights.parquet of one `spatial_model infer` run (a CSV with the same columns also works)
weights = load_weights("runs/<run_id>/edge_weights.parquet", router.version, "2026-10-07T16:00:00Z")
window_splits = split_windows(all_available_windows)  # every window in the study period
train = load_od_pairs("docs/experiments/TI-AI-08/od_pairs.json", split="train",
                      graph_version=router.version)
env = RouteEnv(router, weights, train, EnvConfig(split="train", seed=42),
               window_splits=window_splits, logger=EpisodeLogger("runs/episodes.jsonl"))
obs, info = env.reset()
obs, reward, terminated, truncated, info = env.step(0)
```

## Inputs and versioning

| Input | Source | Check |
|---|---|---|
| Graph | `RoadRouter` from `get_router` (the export's active `graph_version`) | The edge times are those used by the A* baseline (TI-AI-07) |
| Edge weights | `ExposureSnapshot` (graph version, time window, name, `edge_id → value`), e.g. from `load_weights` | `graph_version` must equal the router's version; the time window must belong to the environment's split |
| OD pairs | `sample_od_pairs`, saved with `save_od_pairs` | Every pair must belong to the environment's split |

`env.manifest(graph_dir)` records the environment version, the graph version, the
SHA-256 of the three export files, the SHA-256 of the weight mapping, edge coverage,
the configuration, and the SHA-256 of the OD set. Store it with every training run.

The weight value is the backend's dimensionless exposure index `I_e` (TI-AI-05
EdgeWeight `exposure_index`). `load_weights` reads the `edge_weights.parquet` that
`spatial_model infer` writes per run (this needs pandas with a Parquet engine such
as pyarrow), or a CSV with the same `edge_id` and `exposure_index` columns. A concentration in µg/m³ can be supplied instead; the
reward is normalised, so only the reported totals change unit. **Missing values**:
an edge with no value receives the median of the covered edges. This matches the
planner's `imputed_median` rule. The number of imputed edges is in the manifest,
and each episode reports `imputed_edges_traversed`. A value is never zero-filled.
Negative or non-finite values, a graph-version mismatch, or a snapshot that covers
no edge are rejected when the environment is constructed.

## State

The environment's internal state is `(current node, destination node, visited nodes,
T so far, E so far, step count, α, β)`. The observation is a plain dictionary:

| Key | Meaning |
|---|---|
| `node`, `destination` | OSM node IDs |
| `preference` | `[α, β]` |
| `time_ratio`, `exposure_ratio` | `T/T_ref` and `E/E_ref` accumulated so far |
| `steps`, `steps_left` | Steps taken, and steps remaining before truncation |
| `illegal_actions` | Illegal actions so far in this episode |
| `distance_to_goal_m` | Geodesic distance from `node` to `destination` |
| `actions[i]` | `edge_id`, `neighbor`, `forward`, `time_ratio` (`t_e/T_ref`), `exposure_ratio` (`I_e·t_e/E_ref`), `distance_to_goal_m` of the neighbor, `visited` |
| `action_mask[i]` | Whether action `i` is legal |

`T_ref` and `E_ref` are the time and exposure of the fastest (A*) route for the
episode's OD pair. This is the reference used by the TI-AI-06 objective and by the
mock solver. If `E_ref = 0`, exposure is divided by 1, which is the same policy as
the mock solver.

## Action and transition

Action `i` traverses the `i`-th outgoing arc of the current node in
`router.arcs[node]`. Arcs are sorted by `(neighbor, time, edge_id)`, so actions are
deterministic. The action count varies by node, so policies must use `action_mask`.
Arc direction respects one-way restrictions for the mode. The transition is
deterministic: the agent moves to `neighbor`, and `t_e` and `I_e·t_e` are added to
the totals.

By default (`allow_revisit=False`), arcs into visited nodes are masked, so every
route is a simple path. Actions may be any integer type, including numpy integers
such as the `np.int64` that `np.argmax` returns; `bool` is never an action.
**Illegal action**: any action that is out of range, not an integer, or masked. The agent stays at the same node, both reward components receive
`-illegal_action_penalty` (default 0.1), the step counts toward the limit, and
`info["illegal_action"]` is `true`. The episode record counts illegal actions.

## Terminal conditions

| Outcome | Condition | `terminated` / `truncated` | Episode return |
|---|---|---|---|
| `reached` | `node == destination` | true / false | `−weighted_cost` minus any illegal-action penalties |
| `dead_end` | No legal action left from the current node | true / false | exactly `−failure_return` (default 4.0) |
| `truncated` | `steps ≥ step_limit` | false / true | exactly `−failure_return` |

`step_limit = max(R, min(max_steps, ceil(max_steps_factor × R)))`, where `R` is the
reference route's edge count. The defaults are 1000 and 3. The outer `max` means the
fastest route always fits, even when `max_steps` is smaller than `R`. Calling `step`
after an episode has ended raises `RuntimeError`.

**Invalid episodes** are rejected before the first step by raising `InvalidEpisode`
with a `reason` code. Pairs from `sample_od_pairs` have already been checked for
reachability, so none of these fire during normal sampling:

| `reason` | When |
|---|---|
| `no_route` | Destination is unreachable (disconnected graph) |
| `same_node` | Origin and destination are the same node |
| `unknown_node` | A node has no outgoing arc (origin) or no incoming arc (destination) for this mode |
| `unknown_od` | `reset(od_id=...)` names a pair outside this environment's split |
| `split_violation` | An OD pair from another split is passed in; the weight time window is not in exactly this split; or `window_splits=None` with a real (non-synthetic) snapshot |
| `invalid_preference` | `α, β ∉ [0, 1]` or `|α + β − 1| > 1e-6` |

## Reward

Each legal step on edge `e` gives a vector reward and a scalar reward:

```
r_vec = (−t_e / T_ref, −I_e·t_e / E_ref)
r     = α·r_vec[0] + β·r_vec[1]
```

Illegal-action penalties are added to both vector components. Because `α + β = 1`,
the scalar reward is always exactly the scalarised vector. Scalarised MORL can use `r`;
multi-policy or preference-conditioned MORL can use `info["reward_vector"]`. For an
episode that reaches the destination without illegal actions, the return is

```
G = −(α·T(R)/T_ref + β·E(R)/E_ref) = −weighted_cost(R)
```

This is the TI-AI-06 objective. The fastest route therefore scores `G = −1` for any
preference. With `T(R) = Σ t_e` in seconds and `E(R) = Σ I_e·t_e` in index·seconds,
a route that is cleaner but slower scores better than −1 only when `β` is large
enough. Example (unit test `test_hand_calculated_preference_tradeoff`):

- Fastest route: `T = 20 s`, `E = 80`.
- Cleaner route: `T = 30 s`, `E = 30`, normalised to `(1.5, 0.375)`.
- At `α = 0.8`, the cleaner route gives `G = −1.275` (worse than −1).
- At `α = 0.2`, the cleaner route gives `G = −0.6` (better than −1).

**Failure return.** The last step of a failed episode (`dead_end` or `truncated`)
settles each component at exactly `−failure_return`:

```
r_vec_final = (−failure_return − Σ earlier r_time, −failure_return − Σ earlier r_exposure)
```

This step offsets everything accumulated so far, including the failing step's own
cost and any illegal-action penalties. The return is then `−failure_return` however
long the agent searched. An earlier version subtracted a fixed penalty from the
cost accumulated so far, which made failing early cheaper than searching longer:
walking into the nearest cul-de-sac scored about −2.2, while searching up to the
step limit scored about −5. With a fixed failure return, all failures score the
same (unit test `test_failing_early_is_not_cheaper_than_failing_late`). The final
step's reward depends on the accumulated cost and penalties, but all of them are
part of the observation (`time_ratio`, `exposure_ratio`, `illegal_actions`), so the
reward stays Markov in the observation (unit test
`test_failure_settlement_is_determined_by_the_observation`).

**Training risk:** a successful route whose weighted cost exceeds `failure_return`
still scores worse than failing. With the default of 4.0, any route within four
times the reference time and exposure costs at most 4.0, so it scores at least as
well as failing. Raise it if TI-AI-09's
policies produce longer successful detours, and keep it fixed within a run so that
returns stay comparable.

## OD samples, splits and preferences

**Spatial split** (`SplitSpec`): the area is divided into 0.02° blocks, about
2.2 km at Jakarta's latitude. Each block is assigned train, val or test (70/15/15)
using `SHA-256(seed, block)`. An OD pair belongs to a split only if both endpoints
lie in blocks of that split; pairs that cross splits are discarded. A training
environment therefore never starts or ends an episode in a test block. Routes may
still pass through test blocks, because the road graph is shared.

**Sampling** (`sample_od_pairs`): a seeded RNG draws an origin node with an outgoing
arc and a destination node with an incoming arc, both from the split. A pair is
kept if the endpoints are `min..max` metres apart (geodesic) and the destination is
reachable through the strongly connected component DAG. Rejections are counted by
reason (`distance`, `unreachable`, `duplicate`) and saved with the pairs.

**Time windows** (`split_windows`): the windows are split chronologically, with the
earliest going to train and the latest to test. Val and test each get
`round(n × fraction)` windows (at least one), and train takes the rest. The
fractions must be nonnegative, sum to 1, and give train a positive share.
`RouteEnv` requires a `window_splits` argument and accepts a snapshot only if its
time window lies in exactly the environment's split. Omitting the argument is a
`TypeError`. Passing `window_splits=None` is an explicit opt-out that is allowed
only for a synthetic snapshot (`time_window == SYNTHETIC_WINDOW`, `"synthetic"`), as
in the smoke test. A real snapshot without a split is rejected with `split_violation`.

**Preferences** (`PreferenceSpec`): `uniform` draws `α ~ U[0, 1]` with `β = 1 − α`
(training). `grid` draws from a fixed grid, by default `0, 0.25, 0.5, 0.75, 1`
(validation and evaluation sweeps). `reset(preference=(α, β))` fixes the preference
explicitly.

## Seeds and logging

`EnvConfig.seed` seeds the environment's RNG, which selects OD pairs and
preferences; `reset(seed=...)` reseeds it. OD sampling has its own `seed`, and the
split rule has its own `SplitSpec.seed`. Equal seeds give identical episode
sequences (unit test `test_seeded_episodes_are_reproducible`).

`EpisodeLogger` writes one JSON line per finished episode with these fields:

- Identity: `episode`, `env_version`, `split`, `env_seed`, `reset_seed`, `graph_version`, `mode`, `time_window`, `weight`, `od_id`, `start`, `end`, `alpha`, `beta`
- Outcome: `outcome`, `steps`, `step_limit`, `illegal_actions`
- Returns: `return`, `return_time`, `return_exposure`
- Route: `travel_time_s`, `exposure`, `time_ratio`, `exposure_ratio`, `weighted_cost`, `route_edges`, `imputed_edges_traversed`
- Reference: `reference_time_s`, `reference_exposure`, `reference_edges`

`summary()` aggregates outcome counts, success rate, mean return and steps, illegal
actions, and the mean ratios over episodes that reached the destination.

## Verification

```shell
python -m unittest src.backend.tests.test_training_env -v
python -m src.backend.route_model.train_env_smoke
```

The smoke script runs on the Jakarta export and writes `od_pairs.json`,
`manifest.json`, `episodes.jsonl` and `summary.json` to
`docs/experiments/TI-AI-08/`. See that folder's README for the results.
