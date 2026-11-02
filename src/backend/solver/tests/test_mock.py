"""The mock solver: deterministic, preference-aware, fault injection."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
import random
from types import SimpleNamespace

import pytest

from solver.cancel import CancelToken
from solver.mock import MockSolver, _routes
from solver.paths import shortest_path
from solver.orchestrator import solve_route_request
from route_model.costs import edge_exposure, route_totals, weighted_cost
from planner.models import Preference

from .conftest import FakePollutionRepository, node_id, request_body, weight
from .biggrid import grid_input


async def solve(solver, osm_repo, pollution_repo, settings, solver_settings, body=None):
    return await solve_route_request(
        body or request_body(), osm=osm_repo, pollution=pollution_repo, osm_settings=settings, solver=solver,
        solver_settings=solver_settings, request_id="r", received_at=datetime.now(timezone.utc))


async def test_same_input_same_routes(osm_repo, pollution_repo, settings, solver_settings):
    a = await solve(MockSolver(), osm_repo, pollution_repo, settings, solver_settings)
    b = await solve(MockSolver(), osm_repo, pollution_repo, settings, solver_settings)
    assert a.recommended.edge_ids == b.recommended.edge_ids and a.comparison.edge_ids == b.comparison.edge_ids


async def test_exposure_preference_avoids_polluted_edges(osm_repo, settings, solver_settings):
    # Every grid edge touching row 0 or column 4 (the fastest-looking corner path) is heavily polluted.
    g = osm_repo.graph
    hot = {e.edge_id for e in g.edges if e.u in {node_id(0, c) for c in range(5)} | {node_id(r, 4) for r in range(5)}
           and e.v in {node_id(0, c) for c in range(5)} | {node_id(r, 4) for r in range(5)}}
    repo = FakePollutionRepository([weight(i, exposure=20.0 if i in hot else 0.5) for i in range(1, 41)])
    clean = await solve(MockSolver(), osm_repo, repo, settings, solver_settings, request_body(alpha=0.0, beta=1.0))
    assert not hot & set(clean.recommended.edge_ids)
    assert clean.recommended.metrics.exposure_index_min <= clean.comparison.metrics.exposure_index_min
    assert clean.delta_exposure_pct is not None and clean.delta_exposure_pct <= 0
    # alpha = 1: time only, so the recommended route is as fast as the comparison.
    fast = await solve(MockSolver(), osm_repo, repo, settings, solver_settings, request_body(alpha=1.0, beta=0.0))
    assert fast.recommended.metrics.travel_time_min == pytest.approx(fast.comparison.metrics.travel_time_min)


@pytest.mark.parametrize(
    ("alpha", "beta", "expected", "fast_cost", "clean_cost"),
    [(0.8, 0.2, (1, 2), 1.0, 1.275), (0.2, 0.8, (3, 4), 1.0, 0.6)],
)
def test_weighted_cost_matches_manual_two_route_example(monkeypatch, alpha, beta, expected, fast_cost, clean_cost):
    # Fast path: T=20 s, E=80 index·s. Clean path: T=30 s, E=30 index·s.
    # Fastest-path normalization gives (T/Tref, E/Eref)=(1, 1) and (1.5, .375).
    def edge(eid, u, v, seconds, index):
        return SimpleNamespace(edge_id=eid, u=u, v=v, bidirectional=False,
                               travel_time_s=seconds, exposure_index=index)

    edges = [edge(1, 1, 2, 10, 4), edge(2, 2, 4, 10, 4),
             edge(3, 1, 3, 15, 1), edge(4, 3, 4, 15, 1)]
    si = SimpleNamespace(edges=edges, origin=SimpleNamespace(node_id=1),
                         destination=SimpleNamespace(node_id=4),
                         preference=SimpleNamespace(alpha=alpha, beta=beta))
    observed = []

    def capture_costs(solver_input, cost, token):
        observed.append([cost(e) for e in edges])
        return shortest_path(solver_input, cost, token)

    monkeypatch.setattr("solver.mock.shortest_path", capture_costs)
    result = _routes(CancelToken(), si)
    assert result.comparison == (1, 2)
    assert result.recommended == expected
    assert sum(observed[1][:2]) == pytest.approx(fast_cost)
    assert sum(observed[1][2:]) == pytest.approx(clean_cost)


@pytest.mark.parametrize("value", [None, -1, float("nan"), float("inf"), True])
@pytest.mark.parametrize("field", ["travel_time_s", "exposure_index"])
def test_invalid_edge_values_rejected_before_search(value, field):
    """Reject invalid edge values even if the route would not traverse them."""
    si = grid_input(2, reachable=True)
    si.edges[-1] = replace(si.edges[-1], **{field: value})
    with pytest.raises(ValueError):
        _routes(CancelToken(), si)


def test_zero_exposure_reference_uses_one_as_denominator():
    """Keep zero-reference exposure costs finite and select the zero route."""
    si = grid_input(2, reachable=True)
    si.preference = Preference(0, 1)
    si.edges = [replace(edge, exposure_index=0 if i in (0, 2) else 2)
                for i, edge in enumerate(si.edges)]
    result = _routes(CancelToken(), si)
    assert result.comparison == (1, 3)
    assert result.recommended == (1, 3)


def test_zero_time_reference_uses_one_as_denominator():
    """Allow zero total time without dividing by zero."""
    si = grid_input(2, reachable=True)
    si.edges = [replace(edge, travel_time_s=0.0) for edge in si.edges]
    result = _routes(CancelToken(), si)
    assert result.comparison and result.recommended


@pytest.mark.parametrize("alpha", [0, .2, .5, .8, 1])
@pytest.mark.parametrize("seed", range(5))
def test_optimized_objective_matches_validated_formula(alpha, seed):
    """Match minimum weighted costs from the original validated formula."""
    rng = random.Random(seed)
    si = grid_input(5, reachable=True)
    si.preference = Preference(alpha, 1 - alpha)
    si.edges = [replace(edge, travel_time_s=rng.uniform(1, 100),
                        exposure_index=rng.choice([0.0, 0.1, 1.0, 10.0])) for edge in si.edges]
    result = _routes(CancelToken(), si)
    by_id = {edge.edge_id: edge for edge in si.edges}
    t_ref, e_ref = route_totals((by_id[i].travel_time_s, by_id[i].exposure_index) for i in result.comparison)
    def cost(edge):
        """Return the independently validated normalized edge cost."""
        return weighted_cost(edge.travel_time_s, edge_exposure(edge.travel_time_s, edge.exposure_index),
                             t_ref, e_ref, alpha, 1 - alpha)
    expected = shortest_path(si, cost)
    assert math.fsum(cost(by_id[i]) for i in result.recommended) == pytest.approx(
        math.fsum(cost(by_id[i]) for i in expected))


def test_edge_validation_respects_cancellation():
    """Stop edge validation promptly after cancellation."""
    from solver.cancel import Cancelled

    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        _routes(token, grid_input(2, reachable=True))
