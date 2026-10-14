"""Endpoints with a fake repository: status codes, error schema, request id."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from osm.app import create_app
from osm.errors import CacheUnavailable

from .conftest import ORIGIN_LAT, ORIGIN_LON, STEP_DEG, VERSION, FakeRepository, node_id

BBOX = f"{ORIGIN_LON},{ORIGIN_LAT},{ORIGIN_LON + 0.01},{ORIGIN_LAT + 0.01}"


@pytest.fixture
def repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def client(repo, settings):
    with TestClient(create_app(repository=repo, settings=settings)) as c:
        yield c


def assert_error(resp, status: int, code: str):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert body["error"]["request_id"] == resp.headers["X-Request-ID"]


def test_version(client):
    resp = client.get("/v1/osm/version")
    assert resp.status_code == 200
    assert resp.json()["version"] == VERSION
    uuid.UUID(resp.headers["X-Request-ID"], version=4)


def test_incoming_request_id_is_echoed(client):
    resp = client.get("/v1/osm/version", headers={"X-Request-ID": "abc-123"})
    assert resp.headers["X-Request-ID"] == "abc-123"


@pytest.mark.parametrize("reason", [CacheUnavailable.CACHE_EMPTY, CacheUnavailable.DATABASE_UNAVAILABLE,
                                    CacheUnavailable.QUERY_TIMEOUT])
def test_cache_unavailable_maps_to_503_with_reason(client, repo, reason):
    repo.fail = CacheUnavailable("cache tidak dapat dibaca", reason)
    resp = client.get("/v1/osm/version")
    assert_error(resp, 503, "cache_unavailable")
    assert resp.json()["error"]["reason"] == reason


def test_other_errors_have_no_reason(client):
    assert "reason" not in client.get("/v1/osm/edges/777").json()["error"]


def test_health_degraded_when_db_down(client, repo):
    repo.fail = CacheUnavailable("down")
    body = client.get("/health").json()
    assert body["status"] == "degraded" and body["database"] == "unavailable" and body["graph_version"] is None


def test_graph(client):
    resp = client.get("/v1/osm/graph", params={"bbox": BBOX, "mode": "walk"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["graph_version"] == VERSION
    assert body["mode"] == "walk"
    assert body["node_count"] == len(body["nodes"]) == 27
    assert body["edge_count"] == len(body["edges"]) == 41


@pytest.mark.parametrize("bbox", ["1,2,3", "a,b,c,d", "106.9,-6.2,106.8,-6.1", "106.8,-95,106.9,-6.1"])
def test_bad_bbox_is_400(client, bbox):
    assert_error(client.get("/v1/osm/graph", params={"bbox": bbox, "mode": "walk"}), 400, "invalid_request")


def test_too_large_bbox_is_400(client):
    resp = client.get("/v1/osm/roads", params={"bbox": "106.68,-6.38,106.98,-6.08"})
    assert_error(resp, 400, "invalid_request")
    assert "terlalu luas" in resp.json()["error"]["message"]


def test_unknown_mode_is_400(client):
    assert_error(client.get("/v1/osm/graph", params={"bbox": BBOX, "mode": "car"}), 400, "invalid_request")


def test_roads_geojson(client):
    resp = client.get("/v1/osm/roads", params={"bbox": BBOX, "highway": ["primary", "secondary"]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/geo+json")
    assert resp.headers["X-Graph-Version"] == VERSION
    assert resp.json()["type"] == "FeatureCollection"


def test_roads_unknown_highway_is_400(client):
    assert_error(client.get("/v1/osm/roads", params={"bbox": BBOX, "highway": "rocket"}), 400, "invalid_request")


def test_nearest_node(client):
    resp = client.get("/v1/osm/nearest-node", params={"lon": ORIGIN_LON, "lat": ORIGIN_LAT, "mode": "walk"})
    assert resp.status_code == 200
    assert resp.json()["node_id"] == node_id(0, 0)


def test_nearest_node_outside_study_area_is_422(client, repo):
    resp = client.get("/v1/osm/nearest-node", params={"lon": 106.55, "lat": -5.75, "mode": "walk"})
    assert_error(resp, 422, "outside_study_area")
    assert "find_nearest_node" not in repo.calls


def test_nearest_node_none_is_point_not_on_network(client):
    resp = client.get("/v1/osm/nearest-node", params={"lon": ORIGIN_LON, "lat": -6.10, "mode": "walk"})
    assert_error(resp, 422, "point_not_on_network")


def test_nearest_edge(client):
    resp = client.get("/v1/osm/nearest-edge", params={"lon": ORIGIN_LON, "lat": ORIGIN_LAT, "mode": "bike"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["edge_id"] == 1
    assert body["mode"] == "bike"
    assert body["snapped_point"] == {"lat": ORIGIN_LAT, "lon": ORIGIN_LON + 0.00025}


def test_node_and_404(client):
    assert client.get(f"/v1/osm/nodes/{node_id(0, 0)}").json()["tags"] == {"highway": "traffic_signals"}
    assert_error(client.get("/v1/osm/nodes/42"), 404, "not_found")


def test_neighbors(client):
    resp = client.get(f"/v1/osm/nodes/{node_id(0, 0)}/neighbors", params={"mode": "walk"})
    assert resp.status_code == 200
    assert [n["next_node"] for n in resp.json()["neighbors"]] == [node_id(0, 1), node_id(1, 0)]
    assert_error(client.get("/v1/osm/nodes/42/neighbors", params={"mode": "walk"}), 404, "not_found")


def test_edge_and_404(client):
    body = client.get("/v1/osm/edges/1").json()
    assert body["tags"]["lanes"] == "2"
    assert body["geometry"]["type"] == "LineString"
    assert_error(client.get("/v1/osm/edges/777"), 404, "not_found")


def test_streets(client):
    resp = client.get("/v1/osm/streets", params={"q": "sudirman"})
    assert resp.status_code == 200
    assert resp.json()["results"][0]["name"] == "Jalan Jenderal Sudirman"
    assert_error(client.get("/v1/osm/streets", params={"q": "ab"}), 400, "invalid_request")


@pytest.mark.parametrize("q", ["%%%", "a b c", "jl.", "___", "  ab  "])
def test_streets_without_three_letters_is_400(client, repo, q):
    assert_error(client.get("/v1/osm/streets", params={"q": q}), 400, "invalid_request")
    assert "search_streets" not in repo.calls


@pytest.mark.parametrize("q", ["Jl. Sudirman", "kh mas mansyur", "123"])
def test_streets_accepts_real_queries(client, q):
    assert client.get("/v1/osm/streets", params={"q": q}).status_code == 200


def test_subgraph(client):
    body = {
        "origin": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
        "destination": {"lat": ORIGIN_LAT + 4 * STEP_DEG, "lon": ORIGIN_LON + 4 * STEP_DEG},
        "mode": "walk",
    }
    resp = client.post("/v1/osm/subgraph", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["origin_node"] == node_id(0, 0)
    assert out["destination_node"] == node_id(4, 4)
    assert out["node_count"] == 25 and out["graph"] is None


def test_subgraph_disconnected_is_422(client):
    body = {
        "origin": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
        "destination": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON + 0.010},
        "mode": "walk",
    }
    assert_error(client.post("/v1/osm/subgraph", json=body), 422, "no_route_found")


def test_subgraph_destination_outside_study_area(client):
    body = {"origin": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
            "destination": {"lat": -6.5, "lon": 106.8}, "mode": "bike"}
    resp = client.post("/v1/osm/subgraph", json=body)
    assert_error(resp, 422, "outside_study_area")
    assert "tujuan" in resp.json()["error"]["message"]


def test_unknown_path_uses_error_schema(client):
    assert_error(client.get("/v1/osm/nope"), 404, "not_found")


# Edge cases --------------------------------------------------------------------

BIGINT_OVERFLOW = 2**63


@pytest.mark.parametrize("path", [f"/v1/osm/nodes/{BIGINT_OVERFLOW}", f"/v1/osm/edges/{BIGINT_OVERFLOW}",
                                  "/v1/osm/nodes/0", "/v1/osm/edges/-1", "/v1/osm/nodes/abc"])
def test_out_of_range_ids_are_400(client, repo, path):
    assert_error(client.get(path), 400, "invalid_request")
    assert not {"get_node", "get_edge"} & set(repo.calls)


def test_wrong_method_is_405(client):
    assert_error(client.post("/v1/osm/version"), 405, "method_not_allowed")


def test_malformed_request_id_is_replaced(client):
    resp = client.get("/v1/osm/version", headers={"X-Request-ID": "bad id with spaces"})
    uuid.UUID(resp.headers["X-Request-ID"], version=4)


@pytest.mark.parametrize("limit", [0, 21])
def test_street_limit_bounds(client, limit):
    assert_error(client.get("/v1/osm/streets", params={"q": "sudirman", "limit": limit}), 400, "invalid_request")


@pytest.mark.parametrize("radius", [0, 1001])
def test_snap_radius_bounds(client, radius):
    params = {"lon": ORIGIN_LON, "lat": ORIGIN_LAT, "mode": "walk", "max_distance_m": radius}
    assert_error(client.get("/v1/osm/nearest-node", params=params), 400, "invalid_request")


@pytest.mark.parametrize("change", [
    {"buffer_m": 20000},
    {"mode": "car"},
    {"origin": {"lat": 106.8, "lon": -6.2}},
    {"destination": None},
])
def test_subgraph_invalid_body_is_400(client, repo, change):
    body = {"origin": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
            "destination": {"lat": ORIGIN_LAT + STEP_DEG, "lon": ORIGIN_LON}, "mode": "walk", **change}
    body = {k: v for k, v in body.items() if v is not None}
    assert_error(client.post("/v1/osm/subgraph", json=body), 400, "invalid_request")
    assert "read_road_graph" not in repo.calls


def test_subgraph_same_origin_and_destination(client):
    point = {"lat": ORIGIN_LAT, "lon": ORIGIN_LON}
    out = client.post("/v1/osm/subgraph", json={"origin": point, "destination": point, "mode": "walk"}).json()
    assert out["origin_node"] == out["destination_node"] == node_id(0, 0)


def test_origin_outside_study_area_names_origin(client):
    body = {"origin": {"lat": -5.75, "lon": 106.55}, "destination": {"lat": ORIGIN_LAT, "lon": ORIGIN_LON},
            "mode": "walk"}
    resp = client.post("/v1/osm/subgraph", json=body)
    assert_error(resp, 422, "outside_study_area")
    assert "asal" in resp.json()["error"]["message"]
