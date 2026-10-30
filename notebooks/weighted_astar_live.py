"""Read representative planner inputs and benchmark weighted searches locally.

The application database pool sets every session to read-only. A read-only DB
role is still recommended: the session setting is not an access-control boundary.
No connection string or graph data is printed or persisted by this module.
"""
from __future__ import annotations

import asyncio
import heapq
import math
import statistics
import time
from pathlib import Path

from osm.config import OsmSettings
from osm.db import OsmDatabase
from osm.repository import OsmRepository
from planner.assembler import build_solver_input
from planner.models import Preference
from pollution.repository import PollutionRepository
from route_model.astar_osm import distance_m
from route_model.costs import edge_exposure, route_totals, weighted_cost
from route_model.weighted_astar import weighted_astar
from solver.paths import shortest_path

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = (
    ("central-short-walk", (106.8272, -6.1754), (106.8230, -6.1950), "walk"),
    ("central-long-walk", (106.8227, -6.1754), (106.8456, -6.2088), "walk"),
    ("central-long-bike", (106.8227, -6.1754), (106.8456, -6.2088), "bike"),
)


def _milliseconds(started: float) -> float:
    """Return the elapsed monotonic time in milliseconds."""
    return (time.perf_counter() - started) * 1000


def _cost_of_path(si, path, time_ref, exposure_ref):
    """Compute the weighted cost of an edge-ID path using shared formulas."""
    by_id = {edge.edge_id: edge for edge in si.edges}
    seconds, exposure = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in path)
    return weighted_cost(seconds, exposure, time_ref, exposure_ref, si.preference.alpha, si.preference.beta)


def _prepare_graph(si, time_ref, exposure_ref):
    """Validate and prepare one weighted adjacency shared by both algorithms."""
    coords = {node: (p.lon, p.lat) for node, p in si.nodes.items()}
    if si.origin.node_id not in coords or si.destination.node_id not in coords:
        raise ValueError("Missing origin or destination")
    if any(not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90)
           for lon, lat in coords.values()):
        raise ValueError("Invalid node coordinates")
    arcs, seen = {}, set()
    for edge in si.edges:
        if edge.edge_id in seen or edge.u not in coords or edge.v not in coords:
            raise ValueError("Duplicate edge or missing endpoint")
        seen.add(edge.edge_id)
        if not math.isfinite(edge.travel_time_s) or edge.travel_time_s <= 0:
            raise ValueError("Invalid travel time")
        cost = weighted_cost(edge.travel_time_s, edge_exposure(edge.travel_time_s, edge.exposure_index),
                             time_ref, exposure_ref, si.preference.alpha, si.preference.beta)
        if not math.isfinite(cost):
            raise ValueError("Invalid weighted cost")
        if edge.u == edge.v:
            continue
        arcs.setdefault(edge.u, []).append((edge.v, edge.edge_id, cost))
        if edge.bidirectional:
            arcs.setdefault(edge.v, []).append((edge.u, edge.edge_id, cost))
    for outgoing in arcs.values():
        outgoing.sort()
    return coords, arcs


def _heuristic_factor(si, coords, time_ref, exposure_ref):
    """Calculate the A* distance-to-weighted-cost lower-bound factor."""
    scale, minimum_index = math.inf, math.inf
    for edge in si.edges:
        if edge.u == edge.v:
            continue
        minimum_index = min(minimum_index, edge.exposure_index)
        straight = distance_m(coords[edge.u], coords[edge.v])
        if straight:
            scale = min(scale, edge.travel_time_s / straight)
    scale = scale if math.isfinite(scale) else 0.0
    minimum_index = minimum_index if math.isfinite(minimum_index) else 0.0
    return scale * weighted_cost(1.0, minimum_index, time_ref, exposure_ref,
                                 si.preference.alpha, si.preference.beta)


def _search(arcs, coords, start, goal, heuristic_factor):
    """Find a minimum-cost path; a zero heuristic factor gives Dijkstra."""
    def heuristic(node):
        """Estimate remaining weighted cost using the provided lower bound."""
        return heuristic_factor * distance_m(coords[node], coords[goal]) if heuristic_factor else 0.0

    best, previous, queue = {start: 0.0}, {}, [(heuristic(start), 0.0, start)]
    expanded = 0
    while queue:
        _, cost, node = heapq.heappop(queue)
        if cost > best.get(node, math.inf):
            continue
        expanded += 1
        if node == goal:
            path = []
            while node != start:
                node, edge_id = previous[node]
                path.append(edge_id)
            return tuple(reversed(path)), cost, expanded
        for neighbor, edge_id, edge_cost in arcs.get(node, ()):
            candidate = cost + edge_cost
            if candidate < best.get(neighbor, math.inf):
                best[neighbor], previous[neighbor] = candidate, (node, edge_id)
                heapq.heappush(queue, (candidate + heuristic(neighbor), candidate, neighbor))
    return None, None, expanded


def _path_totals(si, path):
    """Return route time and exposure from the planner's edge weights."""
    by_id = {edge.edge_id: edge for edge in si.edges}
    return route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in path)


def _run_searches(si, repeats):
    """Compare identical search code with a weighted A* heuristic and with zero."""
    started = time.perf_counter()
    fastest = shortest_path(si, lambda edge: edge.travel_time_s)
    if fastest is None:
        raise ValueError("Planner input has no fastest path")
    by_id = {edge.edge_id: edge for edge in si.edges}
    time_ref, exposure_ref = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in fastest)
    reference_ms = _milliseconds(started)
    prepare_times, heuristic_times = [], []
    for _ in range(repeats):
        started = time.perf_counter()
        coords, arcs = _prepare_graph(si, time_ref, exposure_ref)
        prepare_times.append(_milliseconds(started))
        started = time.perf_counter()
        factor = _heuristic_factor(si, coords, time_ref, exposure_ref)
        heuristic_times.append(_milliseconds(started))
    start, goal = si.origin.node_id, si.destination.node_id
    # Both searches use the same prepared edge costs, adjacency and code.
    _search(arcs, coords, start, goal, factor)
    _search(arcs, coords, start, goal, 0.0)
    astar_times, dijkstra_times = [], []
    for i in range(repeats):
        # Alternate order to limit systematic warm-up and scheduling bias.
        for name in (("astar", "dijkstra") if i % 2 == 0 else ("dijkstra", "astar")):
            started = time.perf_counter()
            if name == "astar":
                astar_path, astar_cost, astar_expanded = _search(arcs, coords, start, goal, factor)
                astar_times.append(_milliseconds(started))
            else:
                path, dijkstra_cost, dijkstra_expanded = _search(arcs, coords, start, goal, 0.0)
                dijkstra_times.append(_milliseconds(started))
    if path is None or astar_path is None:
        raise ValueError("A reachable graph produced no weighted route")
    reference_cost = _cost_of_path(si, path, time_ref, exposure_ref)
    research_astar = weighted_astar(si, time_ref, exposure_ref)
    if not all(math.isclose(cost, reference_cost, rel_tol=1e-9, abs_tol=1e-11)
               for cost in (astar_cost, dijkstra_cost, research_astar.cost)):
        raise AssertionError("Weighted A* and Dijkstra returned different minimum costs")
    astar_time, astar_exposure = _path_totals(si, astar_path)
    dijkstra_time, dijkstra_exposure = _path_totals(si, path)
    return {"reference_ms": round(reference_ms, 2),
            "shared_prepare_ms": round(statistics.median(prepare_times), 2),
            "astar_heuristic_setup_ms": round(statistics.median(heuristic_times), 2),
            "astar_search_ms": round(statistics.median(astar_times), 2),
            "dijkstra_search_ms": round(statistics.median(dijkstra_times), 2),
            "astar_expanded": astar_expanded, "dijkstra_expanded": dijkstra_expanded,
            "astar_time_s": round(astar_time, 3), "dijkstra_time_s": round(dijkstra_time, 3),
            "astar_exposure_index_s": round(astar_exposure, 4),
            "dijkstra_exposure_index_s": round(dijkstra_exposure, 4),
            "same_edge_path": astar_path == path, "cost": round(astar_cost, 8),
            "t_ref_s": time_ref, "e_ref": exposure_ref}


async def benchmark_live(repeats=3, scenarios=SCENARIOS):
    """Read planner subgraphs and return local benchmark results without credentials.

    The backend settings read DATABASE_URL from its .env or the environment.
    Queries use the application's read-only pool; results use the latest complete
    pollution window and report its timestamp and missing-data count.
    """
    if repeats < 1:
        raise ValueError("repeats must be positive")
    # Longer query timeouts allow exploratory reads over the public connection;
    # planner_ms therefore does not measure production timeout behavior.
    settings = OsmSettings(_env_file=ROOT / "src/backend/.env", pool_min_size=1, pool_max_size=2,
                           statement_timeout_ms=15000, command_timeout_s=30, connect_timeout_s=15)
    db = OsmDatabase(settings)
    osm = OsmRepository(db)
    pollution = PollutionRepository(db, osm)
    rows = []
    try:
        async with db.connection() as conn:
            if await conn.fetchval("SHOW transaction_read_only") != "on":
                raise RuntimeError("Database session is not read-only")
        for label, origin, destination, mode in scenarios:
            body = {"origin": {"lon": origin[0], "lat": origin[1]},
                    "destination": {"lon": destination[0], "lat": destination[1]},
                    "mode": mode, "pollutant": "pm25", "preference": {"alpha": .5, "beta": .5}}
            started = time.perf_counter()
            si = await build_solver_input(body, osm=osm, pollution=pollution, settings=settings,
                                          request_id="weighted-benchmark")
            planner_ms = _milliseconds(started)
            for alpha in (0.0, .5, 1.0):
                si.preference = Preference(alpha, 1 - alpha)
                search = _run_searches(si, repeats)
                rows.append({"scenario": label, "alpha": alpha, "nodes": len(si.nodes), "edges": len(si.edges),
                             "graph_version": si.source.graph_version,
                             "window_utc": si.weights.time_window_start.isoformat(),
                             "data_stale": si.weights.data_stale,
                             "imputed_edges": si.weights.missing_edge_count,
                             "planner_ms": round(planner_ms, 2), **search})
    finally:
        await db.close()
    return rows


if __name__ == "__main__":
    for row in asyncio.run(benchmark_live()):
        print(row)
