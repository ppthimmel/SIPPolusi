"""TI-SE-07 QA: route labels are checked, not trusted, and "no route" from the solver
is a solver failure.

1. A reply labelled policy must apply the preference without a fallback reason; one
   labelled baseline must not apply it and must give a fallback reason.
2. Every route shown as baseline (the comparison, or a baseline recommendation) must
   be a fastest route of the subgraph; equal-time alternatives are accepted.
3. "no route" from the solver is 503 solver_no_route whenever no fallback can run.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import solver.cancel as cancel
import solver.orchestrator as orchestrator
from solver.cancel import CancelToken
from solver.config import SolverSettings
from solver.contract import RouteServiceUnavailable, SolverReply
from solver.http_client import HttpSolver
from solver.mock import MockSolver
from solver.orchestrator import solve_route_request
from solver.paths import shortest_path

from .conftest import node_id, request_body

NO_FALLBACK = SolverSettings(_env_file=None, budget_ms=400, response_margin_ms=0, grace_ms=50, fallback="none")
WITH_FALLBACK = SolverSettings(_env_file=None, budget_ms=400, response_margin_ms=0, grace_ms=50, fallback="baseline")


class FnSolver:
    """Answers with reply_for(solver_input), so tests can build paths on the real subgraph."""

    name = "http"

    def __init__(self, reply_for):
        self.reply_for = reply_for

    async def solve(self, request):
        return self.reply_for(request.solver_input)


def fastest(si):
    return shortest_path(si, lambda e: e.travel_time_s)


def detour(si):
    """A valid but slower path: avoid every edge of row 0 (origin and destination are on it)."""
    row0 = {node_id(0, c) for c in range(5)}
    return shortest_path(si, lambda e: e.travel_time_s * (100 if e.u in row0 and e.v in row0 else 1))


async def solve(solver, osm_repo, pollution_repo, settings, solver_settings=NO_FALLBACK, body=None, received_at=None):
    return await solve_route_request(
        body or request_body(destination=(0, 4)), osm=osm_repo, pollution=pollution_repo, osm_settings=settings,
        solver=solver, solver_settings=solver_settings, request_id="prov",
        received_at=received_at or datetime.now(timezone.utc))


async def expect_invalid(coro, detail: str):
    with pytest.raises(RouteServiceUnavailable) as exc:
        await coro
    assert exc.value.reason == "invalid_solver_response" and detail in exc.value.detail


# ------------------------------------------------------------------ 1. labels agree

@pytest.mark.parametrize("source, applied, fallback_reason", [
    ("policy", False, None),               # policy route claiming the preference was not applied
    ("policy", True, "policy_timeout"),    # policy route with a fallback reason
    ("baseline", True, "policy_timeout"),  # baseline route claiming the preference was applied
    ("baseline", False, None),             # baseline route without saying why
])
async def test_contradicting_labels_are_invalid(osm_repo, pollution_repo, settings, source, applied, fallback_reason):
    solver = FnSolver(lambda si: SolverReply("ok", source, applied, fallback_reason, "m", fastest(si), fastest(si)))
    await expect_invalid(solve(solver, osm_repo, pollution_repo, settings), f"route_source {source}")


async def test_contradicting_labels_fall_back_with_marker(osm_repo, pollution_repo, settings):
    solver = FnSolver(lambda si: SolverReply("ok", "policy", False, None, "m", fastest(si), fastest(si)))
    result = await solve(solver, osm_repo, pollution_repo, settings, WITH_FALLBACK)
    assert (result.route_source, result.preference_applied, result.fallback_reason) == \
        ("baseline", False, "model_invalid_response")


@pytest.mark.parametrize("reply, accepted", [
    ({"route_source": "policy", "preference_applied": False}, False),
    ({"route_source": "baseline", "preference_applied": True, "fallback_reason": "policy_timeout"}, False),
    ({"route_source": "baseline", "fallback_reason": "policy_timeout"}, True),   # preference_applied derived: false
    ({"route_source": "policy"}, True),                                          # derived: true
])
async def test_http_reply_labels_are_checked(osm_repo, pollution_repo, settings, reply, accepted):
    def handler(request: httpx.Request):
        body = json.loads(gzip.decompress(request.content))
        edges = {e["id"]: e for e in body["edges"]}
        from .conftest import _fastest
        path = _fastest(body["origin_node"], body["destination_node"], edges)
        return httpx.Response(200, json={"model_version": "svc", "recommended": {"edge_ids": path},
                                         "comparison": {"edge_ids": path}, **reply})

    solver = HttpSolver("http://route-model", transport=httpx.MockTransport(handler))
    if accepted:
        result = await solve(solver, osm_repo, pollution_repo, settings)
        assert result.route_source == reply["route_source"]
        assert result.preference_applied is (reply["route_source"] == "policy")
    else:
        await expect_invalid(solve(solver, osm_repo, pollution_repo, settings), "route_source")


# ------------------------------------------------------------------ 2. baseline routes are fastest

async def test_slower_comparison_is_invalid(osm_repo, pollution_repo, settings):
    solver = FnSolver(lambda si: SolverReply("ok", "policy", True, None, "m", fastest(si), detour(si)))
    await expect_invalid(solve(solver, osm_repo, pollution_repo, settings), "pembanding")


async def test_slower_comparison_falls_back(osm_repo, pollution_repo, settings):
    solver = FnSolver(lambda si: SolverReply("ok", "policy", True, None, "m", fastest(si), detour(si)))
    result = await solve(solver, osm_repo, pollution_repo, settings, WITH_FALLBACK)
    assert result.fallback_reason == "model_invalid_response"
    assert result.comparison.metrics.travel_time_min <= result.recommended.metrics.travel_time_min + 1e-9


async def test_slower_policy_recommendation_is_fine(osm_repo, pollution_repo, settings):
    # A policy recommendation may trade time for exposure; only routes labelled baseline must be fastest.
    solver = FnSolver(lambda si: SolverReply("ok", "policy", True, None, "m", detour(si), fastest(si)))
    result = await solve(solver, osm_repo, pollution_repo, settings)
    assert result.route_source == "policy"
    assert result.recommended.metrics.travel_time_min > result.comparison.metrics.travel_time_min


async def test_slower_baseline_recommendation_is_invalid(osm_repo, pollution_repo, settings):
    solver = FnSolver(lambda si: SolverReply("ok", "baseline", False, "policy_timeout", "m", detour(si), fastest(si)))
    await expect_invalid(solve(solver, osm_repo, pollution_repo, settings), "rekomendasi")


async def test_equally_fast_alternative_is_accepted(osm_repo, pollution_repo, settings):
    # (0,0) -> (1,1): via (0,1) or via (1,0), same travel time.
    def reply(si):
        a, b = fastest(si), detour(si)  # detour avoids row 0, so it goes via (1,0)
        assert a != b
        return SolverReply("ok", "baseline", False, "policy_timeout", "m", b, a)

    result = await solve(FnSolver(reply), osm_repo, pollution_repo, settings, body=request_body(destination=(1, 1)))
    assert result.route_source == "baseline"
    assert result.recommended.metrics.travel_time_min == pytest.approx(result.comparison.metrics.travel_time_min)


class RecordingToken(CancelToken):
    created: list = []

    def __init__(self):
        super().__init__()
        RecordingToken.created.append(self)


def _spin(token, si):
    end = time.perf_counter() + 5
    while time.perf_counter() < end:
        token.check()
    return 0.0


class FailingLater:
    name = "http"

    async def solve(self, request):
        await asyncio.sleep(0.05)  # the fastest-route search is running by now
        from solver.contract import SolverError
        raise SolverError("solver_error", "boom")


async def test_fastest_check_stops_when_the_solver_fails(osm_repo, pollution_repo, settings, monkeypatch):
    RecordingToken.created = []
    monkeypatch.setattr(cancel, "CancelToken", RecordingToken)
    monkeypatch.setattr(orchestrator, "fastest_time_s", _spin)
    started = time.perf_counter()
    with pytest.raises(RouteServiceUnavailable):
        await solve(FailingLater(), osm_repo, pollution_repo, settings)
    (token,) = RecordingToken.created
    assert token.cancelled and token.finished.wait(1.0)
    assert time.perf_counter() - started < 1.0  # the 5 s search was stopped, not waited for


async def test_fastest_check_is_bounded_by_the_deadline(osm_repo, pollution_repo, settings, monkeypatch):
    monkeypatch.setattr(orchestrator, "fastest_time_s", _spin)
    solver = FnSolver(lambda si: SolverReply("ok", "policy", True, None, "m", fastest(si), fastest(si)))
    started = asyncio.get_running_loop().time()
    with pytest.raises(RouteServiceUnavailable) as exc:
        await solve(solver, osm_repo, pollution_repo, settings)
    assert exc.value.reason == "solver_timeout" and "fastest-route check" in exc.value.detail
    assert asyncio.get_running_loop().time() - started < 1.5


async def test_baseline_solver_is_not_rechecked(osm_repo, pollution_repo, settings, monkeypatch):
    from solver.baseline import BaselineSolver

    def unexpected(token, si):
        raise AssertionError("the in-process baseline is the fastest route by construction")

    monkeypatch.setattr(orchestrator, "fastest_time_s", unexpected)
    result = await solve(BaselineSolver(), osm_repo, pollution_repo, settings)
    assert result.solver == "baseline"


# ------------------------------------------------------------------ 3. "no route" from the solver

async def test_no_route_without_time_for_fallback_is_503(osm_repo, pollution_repo, settings):
    # The model deadline is fine, but nothing is left under the gateway timeout for the fallback.
    tight = SolverSettings(_env_file=None, budget_ms=400, response_margin_ms=0, grace_ms=50, fallback="baseline",
                           gateway_timeout_ms=1)
    with pytest.raises(RouteServiceUnavailable) as exc:
        await solve(MockSolver("no_route"), osm_repo, pollution_repo, settings, tight,
                    received_at=datetime.now(timezone.utc) - timedelta(milliseconds=20))
    assert exc.value.reason == "solver_no_route" and exc.value.status_code == 503


async def test_no_route_with_fallback_is_marked(osm_repo, pollution_repo, settings):
    result = await solve(MockSolver("no_route"), osm_repo, pollution_repo, settings, WITH_FALLBACK)
    assert (result.route_source, result.fallback_reason) == ("baseline", "model_no_route")
