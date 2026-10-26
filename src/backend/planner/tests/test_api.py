"""POST /v1/routes/solver-input with fake caches."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from osm.app import create_app
from osm.errors import CacheUnavailable

from .conftest import EMPTY, FakePollutionRepository, node_id, request_body


@pytest.fixture
def make_client(settings, osm_repo):
    def make(pollution=None):
        app = create_app(repository=osm_repo, settings=settings, pollution_repository=pollution or FakePollutionRepository())
        return TestClient(app)
    return make


def error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()["error"]
    assert body["code"] == code and body["request_id"] == resp.headers["X-Request-ID"]
    return body


def test_solver_input(make_client):
    with make_client() as c:
        resp = c.post("/v1/routes/solver-input", json=request_body(), headers={"X-Request-ID": "abc"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["request_id"] == "abc"
    assert body["context"]["mode"] == "walk" and body["context"]["preference"] == {"alpha": 0.5, "beta": 0.5}
    assert body["origin_node"] == node_id(0, 0) and body["destination_node"] == node_id(4, 4)
    assert body["edge_count"] == len(body["edges"]) == 40 and body["node_count"] == len(body["nodes"]) == 25
    e = body["edges"][0]
    assert set(e) >= {"id", "u", "v", "travel_time_s", "exposure_index", "confidence_score", "weight_source",
                      "concentration_ugm3", "bidirectional"}
    assert body["weights"]["time_window_start"] == "2026-10-13T05:00:00Z"
    assert body["source"]["pollution_model_version"] == "idw-p2-k8"


def test_summary_omits_nodes_and_edges(make_client):
    with make_client() as c:
        body = c.post("/v1/routes/solver-input", params={"summary": True}, json=request_body()).json()
    assert body["nodes"] is None and body["edges"] is None and body["edge_count"] == 40


@pytest.mark.parametrize("body, status, code", [
    (request_body(alpha=0.6, beta=0.5), 400, "invalid_request"),
    ({**request_body(), "mode": "car"}, 400, "invalid_request"),
    ({**request_body(), "pollutant": "co"}, 400, "invalid_request"),
    ({**request_body(), "destination": {"lat": -5.75, "lon": 106.55}}, 422, "outside_study_area"),
])
def test_domain_errors(make_client, body, status, code):
    with make_client() as c:
        error(c.post("/v1/routes/solver-input", json=body), status, code)


def test_empty_cache_is_503_with_reason(make_client):
    with make_client(FakePollutionRepository(fail=EMPTY)) as c:
        body = error(c.post("/v1/routes/solver-input", json=request_body()), 503, "cache_unavailable")
    assert body["reason"] == CacheUnavailable.CACHE_EMPTY
