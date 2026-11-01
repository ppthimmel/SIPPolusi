"""Multi-objective routing environment for MORL training (TI-AI-08).

See TRAINING_ENV.md for the state, action, transition, reward and split
contract. The API follows Gymnasium's reset/step convention without depending
on it: reset() -> (obs, info), step(a) -> (obs, reward, terminated, truncated, info).
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import numbers
import random
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .astar_osm import RoadRouter, distance_m
from .route_metrics import ExposureSnapshot

ENV_VERSION = "route-env-1"
SPLITS = ("train", "val", "test")
PREFERENCE_TOLERANCE = 1e-6
# Time window of a synthetic weight field. Only such snapshots may skip the
# time-window split (window_splits=None); a real snapshot must name its split.
SYNTHETIC_WINDOW = "synthetic"
GRAPH_FILES = ("graph_version.csv", "road_node.csv.gz", "road_edge.csv.gz")


class InvalidEpisode(ValueError):
    """An episode that cannot be started; `reason` is a stable machine-readable code."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ODPair:
    od_id: str
    start: int  # node with an outgoing arc for the router's mode
    end: int    # node with an incoming arc for the router's mode
    split: str


@dataclass(frozen=True)
class SplitSpec:
    """Spatial block split: a node's split is decided by the block it lies in.

    Blocks are `block_deg` x `block_deg` cells (0.02 deg is about 2.2 km at
    Jakarta's latitude). Each block gets a split from a SHA-256 hash of
    (seed, block), so the rule is reproducible across machines and Python runs.
    An OD pair belongs to a split only if both endpoints' blocks do; pairs
    crossing splits are discarded. Test areas are therefore never visited as
    an origin or destination during training.
    """
    block_deg: float = 0.02
    fractions: tuple[float, float, float] = (0.7, 0.15, 0.15)
    seed: int = 0

    def __post_init__(self):
        if not (self.block_deg > 0 and math.isfinite(self.block_deg)):
            raise ValueError("block_deg must be positive and finite")
        if len(self.fractions) != 3 or any(f < 0 for f in self.fractions) or abs(sum(self.fractions) - 1) > 1e-9:
            raise ValueError("fractions must be three nonnegative values summing to 1")

    def block(self, xy):
        return math.floor(xy[0] / self.block_deg), math.floor(xy[1] / self.block_deg)

    def split_of(self, xy):
        digest = hashlib.sha256(f"{self.seed}:{self.block(xy)}".encode()).digest()
        u = int.from_bytes(digest[:8], "big") / 2**64
        bound = 0.0
        for name, fraction in zip(SPLITS, self.fractions):
            bound += fraction
            if u < bound:
                return name
        return SPLITS[-1]


def split_windows(windows, fractions=(0.7, 0.15, 0.15)):
    """Chronological split of time windows: earliest to train, latest to test.

    ISO-8601 UTC strings sort chronologically. Val and test get
    round(n * fraction) windows, at least one each when their fraction is
    positive; train takes the remainder.
    """
    if (len(fractions) != 3 or any(f < 0 for f in fractions) or fractions[0] == 0
            or abs(sum(fractions) - 1) > 1e-9):
        raise ValueError("fractions must be three nonnegative values summing to 1, with a positive train share")
    windows = sorted(set(windows))
    if len(windows) < 3:
        raise ValueError("At least three time windows are needed for train/val/test")
    n_val = max(1, round(len(windows) * fractions[1])) if fractions[1] else 0
    n_test = max(1, round(len(windows) * fractions[2])) if fractions[2] else 0
    n_train = len(windows) - n_val - n_test
    if n_train < 1:
        raise ValueError("Not enough time windows for a training split")
    return {"train": tuple(windows[:n_train]), "val": tuple(windows[n_train:n_train + n_val]),
            "test": tuple(windows[n_train + n_val:])}


@dataclass(frozen=True)
class PreferenceSpec:
    """How (alpha, beta) is drawn per episode; beta = 1 - alpha always.

    `uniform` draws alpha from U[0, 1] (training); `grid` draws from `grid`
    (validation/evaluation sweeps, so every preference is covered evenly).
    """
    mode: str = "uniform"
    grid: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)

    def __post_init__(self):
        if self.mode not in ("uniform", "grid"):
            raise ValueError("Preference mode must be uniform or grid")
        if not self.grid or any(not 0 <= a <= 1 for a in self.grid):
            raise ValueError("Preference grid values must lie in [0, 1]")

    def sample(self, rng):
        alpha = rng.random() if self.mode == "uniform" else rng.choice(self.grid)
        return alpha, 1.0 - alpha


@dataclass(frozen=True)
class EnvConfig:
    split: str = "train"
    seed: int = 0
    # Episode step limit: min(max_steps, ceil(max_steps_factor * reference edges)),
    # but never below the reference edge count, so the fastest route always fits.
    max_steps: int = 1000
    max_steps_factor: float = 3.0
    # A failed episode (truncated or dead end) returns exactly -failure_return
    # on both components, however far it got: failing early is never cheaper
    # than searching longer. Successful routes cost their weighted cost.
    failure_return: float = 4.0
    # Added to both reward components for an illegal action; the state is unchanged.
    illegal_action_penalty: float = 0.1
    # False masks arcs into already visited nodes, so routes are simple paths.
    allow_revisit: bool = False
    preference: PreferenceSpec = field(default_factory=PreferenceSpec)

    def __post_init__(self):
        if self.split not in SPLITS:
            raise ValueError(f"split must be one of {SPLITS}")
        if isinstance(self.max_steps, bool) or not isinstance(self.max_steps, int) or self.max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        if not (self.max_steps_factor >= 1 and math.isfinite(self.max_steps_factor)):
            raise ValueError("max_steps_factor must be finite and at least 1")
        if not (self.failure_return > 0 and math.isfinite(self.failure_return)):
            raise ValueError("failure_return must be positive and finite")
        if not (self.illegal_action_penalty >= 0 and math.isfinite(self.illegal_action_penalty)):
            raise ValueError("illegal_action_penalty must be finite and nonnegative")


def _reachable_components(router, component, cache):
    if component not in cache:
        reachable, stack = {component}, [component]
        while stack:
            for neighbor in router.dag.get(stack.pop(), ()):
                if neighbor not in reachable:
                    reachable.add(neighbor)
                    stack.append(neighbor)
        cache[component] = reachable
    return cache[component]


def sample_od_pairs(router: RoadRouter, spec: SplitSpec, counts, *, seed=0,
                    min_distance_m=500.0, max_distance_m=5000.0, max_attempts_factor=200):
    """Sample reachable OD node pairs per split; returns (pairs, stats).

    `counts` maps split name to the number of pairs wanted. Both endpoints must
    lie in blocks of that split, be min..max metres apart (geodesic), and the
    destination must be reachable from the origin in the directed graph.
    Rejections are counted by reason so the sampling can be reported.
    """
    if not 0 < min_distance_m <= max_distance_m:
        raise ValueError("Require 0 < min_distance_m <= max_distance_m")
    rng = random.Random(seed)
    starts = {name: [] for name in SPLITS}
    ends = {name: [] for name in SPLITS}
    for node in sorted(router.arcs):
        starts[spec.split_of(router.coords[node])].append(node)
    for node in sorted(router.incoming):
        ends[spec.split_of(router.coords[node])].append(node)
    pairs, reach_cache = [], {}
    stats = {"seed": seed, "min_distance_m": min_distance_m, "max_distance_m": max_distance_m,
             "candidate_origins": {k: len(v) for k, v in starts.items()},
             "candidate_destinations": {k: len(v) for k, v in ends.items()}, "splits": {}}
    for name in SPLITS:
        wanted = counts.get(name, 0)
        rejected = {"distance": 0, "unreachable": 0, "duplicate": 0}
        chosen, attempts = set(), 0
        if wanted and (not starts[name] or not ends[name]):
            raise ValueError(f"Split {name} has no candidate nodes; adjust SplitSpec")
        while len(chosen) < wanted:
            attempts += 1
            if attempts > wanted * max_attempts_factor:
                raise ValueError(f"Could not sample {wanted} {name} OD pairs; relax the constraints")
            start, end = rng.choice(starts[name]), rng.choice(ends[name])
            if (start, end) in chosen:
                rejected["duplicate"] += 1
                continue
            if not min_distance_m <= distance_m(router.coords[start], router.coords[end]) <= max_distance_m:
                rejected["distance"] += 1
                continue
            if router.component[end] not in _reachable_components(router, router.component[start], reach_cache):
                rejected["unreachable"] += 1
                continue
            chosen.add((start, end))
            pairs.append(ODPair(f"{name}-{len(chosen) - 1:05d}", start, end, name))
        stats["splits"][name] = {"pairs": wanted, "attempts": attempts, "rejected": rejected}
    return pairs, stats


def save_od_pairs(path, pairs, spec: SplitSpec, stats, graph_version):
    payload = {"env_version": ENV_VERSION, "graph_version": graph_version,
               "split_spec": asdict(spec), "sampling": stats,
               "pairs": [asdict(p) for p in pairs]}
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_od_pairs(path, *, split=None, graph_version=None):
    """Load OD pairs; with `split`, only that split's pairs are returned."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if graph_version is not None and payload["graph_version"] != graph_version:
        raise ValueError("OD pairs were sampled on a different graph version")
    pairs = [ODPair(**p) for p in payload["pairs"]]
    return [p for p in pairs if split is None or p.split == split]


def load_weights(path, graph_version, time_window, column="exposure_index"):
    """Read EdgeWeights (edge_id plus `column`) into an ExposureSnapshot.

    Accepts the `edge_weights.parquet` written by `spatial_model infer`
    (needs pandas with a Parquet engine) or a CSV with the same columns.
    Empty cells are treated as missing, never as zero.
    """
    path = Path(path)
    values = {}
    if path.suffix == ".parquet":
        import pandas as pd  # Optional: only Parquet input needs it.

        frame = pd.read_parquet(path, columns=["edge_id", column]).dropna(subset=[column])
        values = {int(e): float(v) for e, v in zip(frame["edge_id"], frame[column])}
    else:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                if row.get(column) not in (None, ""):
                    values[int(row["edge_id"])] = float(row[column])
    return ExposureSnapshot(graph_version, time_window, column, values)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def weights_fingerprint(weights: ExposureSnapshot):
    items = sorted(weights.concentration_ug_m3.items())
    return hashlib.sha256(json.dumps(items, allow_nan=False).encode()).hexdigest()


class EpisodeLogger:
    """Append one JSON line per finished episode and keep a running summary."""

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.records = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def log(self, record):
        self.records.append(record)
        if self.path:
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, allow_nan=False) + "\n")

    def summary(self):
        records = self.records
        reached = [r for r in records if r["outcome"] == "reached"]
        mean = lambda rows, key: statistics.fmean(r[key] for r in rows) if rows else None
        return {"episodes": len(records),
                "outcomes": {o: sum(r["outcome"] == o for r in records) for o in ("reached", "truncated", "dead_end")},
                "success_rate": len(reached) / len(records) if records else None,
                "mean_return": mean(records, "return"),
                "mean_steps": mean(records, "steps"),
                "illegal_actions": sum(r["illegal_actions"] for r in records),
                "reached_mean_time_ratio": mean(reached, "time_ratio"),
                "reached_mean_exposure_ratio": mean(reached, "exposure_ratio"),
                "reached_mean_weighted_cost": mean(reached, "weighted_cost")}


class RouteEnv:
    """One episode routes one OD pair under one (alpha, beta) preference.

    The graph comes from a versioned RoadRouter and the edge exposure from an
    ExposureSnapshot of the same graph version. The environment holds only the
    OD pairs and the time window of its own split.

    `window_splits` (from split_windows) is required: the snapshot's time
    window must belong to this split. Passing None is an explicit opt-out
    allowed only for a synthetic snapshot (time_window == SYNTHETIC_WINDOW).
    """

    def __init__(self, router: RoadRouter, weights: ExposureSnapshot, od_pairs, config: EnvConfig = EnvConfig(),
                 *, window_splits, logger: EpisodeLogger | None = None):
        if not isinstance(weights, ExposureSnapshot) or weights.graph_version != router.version:
            raise ValueError("Edge weights must be an ExposureSnapshot of the router's graph version")
        if window_splits is None:
            if weights.time_window != SYNTHETIC_WINDOW:
                raise InvalidEpisode("split_violation",
                                     f"window_splits is required for the real time window {weights.time_window}")
        else:
            # A time window belongs to exactly one split; using another split's
            # window would leak its pollution field into this split.
            owners = [name for name in SPLITS if weights.time_window in set(window_splits.get(name, ()))]
            if owners != [config.split]:
                raise InvalidEpisode("split_violation",
                                     f"Time window {weights.time_window} belongs to {owners or 'no split'}, "
                                     f"not only to {config.split}")
        self.router, self.weights, self.config, self.logger = router, weights, config, logger
        self.od_pairs = list(od_pairs)
        if not self.od_pairs:
            raise ValueError("At least one OD pair is required")
        for od in self.od_pairs:
            if od.split != config.split:
                raise InvalidEpisode("split_violation", f"OD pair {od.od_id} belongs to {od.split}, not {config.split}")
            if od.start not in router.arcs or od.end not in router.incoming:
                raise InvalidEpisode("unknown_node", f"OD pair {od.od_id} uses nodes outside this mode's graph")
        self._od_by_id = {od.od_id: od for od in self.od_pairs}

        values = {}
        for edge_id, value in weights.concentration_ug_m3.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("Edge exposure values must be finite and nonnegative")
            if edge_id in router.edges:
                values[edge_id] = float(value)
        if not values:
            raise ValueError("Edge weights cover no edge of this graph")
        # Missing edges get the median of covered edges (as the backend planner
        # does); they are counted, never silently zero.
        self.imputed_value = statistics.median(values.values())
        self.imputed_edges = frozenset(router.edges.keys() - values.keys())
        self.exposure = {e: values.get(e, self.imputed_value) for e in router.edges}

        self.rng = random.Random(config.seed)
        self.episode_index = -1
        self.done = True

    # -- episode lifecycle -------------------------------------------------

    def reset(self, *, seed=None, od_id=None, preference=None):
        if seed is not None:
            self.rng = random.Random(seed)
        if od_id is None:
            od = self.rng.choice(self.od_pairs)
        elif od_id in self._od_by_id:
            od = self._od_by_id[od_id]
        else:
            raise InvalidEpisode("unknown_od", f"OD pair {od_id} is not in this environment's {self.config.split} split")
        alpha, beta = self.config.preference.sample(self.rng) if preference is None else preference
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 <= x <= 1 for x in (alpha, beta)) \
                or abs(alpha + beta - 1) > PREFERENCE_TOLERANCE:
            raise InvalidEpisode("invalid_preference", "alpha and beta must be in [0, 1] and sum to 1")
        if od.start == od.end:
            raise InvalidEpisode("same_node", f"OD pair {od.od_id} starts and ends at the same node")
        path, _, _ = self.router.search(od.start, od.end)
        if path is None:
            raise InvalidEpisode("no_route", f"OD pair {od.od_id} is disconnected in graph {self.router.version}")

        edges = self.router.edges
        self.reference_edge_ids = [edge_id for edge_id, _ in path]
        self.reference_time = math.fsum(edges[e].time_s for e in self.reference_edge_ids)
        self.reference_exposure = math.fsum(self.exposure[e] * edges[e].time_s for e in self.reference_edge_ids)
        # Zero-exposure reference: normalise by 1 (same policy as the mock solver).
        self._exposure_scale = self.reference_exposure or 1.0
        self.step_limit = max(len(self.reference_edge_ids), min(
            self.config.max_steps, math.ceil(self.config.max_steps_factor * len(self.reference_edge_ids))))

        self.episode_index += 1
        self.episode_seed = seed
        self.od, self.alpha, self.beta = od, float(alpha), float(beta)
        self.node, self.visited, self.route = od.start, {od.start}, []
        self.time_s = self.exposure_total = 0.0
        self.steps = self.illegal_actions = self.imputed_traversed = 0
        self.returns = [0.0, 0.0, 0.0]  # scalar, time component, exposure component
        self.done = False
        info = {"od_id": od.od_id, "preference": [self.alpha, self.beta], "step_limit": self.step_limit,
                "reference_time_s": self.reference_time, "reference_exposure": self.reference_exposure,
                "reference_edge_ids": list(self.reference_edge_ids)}
        return self._observation(), info

    def step(self, action):
        if self.done:
            raise RuntimeError("Episode has finished; call reset()")
        self.steps += 1
        arcs, mask = self._arcs()
        # Any integer type is accepted (numpy's np.argmax returns np.int64); bool is not an action.
        legal = (isinstance(action, numbers.Integral) and not isinstance(action, bool)
                 and 0 <= action < len(arcs) and mask[int(action)])
        if legal:
            neighbor, seconds, edge_id, _ = arcs[int(action)]
            exposure = self.exposure[edge_id] * seconds
            vector = [-seconds / self.reference_time, -exposure / self._exposure_scale]
            self.node = neighbor
            self.visited.add(neighbor)
            self.route.append(edge_id)
            self.time_s += seconds
            self.exposure_total += exposure
            self.imputed_traversed += edge_id in self.imputed_edges
        else:
            self.illegal_actions += 1
            vector = [-self.config.illegal_action_penalty] * 2

        outcome = None
        if self.node == self.od.end:
            outcome = "reached"
        elif not any(self._arcs()[1]):
            outcome = "dead_end"
        elif self.steps >= self.step_limit:
            outcome = "truncated"
        if outcome in ("dead_end", "truncated"):
            # Settle the episode at exactly -failure_return per component: this
            # final reward offsets everything accumulated so far, this step included.
            vector = [-self.config.failure_return - self.returns[1], -self.config.failure_return - self.returns[2]]
        # alpha + beta = 1, so the scalar reward is exactly the scalarised vector.
        reward = self.alpha * vector[0] + self.beta * vector[1]
        self.returns = [self.returns[0] + reward, self.returns[1] + vector[0], self.returns[2] + vector[1]]

        info = {"reward_vector": vector, "illegal_action": not legal, "outcome": outcome}
        if outcome:
            self.done = True
            info["episode"] = self._record(outcome)
            if self.logger:
                self.logger.log(info["episode"])
        return self._observation(), reward, outcome in ("reached", "dead_end"), outcome == "truncated", info

    # -- helpers -------------------------------------------------------------

    def _arcs(self):
        arcs = self.router.arcs.get(self.node, [])
        if self.config.allow_revisit:
            return arcs, [True] * len(arcs)
        return arcs, [neighbor not in self.visited for neighbor, *_ in arcs]

    def _observation(self):
        arcs, mask = self._arcs()
        goal = self.router.coords[self.od.end]
        return {
            "node": self.node,
            "destination": self.od.end,
            "preference": [self.alpha, self.beta],
            "time_ratio": self.time_s / self.reference_time,
            "exposure_ratio": self.exposure_total / self._exposure_scale,
            "steps": self.steps,
            "steps_left": self.step_limit - self.steps,
            "distance_to_goal_m": distance_m(self.router.coords[self.node], goal),
            "actions": [{"edge_id": edge_id, "neighbor": neighbor, "forward": forward,
                         "time_ratio": seconds / self.reference_time,
                         "exposure_ratio": self.exposure[edge_id] * seconds / self._exposure_scale,
                         "distance_to_goal_m": distance_m(self.router.coords[neighbor], goal),
                         "visited": neighbor in self.visited}
                        for neighbor, seconds, edge_id, forward in arcs],
            "action_mask": mask,
        }

    def _record(self, outcome):
        time_ratio = self.time_s / self.reference_time
        exposure_ratio = self.exposure_total / self._exposure_scale
        return {
            "episode": self.episode_index, "env_version": ENV_VERSION, "split": self.config.split,
            "env_seed": self.config.seed, "reset_seed": self.episode_seed,
            "graph_version": self.router.version, "mode": self.router.mode,
            "time_window": self.weights.time_window, "weight": self.weights.pollutant,
            "od_id": self.od.od_id, "start": self.od.start, "end": self.od.end,
            "alpha": self.alpha, "beta": self.beta, "outcome": outcome,
            "steps": self.steps, "step_limit": self.step_limit, "illegal_actions": self.illegal_actions,
            "return": self.returns[0], "return_time": self.returns[1], "return_exposure": self.returns[2],
            "travel_time_s": self.time_s, "exposure": self.exposure_total,
            "time_ratio": time_ratio, "exposure_ratio": exposure_ratio,
            "weighted_cost": self.alpha * time_ratio + self.beta * exposure_ratio,
            "route_edges": len(self.route), "imputed_edges_traversed": self.imputed_traversed,
            "reference_time_s": self.reference_time, "reference_exposure": self.reference_exposure,
            "reference_edges": len(self.reference_edge_ids),
        }

    def manifest(self, graph_dir=None):
        """Everything needed to rebuild this environment exactly."""
        graph_files = None
        if graph_dir is not None:
            graph_files = {name: file_sha256(Path(graph_dir) / name) for name in GRAPH_FILES}
        return {
            "env_version": ENV_VERSION,
            "graph_version": self.router.version, "mode": self.router.mode,
            "graph_files_sha256": graph_files,
            "graph_nodes": len(self.router.coords), "graph_edges": len(self.router.edges),
            "weights": {"time_window": self.weights.time_window, "name": self.weights.pollutant,
                        "sha256": weights_fingerprint(self.weights),
                        "covered_edges": len(self.router.edges) - len(self.imputed_edges),
                        "imputed_edges": len(self.imputed_edges), "imputed_value": self.imputed_value},
            "config": asdict(self.config),
            "od_pairs": len(self.od_pairs),
            "od_sha256": hashlib.sha256(json.dumps([asdict(p) for p in self.od_pairs]).encode()).hexdigest(),
        }
