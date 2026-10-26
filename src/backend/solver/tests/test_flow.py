"""TI-SE-06 end to end on a local PostGIS: the Data Worker writes the road graph and a
pollution window, POST /v1/routes/solve returns routes with geometry from the database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from osm.config import get_settings
from planner.tests.test_flow import body
from pollution.config import get_pollution_settings
from pollution.tests.conftest import admin_dsn, cache_dsn, writer  # noqa: F401  (fixtures)
from pollution.tests.test_cache_flow import write_graph, write_weights
from solver.config import get_solver_settings

pytestmark = pytest.mark.cacheflow


@pytest.fixture
def client_for(cache_dsn, monkeypatch):
    from fastapi.testclient import TestClient

    from osm.app import create_app

    def make(provider):
        monkeypatch.setenv("DATABASE_URL", cache_dsn)
        monkeypatch.setenv("ROUTE_SOLVER_PROVIDER", provider)
        for cached in (get_settings, get_pollution_settings, get_solver_settings):
            cached.cache_clear()
        return TestClient(create_app())

    yield make
    for cached in (get_settings, get_pollution_settings, get_solver_settings):
        cached.cache_clear()


@pytest.mark.parametrize("provider", ["mock", "baseline"])
def test_solve_on_data_written_by_the_worker(writer, client_for, provider):
    write_graph(writer, "v1")
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    write_weights(writer, now - timedelta(hours=1), range(1, 25), "v1")
    with client_for(provider) as client:
        resp = client.post("/v1/routes/solve", json=body())
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["solver"] == provider and out["context"]["graph_version"] == "v1"
    rec = out["recommended"]
    # Grid 4 x 4 from (0,0) to (3,3): 6 edges of 110 m, geometry from osm.road_edge.
    assert rec["metrics"]["edge_count"] == 6 and rec["metrics"]["distance_m"] == pytest.approx(660.0)
    assert rec["geometry"]["coordinates"][0] == [106.8, -6.2]
    assert rec["geometry"]["coordinates"][-1] == pytest.approx([106.803, -6.197])
    assert rec["metrics"]["exposure_ug_min_m3"] is not None


def test_solve_reports_empty_pollution_cache(writer, client_for):
    write_graph(writer, "v1")
    with client_for("mock") as client:
        error = client.post("/v1/routes/solve", json=body()).json()["error"]
    assert (error["code"], error["reason"]) == ("cache_unavailable", "cache_empty")
