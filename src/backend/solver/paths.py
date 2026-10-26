"""Dijkstra on the solver input, used by the mock solver and by the orchestrator's
check that a route labelled baseline is a fastest route (TI-SE-07 QA).

Ties break on node id and edge id, so the same input always gives the same path.
The search checks a CancelToken once per expansion (solver.cancel).
"""
from __future__ import annotations

import heapq
from collections.abc import Callable

from planner.models import PlannedEdge, SolverInput

from .cancel import CancelToken


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


def shortest_path(si: SolverInput, cost: Callable[[PlannedEdge], float],
                  token: CancelToken | None = None) -> tuple[int, ...] | None:
    """Edge ids of the cheapest origin -> destination path, or None when unreachable.
    Raises Cancelled at the next expansion once token is cancelled."""
    start, goal = si.origin.node_id, si.destination.node_id
    arcs = _arcs(si.edges)
    best = {start: 0.0}
    previous: dict[int, tuple[int, int]] = {}
    queue = [(0.0, start)]
    while queue:
        if token is not None:
            token.check()
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


def fastest_time_s(token: CancelToken, si: SolverInput) -> float | None:
    """Travel time in seconds of a fastest origin -> destination path, None when unreachable."""
    path = shortest_path(si, lambda e: e.travel_time_s, token)
    if path is None:
        return None
    by_id = {e.edge_id: e for e in si.edges}
    return sum(by_id[i].travel_time_s for i in path)
