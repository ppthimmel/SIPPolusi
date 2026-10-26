"""POST /v1/routes: valid request, failed validation, route not available, upstream
failure, and the request size limit (TI-SE-08 acceptance criteria)."""
from __future__ import annotations

import asyncio
import json

import pytest

from osm.errors import CacheUnavailable
from public_api.schemas import RouteFeatureCollection
from solver.config import SolverSettings
from solver.contract import SolverReply
from solver.geojson import CRS84, ROUTE_UNITS
from solver.mock import MockSolver

from .conftest import EMPTY, FakeSurfaceRepository, error, request_body

GEOJSON = "application/geo+json"


def assert_valid_route_geojson(body: dict) -> RouteFeatureCollection:
    """Schema-valid (every field documented), RFC 7946 shaped, with CRS and units."""
    fc = RouteFeatureCollection.model_validate(body)
    assert [f.properties.role for f in fc.features] == ["recommended", "comparison"]
    for f in fc.features:
        for lon, lat in f.geometry.coordinates:
            assert 106 < lon < 107 and -7 < lat < -6  # [lon, lat], not [lat, lon]
    assert fc.metadata.crs == CRS84 and fc.metadata.coordinate_order == ["lon", "lat"]
    numeric = {k for k, v in body["features"][0]["properties"].items() if isinstance(v, (int, float))
               and not isinstance(v, bool)} | set(body["metadata"]["comparison"])
    assert numeric <= set(fc.metadata.units), numeric - set(fc.metadata.units)
    return fc


# ------------------------------------------------------------------ valid request

def test_valid_request_returns_route_geojson(make_client):
    with make_client() as c:
        resp = c.post("/v1/routes", json=request_body(), headers={"X-Request-ID": "trace-1"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == GEOJSON
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-request-id"] == "trace-1"
    fc = assert_valid_route_geojson(resp.json())
    m = fc.metadata
    assert (m.request_id, m.route_source, m.preference_applied, m.fallback_reason) == ("trace-1", "policy", True, None)
    assert m.model_version.route_policy == "mock-1" and m.model_version.downscaling == "idw-p2-k8"
    assert m.units == ROUTE_UNITS and m.units["travel_time_min"] == "min" and m.units["exposure_ug_min_m3"] == "µg·min/m³"
    rec = fc.features[0].properties
    assert rec.travel_time_min > 0 and rec.distance_m > 0 and rec.exposure_index_min > 0


def test_same_response_as_the_inspection_endpoint(make_client):
    with make_client() as c:
        public = c.post("/v1/routes", json=request_body(), headers={"X-Request-ID": "same"}).json()
        plan = c.post("/v1/routes/plan", json=request_body(), headers={"X-Request-ID": "same"}).json()
    assert public == plan


def test_fallback_is_200_and_marked(make_client):
    with make_client(solver=MockSolver("error")) as c:
        resp = c.post("/v1/routes", json=request_body())
    assert resp.status_code == 200, resp.text
    fc = assert_valid_route_geojson(resp.json())
    assert (fc.metadata.route_source, fc.metadata.preference_applied, fc.metadata.fallback_reason) == \
        ("baseline", False, "model_error")
    assert fc.features[0].properties.route_source == "baseline" and fc.metadata.notes


def test_gzip_when_accepted(make_client):
    with make_client() as c:
        resp = c.post("/v1/routes", json=request_body(), headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200 and resp.headers["content-encoding"] == "gzip"
    assert_valid_route_geojson(resp.json())  # httpx decodes it


# ------------------------------------------------------------------ failed validation (400)

INVALID = {
    "alpha_plus_beta": request_body(alpha=0.6, beta=0.5),
    "alpha_negative": request_body(alpha=-0.1, beta=1.1),
    "unknown_mode": {**request_body(), "mode": "car"},
    "unknown_pollutant": {**request_body(), "pollutant": "co"},
    "latitude_out_of_range": {**request_body(), "origin": {"lat": 106.8, "lon": -6.2}},
    "missing_destination": {k: v for k, v in request_body().items() if k != "destination"},
    "preference_not_object": {**request_body(), "preference": 0.5},
    "body_is_array": [request_body()],
}


@pytest.mark.parametrize("body", INVALID.values(), ids=INVALID.keys())
def test_invalid_request_is_400_without_reading_the_cache(make_client, pollution_repo, body):
    with make_client() as c:
        error(c.post("/v1/routes", json=body), 400, "invalid_request")
    assert pollution_repo.calls == []  # UT-BE-01 (b): 400 without reading the cache layers


@pytest.mark.parametrize("content, content_type", [
    (b'{"origin": ', "application/json"),
    (b"", "application/json"),
    (json.dumps(request_body()).encode(), "text/plain"),
])
def test_malformed_body_is_400(make_client, content, content_type):
    with make_client() as c:
        error(c.post("/v1/routes", content=content, headers={"Content-Type": content_type}), 400, "invalid_request")


def test_wrong_method_is_405(make_client):
    with make_client() as c:
        error(c.get("/v1/routes"), 405, "method_not_allowed")


# ------------------------------------------------------------------ route not available (422)

@pytest.mark.parametrize("body, code, reason", [
    ({**request_body(), "destination": {"lat": -5.75, "lon": 106.55}}, "outside_study_area", None),
    ({**request_body(), "destination": {"lat": -6.18, "lon": 106.80}}, "point_not_on_network", None),
    (request_body(origin=(2, 2), destination=(2, 2)), "no_route_found", "same_node"),
])
def test_route_not_available_is_422(make_client, body, code, reason):
    with make_client() as c:
        detail = error(c.post("/v1/routes", json=body), 422, code)
    assert detail.get("reason") == reason


def test_solver_without_route_is_503_when_no_fallback(make_client):
    # The planner found a reachable subgraph: "no route" is the Route Model's failure, not the trip's.
    with make_client(solver=MockSolver("no_route"), solver_settings=SolverSettings(_env_file=None, fallback="none")) as c:
        detail = error(c.post("/v1/routes", json=request_body()), 503, "route_service_unavailable")
    assert detail["reason"] == "solver_no_route"


class MislabelledSolver:
    """Contract-valid routes, but labelled policy while saying the preference was not applied."""

    name = "http"

    async def solve(self, request):
        reply = await MockSolver().solve(request)
        return SolverReply("ok", "policy", False, None, "svc", reply.recommended, reply.comparison)


def test_mislabelled_model_reply_is_never_shown_as_policy(make_client):
    with make_client(solver=MislabelledSolver()) as c:
        resp = c.post("/v1/routes", json=request_body())
    assert resp.status_code == 200, resp.text
    fc = assert_valid_route_geojson(resp.json())
    assert (fc.metadata.route_source, fc.metadata.preference_applied, fc.metadata.fallback_reason) == \
        ("baseline", False, "model_invalid_response")


# ------------------------------------------------------------------ upstream failure (503)

class SlowSolver:
    name = "http"

    async def solve(self, request) -> SolverReply:
        await asyncio.sleep(5)
        raise AssertionError("unreachable")


def test_route_model_timeout_is_503_on_time(make_client):
    fast = SolverSettings(_env_file=None, fallback="none", budget_ms=600, response_margin_ms=100, grace_ms=50)
    with make_client(solver=SlowSolver(), solver_settings=fast) as c:
        resp = c.post("/v1/routes", json=request_body())
    detail = error(resp, 503, "route_service_unavailable")
    assert detail["reason"] == "solver_timeout"
    assert resp.elapsed.total_seconds() < 2
    assert "SlowSolver" not in detail["message"] and "5" not in detail["message"]


def test_route_model_error_is_503_without_internals(make_client):
    with make_client(solver=MockSolver("error"), solver_settings=SolverSettings(_env_file=None, fallback="none")) as c:
        detail = error(c.post("/v1/routes", json=request_body()), 503, "route_service_unavailable")
    assert detail["reason"] == "solver_error" and "ROUTE_SOLVER_FAULT" not in detail["message"]


@pytest.mark.parametrize("failure, reason", [
    (EMPTY, "cache_empty"),
    (CacheUnavailable("Basis data cache tidak dapat dihubungi.", CacheUnavailable.DATABASE_UNAVAILABLE),
     "database_unavailable"),
    (CacheUnavailable("Query ke cache melewati batas waktu.", CacheUnavailable.QUERY_TIMEOUT), "query_timeout"),
])
def test_cache_failure_is_503_with_reason(make_client, failure, reason):
    with make_client(pollution=FakeSurfaceRepository(fail=failure)) as c:
        detail = error(c.post("/v1/routes", json=request_body()), 503, "cache_unavailable")
    assert detail["reason"] == reason


def test_unexpected_failure_is_500_uniform(make_client, osm_repo):
    osm_repo.fail = RuntimeError("password=s3cret host=db.internal")
    with make_client(raise_server_exceptions=False) as c:
        detail = error(c.post("/v1/routes", json=request_body()), 500, "internal_error")
    assert "s3cret" not in detail["message"] and "db.internal" not in detail["message"]


# ------------------------------------------------------------------ request size (413)

def test_declared_body_over_the_limit_is_413(make_client, pollution_repo):
    body = {**request_body(), "padding": "x" * 20_000}
    with make_client() as c:
        resp = c.post("/v1/routes", json=body, headers={"X-Request-ID": "big-1"})
    detail = error(resp, 413, "payload_too_large")
    assert detail["request_id"] == "big-1" and "16384" in detail["message"]
    assert pollution_repo.calls == []


def test_chunked_body_over_the_limit_is_413(make_client):
    def chunks():
        yield b'{"padding": "'
        for _ in range(40):
            yield b"x" * 1024
        yield b'"}'

    with make_client() as c:
        error(c.post("/v1/routes", content=chunks(), headers={"Content-Type": "application/json"}),
              413, "payload_too_large")


def test_chunked_body_under_the_limit_is_accepted(make_client):
    raw = json.dumps(request_body()).encode()

    def chunks():
        yield raw[:20]
        yield raw[20:]

    with make_client() as c:
        resp = c.post("/v1/routes", content=chunks(), headers={"Content-Type": "application/json"})
    assert resp.status_code == 200, resp.text


def test_limit_is_configurable(make_client):
    with make_client(max_body_bytes=100) as c:
        error(c.post("/v1/routes", json=request_body()), 413, "payload_too_large")
