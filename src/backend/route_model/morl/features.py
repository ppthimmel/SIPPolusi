"""What the policy sees at one step: a local ego-graph around the current node.

The Jakarta graph has about 300k nodes, far too many to encode at every step, so
the Graph Encoder works on the nodes within `ego_hops` of the current node. A
sample is plain numpy, built from the router and an edge -> exposure mapping alone:
training reads the environment's state, inference has no environment at all, and
both must produce identical features.

A local view alone cannot tell a through road from a cul-de-sac (26% of the graph's
nodes have a single arc; a purely local policy ended 62% of its validation episodes
in a dead end). So each episode also computes two cost-to-go potentials by reverse
Dijkstra from the destination: the least remaining time and the least remaining
exposure of every node. The arc features then hold each arc's regret against them,
which is 0 on an optimal route for that objective. Each policy learns how much of
each to accept for its own (alpha, beta). A request costs two extra searches, a few
milliseconds per km.

All values are relative to the episode's fastest route (T_ref, E_ref), as the
TI-AI-08 reward is, so that one policy generalises across trip lengths, travel
modes and time windows.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Mapping, NamedTuple

import numpy as np

from ..astar_osm import distance_m

FEATURE_VERSION = "ego-3"
NODE_FEATURES = 9
EDGE_FEATURES = 7
GLOBAL_FEATURES = 5
# Potentials cover nodes up to this many times the origin's own remaining cost; a node
# beyond that is reported as far rather than searched, which bounds the cost per request.
POTENTIAL_HORIZON = 2.0
# Regret features saturate here (in units of the reference route's total).
MAX_REGRET = 1.0
# Below this the road ahead of a typical junction no longer fits in the ego-graph.
MIN_EGO_NODES = 32


@dataclass
class EpisodeContext:
    """Per-episode constants; `distances` memoises geodesic distances to the goal."""
    goal: int
    time_ref: float
    exposure_scale: float
    initial_distance_m: float
    step_limit: int
    time_to_goal: Mapping[int, float] = field(default_factory=dict)
    exposure_to_goal: Mapping[int, float] = field(default_factory=dict)
    distances: dict[int, float] = field(default_factory=dict)

    def __post_init__(self):
        if not (self.time_ref > 0 and self.exposure_scale > 0):
            raise ValueError("T_ref and E_ref must be positive")
        self.initial_distance_m = max(self.initial_distance_m, 1.0)


@lru_cache(maxsize=2)
def reverse_arcs(router):
    """node -> [(predecessor, seconds, edge_id)]; built once per router."""
    reverse = {}
    for u, arcs in router.arcs.items():
        for v, seconds, edge_id, _ in arcs:
            reverse.setdefault(v, []).append((u, seconds, edge_id))
    return reverse


def cost_to_go(router, goal, start, cost_of):
    """Least cost from each node to `goal` (Dijkstra against the arc direction).

    Stops at POTENTIAL_HORIZON times the cost of `start`. Nodes not in the result are
    far from the goal or cannot reach it at all; the features report them as such.
    """
    reverse = reverse_arcs(router)
    best, queue, limit = {goal: 0.0}, [(0.0, goal)], math.inf
    settled = {}
    while queue:
        cost, node = heapq.heappop(queue)
        if node in settled:
            continue
        if cost > limit:
            break
        settled[node] = cost
        if node == start:
            limit = cost * POTENTIAL_HORIZON
        for previous, seconds, edge_id in reverse.get(node, ()):
            candidate = cost + cost_of(seconds, edge_id)
            if candidate < best.get(previous, math.inf):
                best[previous] = candidate
                heapq.heappush(queue, (candidate, previous))
    return settled


def episode_context(router, exposure, start, end, time_ref, exposure_scale, step_limit) -> EpisodeContext:
    """The per-episode constants, the same for training and for inference."""
    return EpisodeContext(
        end, time_ref, exposure_scale, distance_m(router.coords[start], router.coords[end]), step_limit,
        time_to_goal=cost_to_go(router, end, start, lambda seconds, edge_id: seconds),
        exposure_to_goal=cost_to_go(router, end, start, lambda seconds, edge_id: exposure[edge_id] * seconds))


class Sample(NamedTuple):
    node_x: np.ndarray         # [nodes, NODE_FEATURES]; node 0 is the current node
    edge_index: np.ndarray     # [2, edges]: (from, to) ego-node indices of directed arcs
    edge_x: np.ndarray         # [edges, EDGE_FEATURES]
    action_node: np.ndarray    # [actions]: ego-node index each action leads to
    action_edge_x: np.ndarray  # [actions, EDGE_FEATURES]
    action_mask: np.ndarray    # [actions]: bool, legal
    global_x: np.ndarray       # [GLOBAL_FEATURES]


def build_sample(router, exposure: Mapping[int, float], context: EpisodeContext, node: int, visited,
                 time_s: float, exposure_total: float, steps: int, ego_hops: int, ego_max_nodes: int) -> Sample:
    if ego_max_nodes < MIN_EGO_NODES:
        raise ValueError(f"ego_max_nodes must be at least {MIN_EGO_NODES}")
    goal_xy, coords, memo = router.coords[context.goal], router.coords, context.distances

    def to_goal(n):
        value = memo.get(n)
        if value is None:
            value = memo[n] = distance_m(coords[n], goal_xy)
        return value

    reference_index = context.exposure_scale / context.time_ref  # mean exposure index of the fastest route

    def regret(potential, u, v, cost, scale):
        """Extra cost-to-go of taking u->v over u's best arc, in tenths of the reference; saturates when unknown."""
        here, there = potential.get(u), potential.get(v)
        if here is None or there is None:
            return MAX_REGRET * 10
        return min(max(cost + there - here, 0.0) / scale, MAX_REGRET) * 10

    def edge_features(u, v, seconds, edge_id, forward):
        value = exposure[edge_id]
        length = router.edges[edge_id].length_m
        return (min(seconds / context.time_ref * 10, 5.0),
                min(value / reference_index, 5.0),
                min(value * seconds / context.exposure_scale * 10, 5.0),
                float(np.clip((to_goal(u) - to_goal(v)) / max(length, 1.0), -1.5, 1.5)),
                float(forward),
                regret(context.time_to_goal, u, v, seconds, context.time_ref),
                regret(context.exposure_to_goal, u, v, value * seconds, context.exposure_scale))

    # Breadth-first, in the router's sorted arc order, so the sample is deterministic.
    index, hops, order = {node: 0}, [0], [node]
    sources, targets, edge_rows = [], [], []
    position = 0
    while position < len(order):
        u = order[position]
        hop = hops[position]
        position += 1
        if hop >= ego_hops:
            continue
        for v, seconds, edge_id, forward in router.arcs.get(u, ()):
            if v not in index:
                # The cap never hides an action: the current node's own arcs (hop 0) are always in.
                if hop > 0 and len(order) >= ego_max_nodes:
                    continue
                index[v] = len(order)
                order.append(v)
                hops.append(hop + 1)
            sources.append(index[u])
            targets.append(index[v])
            edge_rows.append(edge_features(u, v, seconds, edge_id, forward))

    root_distance = to_goal(node)
    node_x = np.zeros((len(order), NODE_FEATURES), dtype=np.float32)
    for i, n in enumerate(order):
        d = to_goal(n)
        remaining_time, remaining_exposure = context.time_to_goal.get(n), context.exposure_to_goal.get(n)
        node_x[i] = (min(d / context.initial_distance_m, 3.0),
                     np.clip((d - root_distance) / 250.0, -3.0, 3.0),
                     n in visited, n == context.goal, i == 0, hops[i] / max(ego_hops, 1),
                     3.0 if remaining_time is None else min(remaining_time / context.time_ref, 3.0),
                     3.0 if remaining_exposure is None else min(remaining_exposure / context.exposure_scale, 3.0),
                     remaining_time is not None)

    arcs = router.arcs.get(node, ())
    action_rows = [edge_features(node, v, seconds, edge_id, forward) for v, seconds, edge_id, forward in arcs]
    return Sample(
        node_x=node_x,
        edge_index=np.array([sources, targets], dtype=np.int64).reshape(2, -1),
        edge_x=np.array(edge_rows, dtype=np.float32).reshape(-1, EDGE_FEATURES),
        action_node=np.array([index[v] for v, *_ in arcs], dtype=np.int64),
        action_edge_x=np.array(action_rows, dtype=np.float32).reshape(-1, EDGE_FEATURES),
        action_mask=np.array([v not in visited for v, *_ in arcs], dtype=bool),
        global_x=np.array([min(time_s / context.time_ref, 5.0),
                           min(exposure_total / context.exposure_scale, 5.0),
                           steps / context.step_limit,
                           min(root_distance / context.initial_distance_m, 3.0),
                           np.log1p(root_distance) / 10.0], dtype=np.float32),
    )


def collate(samples) -> dict[str, np.ndarray]:
    """Concatenate samples into one disjoint graph; `*_batch` gives each row's sample."""
    node_counts = np.array([len(s.node_x) for s in samples])
    offsets = np.concatenate(([0], np.cumsum(node_counts)[:-1]))
    action_counts = [len(s.action_node) for s in samples]
    return {
        "node_x": np.concatenate([s.node_x for s in samples]),
        "node_batch": np.repeat(np.arange(len(samples)), node_counts),
        "roots": offsets,
        "edge_index": np.concatenate([s.edge_index + o for s, o in zip(samples, offsets)], axis=1),
        "edge_x": np.concatenate([s.edge_x for s in samples]),
        "action_node": np.concatenate([s.action_node + o for s, o in zip(samples, offsets)]),
        "action_edge_x": np.concatenate([s.action_edge_x for s in samples]),
        "action_mask": np.concatenate([s.action_mask for s in samples]),
        "action_batch": np.repeat(np.arange(len(samples)), action_counts),
        "global_x": np.stack([s.global_x for s in samples]),
    }
