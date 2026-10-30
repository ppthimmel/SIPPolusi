"""In-process solvers answer on time and stop computing once the timeout cancels them.

Two failure modes this guards against:
- a search run synchronously inside the async solve() blocks the event loop, so
  call_route_solve's asyncio.wait_for cannot fire until the search ends;
- a search run in a plain thread keeps computing after the timeout.

Assertions on `raised` are the deterministic part: the worker only raises
Cancelled when it was stopped by the token, never when it finished on its own.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import solver.cancel as cancel
from route_model.astar_osm import Edge, RoadRouter
from solver.baseline import BaselineSolver, _CheckedArcs, _CheckedCoords
from solver.cancel import Cancelled, CancelToken, run_cancellable
from solver.contract import SOLVER_TIMEOUT, RouteServiceUnavailable, SolveRequest
from solver.mock import MockSolver
from solver.orchestrator import call_route_solve

from .biggrid import grid_input


class RecordingToken(CancelToken):
    created: list[RecordingToken] = []

    def __init__(self) -> None:
        super().__init__()
        self.checks = 0
        self.raised = False
        RecordingToken.created.append(self)

    def check(self) -> None:
        self.checks += 1
        try:
            super().check()
        except Cancelled:
            self.raised = True
            raise


@pytest.fixture
def tokens(monkeypatch) -> list[RecordingToken]:
    RecordingToken.created = []
    monkeypatch.setattr(cancel, "CancelToken", RecordingToken)
    return RecordingToken.created


@pytest.fixture(scope="module")
def big():
    # 160 001 nodes, 319 200 edges; the destination is unreachable, so an
    # uncancelled search expands every node (mock ~0.3 s, baseline ~2 s locally).
    return grid_input(400)


async def test_run_cancellable_stops_the_worker(tokens):
    def spin(token: CancelToken) -> str:
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            token.check()
        return "finished"

    started = time.perf_counter()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(run_cancellable(spin), timeout=0.05)
    assert time.perf_counter() - started < 0.5
    (token,) = tokens
    assert token.finished.wait(0.5) and token.raised


async def test_run_cancellable_returns_the_result(tokens):
    assert await run_cancellable(lambda token, x: x * 2, 21) == 42
    assert tokens[0].finished.is_set() and not tokens[0].raised


async def test_mock_search_does_not_block_the_timeout(big, tokens):
    started = time.perf_counter()
    with pytest.raises(RouteServiceUnavailable) as exc:
        await call_route_solve(MockSolver(), SolveRequest(big, deadline_ms=30), grace_ms=0)
    elapsed = time.perf_counter() - started
    assert exc.value.reason == SOLVER_TIMEOUT
    # Same headroom over the deadline as the baseline test: on shared CI runners the
    # GIL handoff to the event loop alone has taken over 0.12 s. A search that held
    # the loop would take the whole uncancelled search (over 1 s there).
    assert elapsed < 0.3, f"timeout fired after {elapsed:.2f} s; the search held the event loop"
    (token,) = tokens
    assert token.finished.wait(0.5) and token.raised
    assert token.checks < len(big.nodes)  # stopped before expanding every node


async def test_event_loop_keeps_running_during_a_mock_search(big):
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    task = asyncio.create_task(ticker())
    reply = await MockSolver().solve(SolveRequest(big, deadline_ms=5000))
    task.cancel()
    assert reply.status == "no_route"
    assert ticks >= 5  # the loop served other tasks while the search ran


async def test_baseline_stops_after_the_timeout(big, tokens):
    started = time.perf_counter()
    with pytest.raises(RouteServiceUnavailable) as exc:
        await call_route_solve(BaselineSolver(), SolveRequest(big, deadline_ms=100), grace_ms=0)
    assert exc.value.reason == SOLVER_TIMEOUT
    assert time.perf_counter() - started < 0.4
    (token,) = tokens
    # Building the router includes networkx strongly connected components, which
    # has no check point; that bounds how long the thread runs after cancellation.
    assert token.finished.wait(1.0) and token.raised


async def test_baseline_still_finds_routes_when_not_cancelled(tokens):
    si = grid_input(30, reachable=True)
    reply = await BaselineSolver().solve(SolveRequest(si, deadline_ms=5000))
    assert reply.status == "ok" and len(reply.recommended) == 2 * 29
    assert not tokens[0].raised


async def test_weighted_stops_after_timeout(big, tokens):
    """Stop the weighted worker when the route solver deadline expires."""
    from solver.weighted import WeightedSolver

    with pytest.raises(RouteServiceUnavailable) as exc:
        await call_route_solve(WeightedSolver(), SolveRequest(big, deadline_ms=100), grace_ms=0)
    assert exc.value.reason == SOLVER_TIMEOUT
    (token,) = tokens
    assert token.finished.wait(1.0) and token.raised


def _router(si, token: CancelToken) -> RoadRouter:
    coords = {n: (p.lon, p.lat) for n, p in si.nodes.items()}
    edges = [(Edge(e.edge_id, e.u, e.v, e.length_m, e.travel_time_s,
                   f"LINESTRING({coords[e.u][0]} {coords[e.u][1]}, {coords[e.v][0]} {coords[e.v][1]})"),
              e.bidirectional) for e in si.edges]
    return RoadRouter("g", "walk", _CheckedCoords(coords, token), edges)


def test_route_model_construction_reaches_the_check_point():
    # Fails if RoadRouter.__init__ stops testing node membership in `coords`.
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        _router(grid_input(5, reachable=True), token)


def test_route_model_search_reaches_the_check_point():
    # Fails if RoadRouter.search stops reading adjacency through `arcs.get`.
    si = grid_input(5, reachable=True)
    token = CancelToken()
    router = _router(si, token)
    router.arcs = _CheckedArcs(router.arcs, token)
    assert router.search(si.origin.node_id, si.destination.node_id)[0] is not None
    token.cancel()
    with pytest.raises(Cancelled):
        router.search(si.origin.node_id, si.destination.node_id)
