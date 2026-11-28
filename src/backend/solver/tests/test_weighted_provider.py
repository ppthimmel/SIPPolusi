"""The weighted A* provider (ROUTE_SOLVER_PROVIDER=weighted): same objective and references as the
MORL policy, preference-conditioned like it, and cancellable like the other in-process solvers."""
from __future__ import annotations

import math
import random
import time
from dataclasses import replace

import pytest

from planner.models import Preference
from public_api.schemas import RouteModelInfo
from route_model.costs import edge_exposure, route_totals, weighted_cost
from solver.cancel import CancelToken
from solver.contract import SOLVER_ERROR, SOLVER_TIMEOUT, RouteServiceUnavailable, SolveRequest, SolverError
from solver.mock import _routes as mock_routes
from solver.orchestrator import call_route_solve
from solver.paths import shortest_path
from solver.weighted import MODEL_VERSION, WeightedSolver, _solve

from .biggrid import grid_input
from .conftest import request_body
from .test_contract import solve


def _two_routes(alpha: float):
    """grid_input order is 1->2, 1->3, 2->4, 3->4: a fast polluted route (1, 3) and a slow clean one (2, 4)."""
    si = grid_input(2, reachable=True)
    si.edges = [replace(e, travel_time_s=10 if i in (0, 2) else 15, exposure_index=4 if i in (0, 2) else 1)
                for i, e in enumerate(si.edges)]
    si.preference = Preference(alpha, 1 - alpha)
    return si


@pytest.mark.parametrize("alpha, expected", [(1.0, (1, 3)), (0.8, (1, 3)), (0.2, (2, 4)), (0.0, (2, 4))])
def test_recommendation_follows_the_preference(alpha, expected):
    reply = _solve(CancelToken(), _two_routes(alpha))
    assert reply.status == "ok" and reply.comparison == (1, 3)  # fastest
    assert reply.recommended == expected
    assert (reply.route_source, reply.preference_applied, reply.fallback_reason) == ("policy", True, None)
    assert reply.model_version == MODEL_VERSION


@pytest.mark.parametrize("alpha", [0, .2, .5, .8, 1])
@pytest.mark.parametrize("seed", range(6))
def test_minimises_the_morl_objective_against_the_fastest_route(seed, alpha):
    """Optimal for alpha*T/T_ref + beta*E/E_ref with T_ref, E_ref of the fastest route (fastest_reference)."""
    rng = random.Random(seed)
    si = grid_input(5, reachable=True)
    si.preference = Preference(alpha, 1 - alpha)
    si.edges = [replace(e, travel_time_s=rng.uniform(1, 100), exposure_index=rng.choice([0, .01, 1, 5]),
                        bidirectional=rng.choice([True, False])) for e in si.edges]
    by_id = {e.edge_id: e for e in si.edges}
    fastest = shortest_path(si, lambda e: e.travel_time_s)
    if fastest is None:
        assert _solve(CancelToken(), si).status == "no_route"
        return
    time_ref, exposure_ref = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in fastest)
    cost = lambda e: weighted_cost(e.travel_time_s, edge_exposure(e.travel_time_s, e.exposure_index),
                                   time_ref, exposure_ref, alpha, 1 - alpha)
    optimum = math.fsum(cost(by_id[i]) for i in shortest_path(si, cost))
    reply = _solve(CancelToken(), si)
    assert reply.comparison == fastest
    assert math.fsum(cost(by_id[i]) for i in reply.recommended) == pytest.approx(optimum)
    # The mock comparator optimises the same objective: equal cost (ties may pick another path).
    assert math.fsum(cost(by_id[i]) for i in mock_routes(CancelToken(), si).recommended) == pytest.approx(optimum)


def test_unreachable_destination_is_no_route():
    reply = _solve(CancelToken(), grid_input(2))
    assert reply.status == "no_route" and reply.recommended == ()


def test_invalid_exposure_is_a_solver_error():
    si = _two_routes(.5)
    si.edges[0] = replace(si.edges[0], exposure_index=float("nan"))
    with pytest.raises(SolverError) as exc:
        _solve(CancelToken(), si)
    assert exc.value.reason == SOLVER_ERROR


def test_details_fit_metadata_route_model():
    details = _solve(CancelToken(), _two_routes(.5)).details
    info = RouteModelInfo(**details)  # strict schema: no unknown or missing fields
    assert (info.name, info.version, info.policy_family, info.status) == \
        ("weighted-astar", MODEL_VERSION, "weighted_astar", "ready")
    assert info.inference_ms is not None and info.inference_ms >= 0


async def test_provider_runs_planner_contract(osm_repo, pollution_repo, settings, solver_settings):
    result = await solve(WeightedSolver(), osm_repo, pollution_repo, settings, solver_settings,
                         request_body(alpha=.3, beta=.7))
    assert result.solver == "weighted" and result.model_version == MODEL_VERSION
    assert result.route_source == "policy" and result.preference_applied and result.fallback_reason is None
    assert result.comparison.route_source == "baseline"
    assert result.route_model["name"] == "weighted-astar"


async def test_stops_after_the_timeout():
    big = grid_input(250, reachable=True)
    started = time.perf_counter()
    with pytest.raises(RouteServiceUnavailable) as exc:
        await call_route_solve(WeightedSolver(), SolveRequest(big, deadline_ms=50), grace_ms=0)
    assert exc.value.reason == SOLVER_TIMEOUT
    assert time.perf_counter() - started < 0.5
