"""TI-SE-07 integration: the Backend against a real HTTP Route Model stand-in (uvicorn on a
local port), for valid, malformed, slow and unavailable model responses."""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.testclient import TestClient

from osm.app import create_app
from solver.config import SolverSettings
from solver.http_client import HttpSolver

from .conftest import _fastest, request_body
from .test_geojson import INTERNAL_HINTS, assert_valid_geojson

BEHAVIOUR = {"mode": "valid"}
stub = FastAPI()


@stub.post("/internal/v1/solve")
async def solve(request: Request):
    body = json.loads(gzip.decompress(await request.body()))
    mode = BEHAVIOUR["mode"]
    if mode == "slow":
        await asyncio.sleep(3)
    if mode == "malformed_json":
        return PlainTextResponse("<html>upstream proxy error</html>")
    if mode == "malformed_contract":
        return JSONResponse({"route_source": "policy", "recommended": {"edges": [1, 2]}})
    if mode == "invalid_path":
        return JSONResponse({"route_source": "policy", "model_version": "stub-morl-3",
                             "recommended": {"edge_ids": [999999]}, "comparison": {"edge_ids": [999999]}})
    if mode == "error":
        return JSONResponse({"error": {"code": "internal_error"}}, status_code=500)
    edges = {e["id"]: e for e in body["edges"]}
    path = _fastest(body["origin_node"], body["destination_node"], edges)
    return JSONResponse({"route_source": "policy", "fallback_reason": None, "model_version": "stub-morl-3",
                         "recommended": {"edge_ids": path}, "comparison": {"edge_ids": path}})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def service_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(stub, host="127.0.0.1", port=port, log_level="warning",
                                           timeout_graceful_shutdown=1))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


SETTINGS = dict(budget_ms=1500, response_margin_ms=0, grace_ms=100)


def call(url, settings, osm_repo, pollution_repo, mode, fallback):
    BEHAVIOUR["mode"] = mode
    s = SolverSettings(_env_file=None, provider="http", url=url, fallback=fallback, **SETTINGS)
    app = create_app(repository=osm_repo, settings=settings, pollution_repository=pollution_repo,
                     solver=HttpSolver(url, s.grace_ms), solver_settings=s)
    with TestClient(app) as c:
        started = time.perf_counter()
        resp = c.post("/v1/routes/plan", json=request_body(alpha=0.4, beta=0.6), headers={"X-Request-ID": f"it-{mode}"})
        return resp, time.perf_counter() - started


def test_valid_model_response(service_url, settings, osm_repo, pollution_repo):
    resp, _ = call(service_url, settings, osm_repo, pollution_repo, "valid", "baseline")
    assert resp.status_code == 200
    fc = resp.json()
    assert_valid_geojson(fc)
    meta = fc["metadata"]
    assert (meta["route_source"], meta["preference_applied"], meta["fallback_reason"]) == ("policy", True, None)
    assert meta["model_version"]["route_policy"] == "stub-morl-3" and meta["solver"] == "http"


CASES = [  # mode, fallback_reason with fallback, reason without fallback
    ("malformed_json", "model_invalid_response", "invalid_solver_response"),
    ("malformed_contract", "model_invalid_response", "invalid_solver_response"),
    ("invalid_path", "model_invalid_response", "invalid_solver_response"),
    ("error", "model_error", "solver_error"),
    ("slow", "model_timeout", "solver_timeout"),
]


@pytest.mark.parametrize("mode, fallback_reason, _", CASES)
def test_bad_model_falls_back_explicitly(service_url, settings, osm_repo, pollution_repo, mode, fallback_reason, _):
    resp, elapsed = call(service_url, settings, osm_repo, pollution_repo, mode, "baseline")
    assert resp.status_code == 200 and elapsed < 2.5  # never hangs on a slow model
    fc = resp.json()
    assert_valid_geojson(fc)
    meta = fc["metadata"]
    assert (meta["route_source"], meta["preference_applied"], meta["fallback_reason"]) == ("baseline", False, fallback_reason)
    assert meta["model_version"]["route_policy"] == "astar-baseline"


@pytest.mark.parametrize("mode, _, reason", CASES)
def test_bad_model_without_fallback_is_structured(service_url, settings, osm_repo, pollution_repo, mode, _, reason):
    resp, elapsed = call(service_url, settings, osm_repo, pollution_repo, mode, "none")
    assert resp.status_code == 503 and elapsed < 2.5
    error = resp.json()["error"]
    assert (error["code"], error["reason"], error["request_id"]) == ("route_service_unavailable", reason, f"it-{mode}")
    assert not any(hint in error["message"] for hint in INTERNAL_HINTS), error["message"]


def test_unavailable_model(settings, osm_repo, pollution_repo, caplog):
    caplog.set_level(logging.WARNING, logger="solver")
    closed = f"http://127.0.0.1:{_free_port()}"  # nothing listens here
    fb, elapsed = call(closed, settings, osm_repo, pollution_repo, "valid", "baseline")
    assert fb.status_code == 200 and elapsed < 2.5
    assert fb.json()["metadata"]["fallback_reason"] == "model_unavailable"
    err, _ = call(closed, settings, osm_repo, pollution_repo, "valid", "none")
    error = err.json()["error"]
    assert err.status_code == 503 and error["reason"] == "solver_unreachable"
    assert "127.0.0.1" not in error["message"] and "Connect" not in error["message"]
    # The log keeps the cause for operators.
    assert any("reason=solver_unreachable" in r.getMessage() and "ConnectError" in r.getMessage()
               for r in caplog.records)
