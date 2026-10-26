"""Deterministic mock solver for Backend development and contract tests.

Behaves like the production Route Model without any AI service:

- comparison: fastest route (Dijkstra on travel_time_s), like the baseline;
- recommended: Dijkstra on alpha * tau_e / T_ref + beta * I_e * tau_e / E_ref, the
  normalised objective of Subbab 3.3.1 with the fastest route as reference;
  route_source "policy", preference_applied true.

Ties break on node id and edge id, so the same input always gives the same
routes. ``fault`` (ROUTE_SOLVER_FAULT) makes every call fail in one way, for
timeout and failure tests (FAULT_INJECTION, IT-K-04).
"""
from __future__ import annotations

import asyncio
import heapq
from collections.abc import Callable

from planner.models import PlannedEdge, SolverInput

from .contract import SOLVER_ERROR, SolveRequest, SolverError, SolverReply

MODEL_VERSION = "mock-1"


def _arcs(edges: list[PlannedEdge]) -> dict[int, list[tuple[int, int, PlannedEdge]]]:
    arcs: dict[int, list[tuple[int, int, PlannedEdge]]] = {}
    for e in edges:
        if e.u == e.v:
            continue
        arcs.setdefault(e.u, []).append((e.v, e.edge_id, e))
        if e.bidirectional:
            arcs.setdefault(e.v, []).append((e.u, e.edge_id, e))
    for out in arcs.values():
        out.sort(key=lambda a: (a[0], a[1]))
    return arcs


def shortest_path(si: SolverInput, cost: Callable[[PlannedEdge], float]) -> tuple[int, ...] | None:
    """Edge ids of the cheapest origin -> destination path, or None when unreachable."""
    start, goal = si.origin.node_id, si.destination.node_id
    arcs = _arcs(si.edges)
    best = {start: 0.0}
    previous: dict[int, tuple[int, int]] = {}
    queue = [(0.0, start)]
    while queue:
        dist, node = heapq.heappop(queue)
        if dist > best.get(node, float("inf")):
            continue
        if node == goal:
            path = []
            while node != start:
                parent, edge_id = previous[node]
                path.append(edge_id)
                node = parent
            return tuple(reversed(path))
        for nxt, edge_id, edge in arcs.get(node, ()):
            candidate = dist + cost(edge)
            if candidate < best.get(nxt, float("inf")):
                best[nxt] = candidate
                previous[nxt] = (node, edge_id)
                heapq.heappush(queue, (candidate, nxt))
    return None


class MockSolver:
    name = "mock"

    def __init__(self, fault: str = "none"):
        self.fault = fault

    async def solve(self, request: SolveRequest) -> SolverReply:
        if self.fault == "timeout":
            await asyncio.sleep(request.deadline_ms / 1000 + 5)  # outlives deadline_ms + grace
        if self.fault == "error":
            raise SolverError(SOLVER_ERROR, "Mock solver: galat disuntikkan (ROUTE_SOLVER_FAULT=error).")
        if self.fault == "invalid":
            return SolverReply("ok", "policy", True, None, MODEL_VERSION, (-1,), (-1,))

        si = request.solver_input
        fastest = None if self.fault == "no_route" else shortest_path(si, lambda e: e.travel_time_s)
        if fastest is None:
            return SolverReply("no_route", "policy", False, None, MODEL_VERSION)

        by_id = {e.edge_id: e for e in si.edges}
        t_ref = sum(by_id[i].travel_time_s for i in fastest) or 1.0
        e_ref = sum(by_id[i].exposure_index * by_id[i].travel_time_s for i in fastest) or 1.0
        alpha, beta = si.preference.alpha, si.preference.beta
        recommended = shortest_path(
            si, lambda e: alpha * e.travel_time_s / t_ref + beta * e.exposure_index * e.travel_time_s / e_ref)
        return SolverReply("ok", "policy", True, None, MODEL_VERSION, recommended or fastest, fastest)
