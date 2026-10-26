"""In-process baseline: the fastest-route A* of route_model (TI-AI-07) on the solver input.

The router is built from the same subgraph, snapped nodes and edge times the
planner produced, as the route_model README asks ("do not independently
resnap"). The baseline has no policy, so both routes are the fastest route,
route_source "baseline", preference_applied false and fallback_reason
"policy_unavailable" until the MORL policy (TI-AI-10) exists.
"""
from __future__ import annotations

from starlette.concurrency import run_in_threadpool

from route_model.astar_osm import Edge, RoadRouter

from .contract import SOLVER_ERROR, SolveRequest, SolverError, SolverReply

MODEL_VERSION = "astar-baseline"


def _wkt(a: tuple[float, float], b: tuple[float, float]) -> str:
    # RoadRouter validates a LINESTRING per edge; geometry for the response comes
    # from the database later, so the straight segment u -> v is enough here.
    return f"LINESTRING({a[0]!r} {a[1]!r}, {b[0]!r} {b[1]!r})"


def _solve(request: SolveRequest) -> SolverReply:
    si = request.solver_input
    coords = {node_id: (p.lon, p.lat) for node_id, p in si.nodes.items()}
    edges = [(Edge(e.edge_id, e.u, e.v, e.length_m, e.travel_time_s, _wkt(coords[e.u], coords[e.v])), e.bidirectional)
             for e in si.edges]
    try:
        router = RoadRouter(si.source.graph_version, si.mode.value, coords, edges)
    except ValueError as exc:
        raise SolverError(SOLVER_ERROR, f"Baseline tidak dapat membangun graf: {exc}") from exc
    path, _, _ = router.search(si.origin.node_id, si.destination.node_id)
    if path is None:
        return SolverReply("no_route", "baseline", False, "policy_unavailable", MODEL_VERSION)
    edge_ids = tuple(edge_id for edge_id, _ in path)
    return SolverReply("ok", "baseline", False, "policy_unavailable", MODEL_VERSION, edge_ids, edge_ids)


class BaselineSolver:
    name = "baseline"

    async def solve(self, request: SolveRequest) -> SolverReply:
        # A* is synchronous CPU work; a timeout stops waiting but not the thread.
        return await run_in_threadpool(_solve, request)
