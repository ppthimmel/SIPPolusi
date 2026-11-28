"""Weighted A* provider (ROUTE_SOLVER_PROVIDER=weighted): the deterministic comparator for MORL.

Evaluation configuration C0 of the design document (Subbab 5.5) needs a graph search that
optimises the same objective as the MORL policy, so a difference between the two comes from
the policy and not from a different goal:

- comparison: fastest route (Dijkstra on travel_time_s), as for every provider (PF-09);
- recommended: route_model.weighted_astar on alpha * T/T_ref + beta * E/E_ref, with T_ref and
  E_ref the travel time and exposure of that fastest route: the references the MORL policy
  uses (route_model.morl.infer.fastest_reference) and the mock comparator uses.

The preference comes from the request, so the route follows the "Prioritas perjalanan"
slider like the policy does. route_source is "policy" with preference_applied true: the
recommendation is preference-conditioned, not the fastest-route fallback, so the response
and the interface treat it like the MORL recommendation. model_version "weighted-astar-1"
and metadata.route_model tell the two apart.

Both searches run in a worker thread (solver.cancel) and stop at their next expansion once
the call_route_solve timeout cancels the token.
"""
from __future__ import annotations

import time

from planner.models import SolverInput
from route_model.costs import route_totals
from route_model.weighted_astar import weighted_astar

from .cancel import CancelToken, run_cancellable
from .contract import SOLVER_ERROR, SOLVER_NO_ROUTE, SolveRequest, SolverError, SolverReply
from .paths import shortest_path

NAME = "weighted-astar"
MODEL_VERSION = "weighted-astar-1"


class WeightedSolver:
    name = "weighted"

    async def solve(self, request: SolveRequest) -> SolverReply:
        return await run_cancellable(_solve, request.solver_input)


def _solve(token: CancelToken, si: SolverInput) -> SolverReply:
    by_id = {edge.edge_id: edge for edge in si.edges}
    fastest = shortest_path(si, lambda e: e.travel_time_s, token)
    if fastest is None:
        return SolverReply("no_route", "policy", False, None, MODEL_VERSION)
    try:
        # Rejects a missing or invalid exposure_index instead of reading it as zero.
        time_ref, exposure_ref = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in fastest)
        started = time.perf_counter()
        result = weighted_astar(si, time_ref, exposure_ref, token)
    except ValueError as exc:
        raise SolverError(SOLVER_ERROR, f"Weighted A* tidak dapat dijalankan: {exc}") from exc
    if result.edge_ids is None:
        # The fastest route exists, so the same graph cannot be unreachable for the weighted search.
        raise SolverError(SOLVER_NO_ROUTE, "Weighted A* tidak menemukan rute padahal rute tercepat ada")
    details = {
        "name": NAME, "version": MODEL_VERSION, "package_sha256": None, "package_version": None,
        "policy_family": "weighted_astar", "status": "ready", "member_alpha": None,
        "inference_ms": round((time.perf_counter() - started) * 1000, 1), "compatibility": None,
    }
    return SolverReply("ok", "policy", True, None, MODEL_VERSION, result.edge_ids, fastest, details)
