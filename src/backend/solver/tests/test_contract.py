"""Contract tests: every provider behind IRouteSolve gives a valid SolveResult on the same
input, without any AI service (mock, in-process baseline, HTTP against a stand-in)."""
from __future__ import annotations

import pytest

from solver.baseline import BaselineSolver
from solver.contract import SolveResult
from solver.http_client import HttpSolver
from solver.mock import MockSolver
from solver.orchestrator import solve_route_request

from .conftest import mock_service_transport, node_id, request_body

PROVIDERS = {
    "mock": lambda: MockSolver(),
    "baseline": lambda: BaselineSolver(),
    "http": lambda: HttpSolver("http://route-model.internal", transport=mock_service_transport()),
}


async def solve(solver, osm_repo, pollution_repo, settings, solver_settings, body=None, **kw):
    from datetime import datetime, timezone
    return await solve_route_request(
        body or request_body(), osm=osm_repo, pollution=pollution_repo, osm_settings=settings, solver=solver,
        solver_settings=solver_settings, request_id="req-c", received_at=datetime.now(timezone.utc), **kw)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_every_provider_honours_the_contract(provider, osm_repo, pollution_repo, settings, solver_settings):
    result = await solve(PROVIDERS[provider](), osm_repo, pollution_repo, settings, solver_settings)
    assert isinstance(result, SolveResult) and result.solver == provider
    origin, goal = osm_repo.graph.nodes[node_id(0, 0)], osm_repo.graph.nodes[node_id(4, 4)]
    for route in (result.recommended, result.comparison):
        assert route.coordinates[0] == [origin.lon, origin.lat]
        assert route.coordinates[-1] == [goal.lon, goal.lat]
        assert route.metrics.edge_count == len(route.edge_ids) == 8  # Manhattan distance on the grid
        assert route.metrics.travel_time_min > 0 and route.metrics.exposure_index_min > 0
        assert 0 <= route.metrics.confidence_score <= 1
    assert result.comparison.route_source == "baseline" and result.comparison.preference_applied is False
    assert result.context.request_id == "req-c" and result.context.graph_version
    assert 0 < result.deadline_ms <= solver_settings.budget_ms


async def test_baseline_marks_fallback(osm_repo, pollution_repo, settings, solver_settings):
    result = await solve(BaselineSolver(), osm_repo, pollution_repo, settings, solver_settings)
    assert (result.route_source, result.preference_applied, result.fallback_reason) == \
        ("baseline", False, "policy_unavailable")
    assert result.recommended.edge_ids == result.comparison.edge_ids
    assert any("tidak diterapkan" in n for n in result.notes)
