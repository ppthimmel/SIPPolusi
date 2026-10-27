"""Fixtures for solver tests: the planner's fake caches plus solver helpers."""
from __future__ import annotations

import gzip
import json

import httpx
import pytest

from planner.tests.conftest import (  # noqa: F401  (fixtures and helpers)
    EMPTY,
    FakePollutionRepository,
    node_id,
    osm_repo,
    pollution_repo,
    request_body,
    settings,
    weight,
)
from solver.config import SolverSettings
from solver.contract import SolveRequest
from solver.mock import MockSolver


@pytest.fixture
def solver_settings() -> SolverSettings:
    return SolverSettings(_env_file=None)


def mock_service_transport(fault: str | None = None) -> httpx.MockTransport:
    """An HTTP Route Model stand-in: decodes the IRouteSolve request and answers like the mock."""
    from planner.models import Preference, SolverInput  # noqa: F401

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/solve"
        body = json.loads(gzip.decompress(request.content))
        if fault == "error":
            return httpx.Response(500, json={"error": {"code": "internal_error"}})
        if fault == "no_route":
            return httpx.Response(422, json={"error": {"code": "no_route_found"}})
        if fault == "invalid":
            return httpx.Response(200, json={"route_source": "policy"})
        if fault == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if fault == "unreachable":
            raise httpx.ConnectError("refused", request=request)
        edges = {e["id"]: e for e in body["edges"]}
        path = _fastest(body["origin_node"], body["destination_node"], edges)
        return httpx.Response(200, json={
            "route_source": "policy", "fallback_reason": None, "model_version": "svc-7",
            "recommended": {"edge_ids": path}, "comparison": {"edge_ids": path}})

    return httpx.MockTransport(handler)


def _fastest(start, goal, edges):
    import heapq
    arcs = {}
    for e in edges.values():
        arcs.setdefault(e["u"], []).append((e["v"], e["id"], e["travel_time_s"]))
        if e["bidirectional"]:
            arcs.setdefault(e["v"], []).append((e["u"], e["id"], e["travel_time_s"]))
    best, prev, queue = {start: 0.0}, {}, [(0.0, start)]
    while queue:
        d, n = heapq.heappop(queue)
        if n == goal:
            break
        for m, eid, t in sorted(arcs.get(n, [])):
            if d + t < best.get(m, float("inf")):
                best[m], prev[m] = d + t, (n, eid)
                heapq.heappush(queue, (d + t, m))
    path, n = [], goal
    while n != start:
        n, eid = prev[n]
        path.append(eid)
    return path[::-1]
