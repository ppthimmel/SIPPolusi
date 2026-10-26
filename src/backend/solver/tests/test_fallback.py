"""TI-SE-07: Backend fallback to the baseline when the configured solver fails."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from osm.errors import NoRouteFound
from solver.config import SolverSettings
from solver.contract import RouteServiceUnavailable, SolverError
from solver.mock import MockSolver
from solver.orchestrator import solve_route_request

from .conftest import request_body

FAST = dict(budget_ms=400, response_margin_ms=0, grace_ms=50)


async def solve(solver, osm_repo, pollution_repo, settings, fallback="baseline", received_at=None, **kw):
    return await solve_route_request(
        request_body(alpha=0.3, beta=0.7), osm=osm_repo, pollution=pollution_repo, osm_settings=settings,
        solver=solver, solver_settings=SolverSettings(_env_file=None, fallback=fallback, **FAST),
        request_id="req-fb", received_at=received_at or datetime.now(timezone.utc), **kw)


@pytest.mark.parametrize("fault, reason, fallback_reason", [
    ("timeout", "solver_timeout", "model_timeout"),
    ("error", "solver_error", "model_error"),
    ("invalid", "invalid_solver_response", "model_invalid_response"),
    ("no_route", "solver_no_route", "model_no_route"),
])
async def test_failures_fall_back_with_explicit_marker(osm_repo, pollution_repo, settings, fault, reason, fallback_reason):
    result = await solve(MockSolver(fault), osm_repo, pollution_repo, settings)
    assert (result.route_source, result.preference_applied, result.fallback_reason) == ("baseline", False, fallback_reason)
    assert result.recommended.route_source == "baseline" and result.recommended.preference_applied is False
    assert result.solver == "baseline" and result.model_version == "astar-baseline"
    assert (result.fallback.from_solver, result.fallback.failure_reason) == ("mock", reason)
    assert result.recommended.edge_ids == result.comparison.edge_ids  # the fastest route, not a fake "clean" one
    assert any("tidak diterapkan" in n for n in result.notes)


async def test_fallback_disabled_keeps_the_design_error(osm_repo, pollution_repo, settings):
    with pytest.raises(RouteServiceUnavailable) as exc:
        await solve(MockSolver("error"), osm_repo, pollution_repo, settings, fallback="none")
    assert exc.value.reason == "solver_error"
    with pytest.raises(NoRouteFound):
        await solve(MockSolver("no_route"), osm_repo, pollution_repo, settings, fallback="none")


async def test_no_fallback_when_no_time_is_left(osm_repo, pollution_repo, settings):
    # Received 9.9 s ago: the model deadline is gone, and so is the room under the 10 s gateway timeout.
    old = datetime.now(timezone.utc) - timedelta(seconds=9.9)
    with pytest.raises(RouteServiceUnavailable) as exc:
        await solve(MockSolver("error"), osm_repo, pollution_repo, settings, received_at=old)
    assert exc.value.reason in ("deadline_exceeded", "solver_error")


async def test_failed_fallback_reports_the_original_failure(osm_repo, pollution_repo, settings):
    class Broken:
        name = "baseline-broken"

        async def solve(self, request):
            raise SolverError("solver_error", "baseline exploded")

    with pytest.raises(RouteServiceUnavailable) as exc:
        await solve(MockSolver("timeout"), osm_repo, pollution_repo, settings, fallback_solver=Broken())
    assert exc.value.reason == "solver_timeout"


async def test_cache_and_trip_errors_never_fall_back(osm_repo, settings):
    from osm.errors import CacheUnavailable
    from .conftest import EMPTY, FakePollutionRepository
    with pytest.raises(CacheUnavailable):
        await solve(MockSolver("error"), osm_repo, FakePollutionRepository(fail=EMPTY), settings)


async def test_log_line_traces_the_request_without_coordinates(osm_repo, pollution_repo, settings, caplog):
    caplog.set_level(logging.INFO, logger="solver")
    await solve(MockSolver("timeout"), osm_repo, pollution_repo, settings)
    await solve(MockSolver(), osm_repo, pollution_repo, settings)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("route_inference")]
    fallback_line, ok_line = lines[-2], lines[-1]
    for part in ("request_id=req-fb", "solver=mock", "outcome=fallback", "route_source=baseline",
                 "model_version=astar-baseline", "fallback_reason=model_timeout", "mode=walk", "alpha=0.3",
                 "beta=0.7", "deadline_ms=", "inference_ms=", "primary_ms=", "reason=solver_timeout", "detail="):
        assert part in fallback_line, part
    for part in ("outcome=ok", "route_source=policy", "model_version=mock-1", "inference_ms="):
        assert part in ok_line, part
    assert all("106." not in line and "-6." not in line for line in lines)  # no coordinates (PNF-06)
