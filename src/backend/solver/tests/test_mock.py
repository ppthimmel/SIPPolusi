"""The mock solver: deterministic, preference-aware, fault injection."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from solver.mock import MockSolver
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
