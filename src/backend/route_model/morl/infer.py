"""Checkpoint format, and greedy route inference from a policy set (no environment needed).

A request's preference selects the member with the nearest alpha. The route is decoded
node by node with visited arcs masked, as in training and as design doc Tabel 3.8
specifies. A route that cannot reach the destination, or that runs out of steps or
time, raises PolicyFailure: the caller's fallback is the A* route. Inference draws no
random numbers, so equal inputs give equal routes.
"""
from __future__ import annotations

import heapq
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import torch

from .features import FEATURE_VERSION, build_sample, collate, episode_context
from .policy import ActorCritic, ModelConfig, sample_actions, to_tensors
from .resources import replace_file

CHECKPOINT_FORMAT = "morl-checkpoint-1"


class PolicyFailure(RuntimeError):
    """The policy produced no valid route; `reason` is dead_end, step_limit or timeout."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class PolicyRoute:
    edge_ids: tuple[int, ...]
    nodes: tuple[int, ...]
    time_s: float
    exposure: float


def save_checkpoint(path, model: ActorCritic, **metadata):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    # Written in full before it replaces the old file: a killed run never leaves a torn checkpoint.
    torch.save({"format": CHECKPOINT_FORMAT, "feature_version": FEATURE_VERSION,
                "model_config": model.config.as_dict(), "model_state": model.state_dict(), **metadata}, temporary)
    replace_file(temporary, path)


def load_policy(path, device="cpu") -> tuple[ActorCritic, dict]:
    """Rebuild the network from a checkpoint alone; returns (model in eval mode, checkpoint)."""
    # weights_only refuses arbitrary pickled objects: a checkpoint is data, not code.
    checkpoint = torch.load(path, map_location=device, weights_only=True)
    if checkpoint.get("format") != CHECKPOINT_FORMAT:
        raise ValueError(f"Unknown checkpoint format: {checkpoint.get('format')!r}")
    if checkpoint.get("feature_version") != FEATURE_VERSION:
        raise ValueError(f"Checkpoint expects features {checkpoint.get('feature_version')!r}, "
                         f"this code builds {FEATURE_VERSION!r}")
    model = ActorCritic(ModelConfig(**checkpoint["model_config"])).to(device)
    model.load_state_dict(checkpoint["model_state"])
    return model.eval(), checkpoint


def fastest_reference(router, exposure, start, end):
    """(edge_ids, T_ref, E_ref) of the A* route, the reference of the objective."""
    path, _, _ = router.search(start, end)
    if path is None:
        raise PolicyFailure("no_route", "Destination is unreachable")
    edge_ids = [edge_id for edge_id, _ in path]
    seconds = math.fsum(router.edges[e].time_s for e in edge_ids)
    return edge_ids, seconds, math.fsum(exposure[e] * router.edges[e].time_s for e in edge_ids)


def optimal_weighted_route(router, exposure, start, end, alpha, beta, time_ref, exposure_ref):
    """Exact minimum of alpha*T/T_ref + beta*E/E_ref (Dijkstra): the bound a policy is measured against.

    Returns (edge_ids, weighted cost), or (None, None) when unreachable.
    """
    scale = exposure_ref or 1.0
    queue, best, previous = [(0.0, start)], {start: 0.0}, {}
    while queue:
        cost, node = heapq.heappop(queue)
        if cost > best[node]:
            continue
        if node == end:
            edges = []
            while node != start:
                node, edge_id = previous[node]
                edges.append(edge_id)
            return edges[::-1], cost
        for neighbor, seconds, edge_id, _ in router.arcs.get(node, ()):
            step = alpha * seconds / time_ref + beta * exposure[edge_id] * seconds / scale
            if cost + step < best.get(neighbor, math.inf):
                best[neighbor] = cost + step
                previous[neighbor] = (node, edge_id)
                heapq.heappush(queue, (cost + step, neighbor))
    return None, None


def nearest_alpha(alphas, alpha) -> float:
    """The member serving a preference: the nearest alpha; on a tie the lower one, which weighs exposure more."""
    return min(alphas, key=lambda member: (round(abs(member - alpha), 9), member))


def load_policy_set(set_dir, device="cpu") -> dict[float, ActorCritic]:
    """alpha -> model, from each member's selected checkpoint named in set_manifest.json."""
    set_dir = Path(set_dir)
    manifest = json.loads((set_dir / "set_manifest.json").read_text(encoding="utf-8"))
    missing = [m["alpha"] for m in manifest["members"] if not (m.get("run_dir") and m.get("best"))]
    if missing:
        raise ValueError(f"Policy set {set_dir} has no selected checkpoint for alpha {missing}")
    return {m["alpha"]: load_policy(set_dir / m["run_dir"] / m["best"]["checkpoint"], device)[0]
            for m in manifest["members"]}


def decode_for_preference(policy_set, router, exposure, start, end, preference, **kwargs) -> tuple[float, PolicyRoute]:
    """(alpha of the member used, its route) for a requested (alpha, beta)."""
    member = nearest_alpha(policy_set, preference[0])
    return member, decode_route(policy_set[member], router, exposure, start, end, **kwargs)


@torch.inference_mode()
def decode_route(model: ActorCritic, router, exposure, start, end, *, step_limit=None,
                 max_steps=1000, max_steps_factor=3.0, timeout_ms=None, device="cpu") -> PolicyRoute:
    """One member's greedy route from start to end; raises PolicyFailure."""
    started = time.perf_counter()
    if start == end:
        raise PolicyFailure("same_node", "Origin and destination are the same node")
    edge_ids, time_ref, exposure_ref = fastest_reference(router, exposure, start, end)
    if step_limit is None:
        step_limit = max(len(edge_ids), min(max_steps, math.ceil(max_steps_factor * len(edge_ids))))
    context = episode_context(router, exposure, start, end, time_ref, exposure_ref or 1.0, step_limit)
    node, visited, route, nodes = start, {start}, [], [start]
    seconds_total = exposure_total = 0.0
    config = model.config
    for steps in range(step_limit):
        if timeout_ms is not None and (time.perf_counter() - started) * 1000 > timeout_ms:
            raise PolicyFailure("timeout", f"Inference exceeded {timeout_ms} ms")
        sample = build_sample(router, exposure, context, node, visited, seconds_total, exposure_total, steps,
                              config.ego_hops, config.ego_max_nodes)
        if not sample.action_mask.any():
            raise PolicyFailure("dead_end", f"No legal arc at node {node} after {steps} steps")
        batch = to_tensors(collate([sample]), device)
        log_probs, _ = model(batch)
        action = int(sample_actions(log_probs, batch["action_batch"], 1, greedy=True)[0][0])
        neighbor, seconds, edge_id, _ = router.arcs[node][action]
        node = neighbor
        visited.add(node)
        route.append(edge_id)
        nodes.append(node)
        seconds_total += seconds
        exposure_total += exposure[edge_id] * seconds
        if node == end:
            return PolicyRoute(tuple(route), tuple(nodes), seconds_total, exposure_total)
    raise PolicyFailure("step_limit", f"Destination not reached within {step_limit} steps")
