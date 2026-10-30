"""Deterministic weighted-Dijkstra comparator and backend test solver.

Behaves like the production Route Model without any AI service:

- comparison: fastest route (Dijkstra on travel_time_s), like the baseline;
- recommended: Dijkstra on alpha * tau_e / T_ref + beta * I_e * tau_e / E_ref, the
  normalised objective of Subbab 3.3.1 with the fastest route as reference;
  route_source "policy", preference_applied true.

Ties break on node id and edge id, so the same input always gives the same
routes. ``fault`` (ROUTE_SOLVER_FAULT) makes every call fail in one way, for
timeout and failure tests (FAULT_INJECTION, IT-K-04).

Both searches run in a worker thread (solver.cancel), so the event loop stays
free to enforce the call_route_solve timeout, and the search stops at its next
node expansion once that timeout cancels it.
"""
from __future__ import annotations

import asyncio

from planner.models import SolverInput
from route_model.costs import edge_exposure, route_totals, weighted_cost

from .cancel import CancelToken, run_cancellable
from .contract import SOLVER_ERROR, SolveRequest, SolverError, SolverReply
from .paths import shortest_path

# Despite its name, mock is the default deterministic routing comparator for
# MORL experiments. Its search is real; only its fault injection is simulated.
MODEL_VERSION = "mock-1"


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

        if self.fault == "no_route":
            return SolverReply("no_route", "policy", False, None, MODEL_VERSION)
        return await run_cancellable(_routes, request.solver_input)


def _routes(token: CancelToken, si: SolverInput) -> SolverReply:
    fastest = shortest_path(si, lambda e: e.travel_time_s, token)
    if fastest is None:
        return SolverReply("no_route", "policy", False, None, MODEL_VERSION)

    by_id = {e.edge_id: e for e in si.edges}
    t_ref, e_ref = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in fastest)
    alpha, beta = si.preference.alpha, si.preference.beta
    recommended = shortest_path(
        si, lambda e: weighted_cost(e.travel_time_s, edge_exposure(e.travel_time_s, e.exposure_index),
                                   t_ref, e_ref, alpha, beta), token)
    return SolverReply("ok", "policy", True, None, MODEL_VERSION, recommended or fastest, fastest)
