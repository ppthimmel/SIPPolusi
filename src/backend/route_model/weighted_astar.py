"""Weighted A* on a planner SolverInput, independent of the solver provider."""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .astar_osm import distance_m
from .costs import edge_exposure, weighted_cost


@dataclass(frozen=True, slots=True)
class WeightedSearchResult:
    """An optimal edge path, its weighted cost and number of node expansions."""

    edge_ids: tuple[int, ...] | None
    cost: float | None
    expanded_nodes: int


def weighted_astar(si, time_ref: float, exposure_ref: float, token=None) -> WeightedSearchResult:
    """Find the minimum weighted route using conservative time and exposure bounds.

    The planner supplies directed edges, coordinates, nonnegative exposure
    indices and preference weights. Missing values are rejected rather than
    interpreted as zero. A cancellation token may provide a check() method.
    """
    alpha, beta = si.preference.alpha, si.preference.beta
    # Validate references and preference even for an empty or unreachable graph.
    weighted_cost(0, 0, time_ref, exposure_ref, alpha, beta)
    start, goal = si.origin.node_id, si.destination.node_id
    if start not in si.nodes or goal not in si.nodes:
        raise ValueError("origin and destination must be in the graph")
    coords = {node: (point.lon, point.lat) for node, point in si.nodes.items()}
    for lon, lat in coords.values():
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("node coordinates must be finite longitude and latitude")

    arcs, seen = {}, set()
    time_per_m, minimum_index = math.inf, math.inf
    for i, edge in enumerate(si.edges):
        if token is not None and i % 1024 == 0:
            token.check()
        if edge.edge_id in seen or edge.u not in coords or edge.v not in coords:
            raise ValueError("duplicate edge or missing endpoint")
        seen.add(edge.edge_id)
        if not math.isfinite(edge.travel_time_s) or edge.travel_time_s <= 0:
            raise ValueError("edge travel time must be positive and finite")
        exposure = edge_exposure(edge.travel_time_s, edge.exposure_index)
        cost = weighted_cost(edge.travel_time_s, exposure, time_ref, exposure_ref, alpha, beta)
        if not math.isfinite(cost):
            raise ValueError("edge cost must be finite")
        if edge.u == edge.v:
            continue
        minimum_index = min(minimum_index, edge.exposure_index)
        straight = distance_m(coords[edge.u], coords[edge.v])
        if straight > 0:
            time_per_m = min(time_per_m, edge.travel_time_s / straight)
        arcs.setdefault(edge.u, []).append((edge.v, edge.edge_id, cost))
        if edge.bidirectional:
            arcs.setdefault(edge.v, []).append((edge.u, edge.edge_id, cost))
    for outgoing in arcs.values():
        outgoing.sort()
    scale = time_per_m if math.isfinite(time_per_m) else 0.0
    minimum_index = minimum_index if math.isfinite(minimum_index) else 0.0

    def heuristic(node):
        """Return a lower bound on the remaining normalized weighted cost."""
        minimum_time = scale * distance_m(coords[node], coords[goal])
        return weighted_cost(minimum_time, minimum_index * minimum_time,
                             time_ref, exposure_ref, alpha, beta)

    best, previous = {start: 0.0}, {}
    queue = [(heuristic(start), 0.0, start)]
    expanded = 0
    while queue:
        if token is not None:
            token.check()
        _, cost, node = heapq.heappop(queue)
        if cost > best[node]:
            continue
        expanded += 1
        if node == goal:
            path = []
            while node != start:
                node, edge_id = previous[node]
                path.append(edge_id)
            return WeightedSearchResult(tuple(reversed(path)), cost, expanded)
        for neighbor, edge_id, edge_cost in arcs.get(node, ()):
            candidate = cost + edge_cost
            if candidate < best.get(neighbor, math.inf):
                best[neighbor], previous[neighbor] = candidate, (node, edge_id)
                heapq.heappush(queue, (candidate + heuristic(neighbor), candidate, neighbor))
    return WeightedSearchResult(None, None, expanded)
