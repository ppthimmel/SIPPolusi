"""HttpSolver: request body and classification of every failure."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import httpx
import pytest

from planner.assembler import build_solver_input
from solver.contract import SolveRequest, SolverError
from solver.http_client import HttpSolver

from .conftest import mock_service_transport, request_body


@pytest.fixture
async def solve_request(osm_repo, pollution_repo, settings):
    si = await build_solver_input(request_body(), osm=osm_repo, pollution=pollution_repo, settings=settings,
                                  request_id="req-h")
    return SolveRequest(si, 1200)


async def test_request_body_is_gzip_json_with_contract_fields(solve_request):
    seen = {}

    def handler(request: httpx.Request):
        seen["headers"] = request.headers
        seen["body"] = json.loads(gzip.decompress(request.content))
        return httpx.Response(200, json={"route_source": "baseline", "fallback_reason": "policy_timeout",
                                         "recommended": {"edge_ids": [1]}, "comparison": {"edge_ids": [1]}})

    reply = await HttpSolver("http://svc", transport=httpx.MockTransport(handler)).solve(solve_request)
    body = seen["body"]
    assert seen["headers"]["content-encoding"] == "gzip" and seen["headers"]["x-request-id"] == "req-h"
    assert {"request_id", "mode", "deadline_ms", "preference", "origin_node", "destination_node", "nodes", "edges"} <= set(body)
    assert body["deadline_ms"] == 1200 and body["preference"] == {"alpha": 0.5, "beta": 0.5}
    assert {"id", "u", "v", "travel_time_s", "exposure_index", "confidence_score", "bidirectional"} <= set(body["edges"][0])
    assert (reply.route_source, reply.preference_applied, reply.fallback_reason) == ("baseline", False, "policy_timeout")


@pytest.mark.parametrize("fault, reason", [
    ("timeout", "solver_timeout"), ("unreachable", "solver_unreachable"),
    ("error", "solver_error"), ("invalid", "invalid_solver_response"),
])
async def test_failures_are_classified(solve_request, fault, reason):
    with pytest.raises(SolverError) as exc:
        await HttpSolver("http://svc", transport=mock_service_transport(fault)).solve(solve_request)
    assert exc.value.reason == reason


async def test_422_no_route_found_is_a_no_route_reply(solve_request):
    reply = await HttpSolver("http://svc", transport=mock_service_transport("no_route")).solve(solve_request)
    assert reply.status == "no_route"
