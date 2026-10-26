"""TI-SE-05 end to end on a local PostGIS: the Data Worker writes the road graph and a
partial pollution window, the Backend turns a RouteRequest into a solver input."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from osm.config import get_settings
from pollution.config import get_pollution_settings
from pollution.tests.conftest import admin_dsn, cache_dsn, writer  # noqa: F401  (fixtures)
from pollution.tests.test_cache_flow import LAT0, LON0, STEP, write_graph, write_weights

pytestmark = pytest.mark.cacheflow

UTC = timezone.utc


def body(mode="walk", dest=(3, 3)):
    r, c = dest
    return {"origin": {"lat": LAT0, "lon": LON0}, "destination": {"lat": LAT0 + r * STEP, "lon": LON0 + c * STEP},
            "mode": mode, "pollutant": "pm25", "preference": {"alpha": 0.5, "beta": 0.5}}


@pytest.fixture
def client(cache_dsn, monkeypatch):
    from fastapi.testclient import TestClient

    from osm.app import create_app

    monkeypatch.setenv("DATABASE_URL", cache_dsn)
    get_settings.cache_clear()
    get_pollution_settings.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    get_settings.cache_clear()
    get_pollution_settings.cache_clear()


def test_route_request_to_solver_input(writer, client):
    write_graph(writer, "v1", footway_edge=0)
    # No pollution window yet: explicit 503 cache_empty, not zero weights.
    resp = client.post("/v1/routes/solver-input", json=body())
    assert resp.status_code == 503 and resp.json()["error"]["reason"] == "cache_empty"

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    write_weights(writer, now - timedelta(hours=1), [i for i in range(1, 25) if i not in (5, 6)], "v1")  # partial

    walk = client.post("/v1/routes/solver-input", json=body()).json()
    assert walk["context"]["origin"]["node_id"] == 1 and walk["context"]["destination"]["node_id"] == 16
    assert walk["edge_count"] == 24 and walk["weights"]["missing_edge_count"] == 2
    imputed = {e["id"] for e in walk["edges"] if e["weight_source"] == "imputed_median"}
    assert imputed == {5, 6}
    assert walk["weights"]["data_stale"] is False and walk["source"]["graph_version"] == "v1"

    bike = client.post("/v1/routes/solver-input", json=body(mode="bike")).json()
    assert bike["edge_count"] == 23 and 1 not in {e["id"] for e in bike["edges"]}  # walk-only footway


def test_unreachable_and_unsnappable(writer, client):
    write_graph(writer, "v1")
    write_weights(writer, datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1),
                  range(1, 25), "v1")
    far = body()
    far["destination"] = {"lat": LAT0 + 0.05, "lon": LON0}  # inside the study area, no road nearby
    resp = client.post("/v1/routes/solver-input", json=far)
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "point_not_on_network"
