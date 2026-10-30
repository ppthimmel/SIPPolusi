"""The mock solver: deterministic, preference-aware, fault injection."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from solver.mock import MockSolver
from solver.cancel import CancelToken
from solver.mock import _routes
from solver.paths import shortest_path
from solver.orchestrator import solve_route_request

from .conftest import FakePollutionRepository, node_id, request_body, weight


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
