"""Check weighted A*, its raw cost functions and the solver contract."""
from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from planner.models import Preference
from route_model.costs import edge_exposure, normalize, route_totals, travel_time, weighted_cost
from route_model.weighted_astar import weighted_astar
from solver.cancel import CancelToken, Cancelled
from solver.config import SolverSettings
from solver.factory import make_solver
from solver.paths import shortest_path
from solver.weighted import WeightedSolver

from .biggrid import grid_input
from .conftest import request_body
from .test_contract import solve


def test_raw_costs_and_zero_reference():
    """Verify the shared numeric functions and the zero-reference policy."""
    assert travel_time(130, 1.3) == pytest.approx(100)
    assert edge_exposure(10, 4) == 40
    assert route_totals([(10, 4), (10, 4)]) == (20, 80)
    assert normalize(4, 0) == 4
    assert weighted_cost(30, 30, 20, 80, .8, .2) == pytest.approx(1.275)
    assert weighted_cost(30, 30, 20, 80, .2, .8) == pytest.approx(.6)
    assert weighted_cost(0, 3, 0, 0, 0, 1) == 3


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True])
def test_missing_or_invalid_exposure_is_rejected(value):
    """Ensure missing and invalid exposure never silently becomes zero."""
    with pytest.raises(ValueError):
        edge_exposure(10, value)
    si = grid_input(2, reachable=True)
    si.edges[0] = replace(si.edges[0], exposure_index=value)
    with pytest.raises(ValueError):
        weighted_astar(si, 20, 20)


def test_invalid_speed_and_preferences():
    """Reject invalid speeds, references and preference weights."""
    with pytest.raises(ValueError):
        travel_time(100, 0)
    for args in [(1, 1, -1, 1, .5, .5), (1, 1, 1, 1, .7, .7), (1, 1, 1, 1, -1, 2)]:
        with pytest.raises(ValueError):
            weighted_cost(*args)


@pytest.mark.parametrize("alpha,expected,cost", [(.8, (1, 3), 1.0), (.2, (2, 4), .6)])
def test_manual_two_route_tradeoff(alpha, expected, cost):
    """Match hand calculations for fast-polluted and slow-clean routes."""
    si = grid_input(2, reachable=True)
    # grid_input order is 1->2, 1->3, 2->4, 3->4.
    si.edges = [replace(e, travel_time_s=10 if i in (0, 2) else 15,
                        exposure_index=4 if i in (0, 2) else 1) for i, e in enumerate(si.edges)]
    si.preference = Preference(alpha, 1 - alpha)
    result = weighted_astar(si, 20, 80)
    assert result.edge_ids == expected
    assert result.cost == pytest.approx(cost)


@pytest.mark.parametrize("alpha", [0, .2, .5, .8, 1])
@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("minimum_index", [0, .5])
def test_astar_matches_dijkstra_on_directed_graphs(seed, alpha, minimum_index):
    """Compare optimal costs across preferences, zero exposure and one-way arcs."""
    rng = random.Random(seed)
    si = grid_input(5, reachable=True)
    si.preference = Preference(alpha, 1 - alpha)
    si.edges = [replace(e, travel_time_s=rng.uniform(1, 100),
                        exposure_index=minimum_index + rng.choice([0, .01, 1, 5]),
                        bidirectional=rng.choice([True, False])) for e in si.edges]
    time_ref, exposure_ref = 100.0, 50.0
    cost = lambda e: weighted_cost(e.travel_time_s, edge_exposure(e.travel_time_s, e.exposure_index),
                                   time_ref, exposure_ref, alpha, 1 - alpha)
    expected = shortest_path(si, cost)
    result = weighted_astar(si, time_ref, exposure_ref)
    assert (result.edge_ids is None) == (expected is None)
    if expected is not None:
        edges = {e.edge_id: e for e in si.edges}
        assert result.cost == pytest.approx(math.fsum(cost(edges[eid]) for eid in expected))


def test_unreachable_same_node_and_cancellation():
    """Handle disconnected graphs, an empty route and cooperative cancellation."""
    si = grid_input(2)
    assert weighted_astar(si, 1, 1).edge_ids is None
    si.destination = si.origin
    assert weighted_astar(si, 0, 0).edge_ids == ()
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        weighted_astar(si, 1, 1, token)


def test_zero_exposure_reference_routes():
    """Keep zero-reference exposure searches finite and choose zero exposure."""
    si = grid_input(2, reachable=True)
    si.preference = Preference(0, 1)
    si.edges = [replace(edge, exposure_index=0 if i in (0, 2) else 2)
                for i, edge in enumerate(si.edges)]
    result = weighted_astar(si, 20, 0)
    assert result.edge_ids == (1, 3)
    assert result.cost == 0


async def test_provider_runs_planner_contract(osm_repo, pollution_repo, settings, solver_settings):
    """Run the weighted provider through planning, validation and route metrics."""
    assert isinstance(make_solver(SolverSettings(provider="weighted", _env_file=None)), WeightedSolver)
    result = await solve(WeightedSolver(), osm_repo, pollution_repo, settings, solver_settings,
                         request_body(alpha=.3, beta=.7))
    assert result.solver == "weighted" and result.model_version == "weighted-astar-1"
    assert result.preference_applied and result.comparison.route_source == "baseline"
    assert result.recommended.edge_ids and any("bukan model MORL" in note for note in result.notes)
