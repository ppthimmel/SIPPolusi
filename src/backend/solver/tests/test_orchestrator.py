"""Timeout, failures and routes without an answer become structured errors."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from osm.errors import NoRouteFound
from solver.config import SolverSettings
from solver.contract import RouteServiceUnavailable, SolverReply
from solver.mock import MockSolver
from solver.orchestrator import solve_route_request

from .conftest import request_body

FAST = SolverSettings(_env_file=None, budget_ms=400, response_margin_ms=0, grace_ms=50)


async def solve(solver, osm_repo, pollution_repo, settings, solver_settings=FAST, body=None, received_at=None):
    return await solve_route_request(
        body or request_body(), osm=osm_repo, pollution=pollution_repo, osm_settings=settings, solver=solver,
        solver_settings=solver_settings, request_id="r", received_at=received_at or datetime.now(timezone.utc))


class StubSolver:
    name = "stub"

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc = reply, exc

    async def solve(self, request):
        if self.exc:
            raise self.exc
        return self.reply


async def expect_503(reason, coro):
    with pytest.raises(RouteServiceUnavailable) as exc:
        await coro
    assert exc.value.code == "route_service_unavailable" and exc.value.reason == reason
    return exc.value


async def test_timeout_is_503_solver_timeout(osm_repo, pollution_repo, settings):
    started = asyncio.get_running_loop().time()
    await expect_503("solver_timeout", solve(MockSolver("timeout"), osm_repo, pollution_repo, settings))
    assert asyncio.get_running_loop().time() - started < 2  # cut at deadline + grace, not at the solver's 5 s


async def test_injected_error_is_503_solver_error(osm_repo, pollution_repo, settings):
    await expect_503("solver_error", solve(MockSolver("error"), osm_repo, pollution_repo, settings))


async def test_unexpected_exception_is_not_a_500(osm_repo, pollution_repo, settings):
    await expect_503("solver_error", solve(StubSolver(exc=KeyError("x")), osm_repo, pollution_repo, settings))


async def test_no_route_is_422_with_reason(osm_repo, pollution_repo, settings):
    with pytest.raises(NoRouteFound) as exc:
        await solve(MockSolver("no_route"), osm_repo, pollution_repo, settings)
    assert exc.value.reason == "solver_no_route"


async def test_unknown_edge_is_invalid_response(osm_repo, pollution_repo, settings):
    await expect_503("invalid_solver_response", solve(MockSolver("invalid"), osm_repo, pollution_repo, settings))


@pytest.mark.parametrize("edge_ids", [(), (1, 3), (2,)])
async def test_broken_paths_are_invalid_response(osm_repo, pollution_repo, settings, edge_ids):
    reply = SolverReply("ok", "policy", True, None, "x", edge_ids, edge_ids)
    await expect_503("invalid_solver_response", solve(StubSolver(reply), osm_repo, pollution_repo, settings))


async def test_wrong_reply_type_is_invalid_response(osm_repo, pollution_repo, settings):
    await expect_503("invalid_solver_response", solve(StubSolver({"edge_ids": [1]}), osm_repo, pollution_repo, settings))


async def test_no_time_left_is_deadline_exceeded(osm_repo, pollution_repo, settings):
    old = datetime.now(timezone.utc) - timedelta(seconds=10)
    await expect_503("deadline_exceeded", solve(MockSolver(), osm_repo, pollution_repo, settings, received_at=old))


async def test_same_node_is_422_same_node(osm_repo, pollution_repo, settings):
    with pytest.raises(NoRouteFound) as exc:
        await solve(MockSolver(), osm_repo, pollution_repo, settings, body=request_body(destination=(0, 0)))
    assert exc.value.reason == "same_node"


async def test_cache_errors_pass_through(osm_repo, settings):
    from osm.errors import CacheUnavailable
    from .conftest import EMPTY, FakePollutionRepository
    with pytest.raises(CacheUnavailable):
        await solve(MockSolver(), osm_repo, FakePollutionRepository(fail=EMPTY), settings)
