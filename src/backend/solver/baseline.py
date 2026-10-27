"""In-process baseline: the fastest-route A* of route_model (TI-AI-07) on the solver input.

The router is built from the same subgraph, snapped nodes and edge times the
planner produced, as the route_model README asks ("do not independently
resnap"). The baseline has no policy, so both routes are the fastest route,
route_source "baseline", preference_applied false and fallback_reason
"policy_unavailable" until the MORL policy (TI-AI-10) exists.

The work runs in a worker thread (solver.cancel) and stops once the
call_route_solve timeout cancels it. route_model is not changed: the token is
checked through the two dicts RoadRouter reads in its loops, `coords` (one
membership test per edge while the router is built) and `arcs` (one lookup per
node expanded by A*). test_cancel.py fails if route_model stops using them.
"""
from __future__ import annotations

from route_model.astar_osm import Edge, RoadRouter

from .cancel import CHECK_EVERY, CancelToken, run_cancellable
from .contract import SOLVER_ERROR, SolveRequest, SolverError, SolverReply

MODEL_VERSION = "astar-baseline"


class _CheckedCoords(dict):
    """Node coordinates; RoadRouter.__init__ tests membership once per edge end."""

    def __init__(self, data, token: CancelToken):
        super().__init__(data)
        self.token = token

    def __contains__(self, key) -> bool:
        self.token.check()
        return super().__contains__(key)


class _CheckedArcs(dict):
    """Adjacency; RoadRouter.search calls get() once per expanded node."""

    def __init__(self, data, token: CancelToken):
        super().__init__(data)
        self.token = token

    def get(self, key, default=None):
        self.token.check()
        return super().get(key, default)


def _wkt(a: tuple[float, float], b: tuple[float, float]) -> str:
    # RoadRouter validates a LINESTRING per edge; geometry for the response comes
    # from the database later, so the straight segment u -> v is enough here.
    return f"LINESTRING({a[0]!r} {a[1]!r}, {b[0]!r} {b[1]!r})"


def _solve(token: CancelToken, request: SolveRequest) -> SolverReply:
    si = request.solver_input
    coords = {node_id: (p.lon, p.lat) for node_id, p in si.nodes.items()}
    edges = []
    for i, e in enumerate(si.edges):
        if i % CHECK_EVERY == 0:
            token.check()
        edges.append((Edge(e.edge_id, e.u, e.v, e.length_m, e.travel_time_s, _wkt(coords[e.u], coords[e.v])),
                      e.bidirectional))
    token.check()
    try:
        router = RoadRouter(si.source.graph_version, si.mode.value, _CheckedCoords(coords, token), edges)
    except ValueError as exc:
        raise SolverError(SOLVER_ERROR, f"Baseline tidak dapat membangun graf: {exc}") from exc
    router.arcs = _CheckedArcs(router.arcs, token)
    token.check()
    path, _, _ = router.search(si.origin.node_id, si.destination.node_id)
    if path is None:
        return SolverReply("no_route", "baseline", False, "policy_unavailable", MODEL_VERSION)
    edge_ids = tuple(edge_id for edge_id, _ in path)
    return SolverReply("ok", "baseline", False, "policy_unavailable", MODEL_VERSION, edge_ids, edge_ids)


class BaselineSolver:
    name = "baseline"

    async def solve(self, request: SolveRequest) -> SolverReply:
        return await run_cancellable(_solve, request)
