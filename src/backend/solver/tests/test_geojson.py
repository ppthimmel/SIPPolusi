"""GeoJSON response (Kode 3.2) and what the user may see when things fail."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from osm.app import create_app
from solver.config import SolverSettings
from solver.mock import MockSolver

from .conftest import request_body

INTERNAL_HINTS = ("HTTP", "Error", "Exception", "http://", "Traceback", "KeyError", "mock", "ROUTE_SOLVER")


def assert_valid_geojson(fc):
    """RFC 7946 FeatureCollection of LineString Features with [lon, lat] positions."""
    assert fc["type"] == "FeatureCollection" and isinstance(fc["features"], list) and fc["features"]
    for f in fc["features"]:
        assert f["type"] == "Feature" and isinstance(f["properties"], dict)
        g = f["geometry"]
        assert g["type"] == "LineString" and len(g["coordinates"]) >= 2
        for position in g["coordinates"]:
            lon, lat = position
            assert -180 <= lon <= 180 and -90 <= lat <= 90


@pytest.fixture
def client_with(settings, osm_repo, pollution_repo):
    def make(solver=None, fallback="baseline", **kw):
        s = SolverSettings(_env_file=None, fallback=fallback, **kw)
        return TestClient(create_app(repository=osm_repo, settings=settings, pollution_repository=pollution_repo,
                                     solver=solver, solver_settings=s))
    return make


def test_plan_returns_valid_geojson_mapped_to_graph_edges(client_with, osm_repo):
    with client_with() as c:
        resp = c.post("/v1/routes/plan", json=request_body(), headers={"X-Request-ID": "geo-1"})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/geo+json")
    fc = resp.json()
    assert_valid_geojson(fc)
    rec, cmp_ = fc["features"]
    assert (rec["properties"]["role"], cmp_["properties"]["role"]) == ("recommended", "comparison")
    # Geometry maps back to the graph: it starts and ends at the snapped nodes and uses subgraph edges.
    edges = {e.edge_id for e in osm_repo.graph.edges}
    assert set(rec["properties"]["edge_ids"]) <= edges
    origin = osm_repo.graph.nodes[min(osm_repo.graph.nodes)]
    assert rec["geometry"]["coordinates"][0] == [origin.lon, origin.lat]
    meta = fc["metadata"]
    assert meta["request_id"] == "geo-1" and meta["route_source"] == "policy" and meta["fallback_reason"] is None
    assert meta["model_version"]["route_policy"] == "mock-1" and meta["disclaimer"]


def test_plan_marks_fallback_explicitly(client_with):
    with client_with(MockSolver("error")) as c:
        fc = c.post("/v1/routes/plan", json=request_body()).json()
    assert_valid_geojson(fc)
    meta, rec = fc["metadata"], fc["features"][0]["properties"]
    assert (meta["route_source"], meta["preference_applied"], meta["fallback_reason"]) == ("baseline", False, "model_error")
    assert (rec["route_source"], rec["preference_applied"]) == ("baseline", False)
    assert any("tidak diterapkan" in n for n in meta["notes"])


@pytest.mark.parametrize("fault, reason", [("error", "solver_error"), ("invalid", "invalid_solver_response"),
                                           ("timeout", "solver_timeout")])
def test_failure_without_fallback_hides_internals(client_with, fault, reason):
    with client_with(MockSolver(fault), fallback="none", budget_ms=400, response_margin_ms=0, grace_ms=50) as c:
        resp = c.post("/v1/routes/plan", json=request_body())
    assert resp.status_code == 503 and resp.headers["content-type"].startswith("application/json")
    error = resp.json()["error"]
    assert (error["code"], error["reason"]) == ("route_service_unavailable", reason)
    assert not any(hint in error["message"] for hint in INTERNAL_HINTS), error["message"]
