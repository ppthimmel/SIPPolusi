"""POST /v1/routes/solve: provider from configuration, structured errors."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from osm.app import create_app
from solver.baseline import BaselineSolver
from solver.config import SolverSettings
from solver.factory import make_solver
from solver.mock import MockSolver

from .conftest import request_body


@pytest.fixture
def make_client(settings, osm_repo, pollution_repo):
    def make(solver=None, solver_settings=None):
        solver_settings = solver_settings or SolverSettings(_env_file=None, fallback="none")
        app = create_app(repository=osm_repo, settings=settings, pollution_repository=pollution_repo,
                         solver=solver, solver_settings=solver_settings)
        return TestClient(app)
    return make


def test_solve_with_default_mock(make_client):
    with make_client() as c:
        resp = c.post("/v1/routes/solve", json=request_body(), headers={"X-Request-ID": "abc"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["solver"] == "mock" and body["route_source"] == "policy" and body["model_version"] == "mock-1"
    for role in ("recommended", "comparison"):
        route = body[role]
        assert route["role"] == role and route["geometry"]["type"] == "LineString"
        assert len(route["geometry"]["coordinates"]) >= 2 and route["edge_ids"]
        assert {"travel_time_min", "distance_m", "exposure_index_min", "confidence_score"} <= set(route["metrics"])
    assert body["context"]["request_id"] == "abc" and body["deadline_ms"] > 0
    assert set(body["comparison_summary"]) == {"delta_travel_time_min", "delta_exposure_pct"}


def test_provider_changes_without_endpoint_change(make_client):
    with make_client(solver=BaselineSolver()) as c:
        body = c.post("/v1/routes/solve", json=request_body()).json()
    assert body["solver"] == "baseline" and body["fallback_reason"] == "policy_unavailable"


@pytest.mark.parametrize("fault, status, code, reason", [
    ("error", 503, "route_service_unavailable", "solver_error"),
    ("invalid", 503, "route_service_unavailable", "invalid_solver_response"),
    ("no_route", 503, "route_service_unavailable", "solver_no_route"),
])
def test_faults_are_structured(make_client, fault, status, code, reason):
    with make_client(solver=MockSolver(fault)) as c:
        resp = c.post("/v1/routes/solve", json=request_body())
    assert resp.status_code == status
    error = resp.json()["error"]
    assert (error["code"], error["reason"]) == (code, reason) and error["request_id"]


def test_timeout_is_structured(make_client):
    fast = SolverSettings(_env_file=None, budget_ms=400, response_margin_ms=0, grace_ms=50, fallback="none")
    with make_client(solver=MockSolver("timeout"), solver_settings=fast) as c:
        error = c.post("/v1/routes/solve", json=request_body()).json()["error"]
    assert (error["code"], error["reason"]) == ("route_service_unavailable", "solver_timeout")


def test_received_at_header_counts_toward_the_budget(make_client):
    old = (datetime.now(timezone.utc) - timedelta(seconds=9)).isoformat()
    with make_client() as c:
        error = c.post("/v1/routes/solve", json=request_body(), headers={"X-Request-Received-At": old}).json()["error"]
    assert error["reason"] == "deadline_exceeded"


def test_invalid_request_still_400(make_client):
    with make_client() as c:
        assert c.post("/v1/routes/solve", json=request_body(alpha=0.9, beta=0.9)).status_code == 400


@pytest.mark.parametrize("provider, name", [("mock", "mock"), ("baseline", "baseline"), ("weighted", "weighted")])
def test_factory_reads_configuration(provider, name):
    assert make_solver(SolverSettings(_env_file=None, provider=provider)).name == name


def test_http_provider_requires_url():
    with pytest.raises(ValueError):
        SolverSettings(_env_file=None, provider="http")
    assert make_solver(SolverSettings(_env_file=None, provider="http", url="http://svc")).name == "http"
