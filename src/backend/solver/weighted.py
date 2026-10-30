"""Supported deterministic weighted-A* provider for the planner graph contract."""
from __future__ import annotations

from route_model.costs import route_totals
from route_model.weighted_astar import weighted_astar

from .baseline import _solve as solve_baseline
from .cancel import run_cancellable
from .contract import SOLVER_ERROR, SolverError, SolverReply

MODEL_VERSION = "weighted-astar-1"


def _solve(token, request):
    """Calculate a time-only baseline and a preference-weighted A* route."""
    baseline = solve_baseline(token, request)
    if baseline.status != "ok":
        return SolverReply("no_route", "policy", False, None, MODEL_VERSION)
    si = request.solver_input
    edges = {edge.edge_id: edge for edge in si.edges}
    try:
        time_ref, exposure_ref = route_totals(
            (edges[eid].travel_time_s, edges[eid].exposure_index) for eid in baseline.comparison)
        result = weighted_astar(si, time_ref, exposure_ref, token)
    except (ValueError, TypeError, OverflowError) as exc:
        raise SolverError(SOLVER_ERROR, f"Invalid weighted route input: {exc}") from exc
    if result.edge_ids is None:
        return SolverReply("no_route", "policy", False, None, MODEL_VERSION)
    return SolverReply("ok", "policy", True, None, MODEL_VERSION, result.edge_ids, baseline.comparison)


class WeightedSolver:
    """Run deterministic weighted A* in the cancellable solver worker."""

    name = "weighted"

    async def solve(self, request):
        """Return weighted and fastest routes through the standard solver contract."""
        return await run_cancellable(_solve, request)
