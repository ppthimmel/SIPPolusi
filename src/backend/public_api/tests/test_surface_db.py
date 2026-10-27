"""GET /v1/exposure-surface and POST /v1/routes end to end on a local PostGIS: the
Data Worker writes the road graph and a pollution window, the public API reads it."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from osm.config import get_settings
from osm.models import BBox
from planner.tests.test_flow import body as route_body
from pollution.config import get_pollution_settings
from pollution.tests.conftest import admin_dsn, cache_dsn, writer  # noqa: F401  (fixtures)
from pollution.tests.test_cache_flow import write_graph, write_weights, trace_for_weights
from public_api.config import get_api_settings
from public_api.schemas import RouteFeatureCollection, SurfaceFeatureCollection
from public_api.surface import Grid
from solver.config import get_solver_settings

pytestmark = pytest.mark.cacheflow

AROUND_GRID = "106.795,-6.205,106.808,-6.192"  # the 4 x 4 grid of write_graph and a margin
_CACHED = (get_settings, get_pollution_settings, get_solver_settings, get_api_settings)


@pytest.fixture
def client(cache_dsn, monkeypatch):
    from fastapi.testclient import TestClient

    from osm.app import create_app

    monkeypatch.setenv("DATABASE_URL", cache_dsn)
    monkeypatch.setenv("ROUTE_SOLVER_PROVIDER", "mock")
    for cached in _CACHED:
        cached.cache_clear()
    with TestClient(create_app()) as c:
        yield c
    for cached in _CACHED:
        cached.cache_clear()


def _window() -> datetime:
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)


def _write_varied(writer):
    """One complete window where PM2.5 = 5 µg/m³ × edge_id (NO2 missing on edge 24), so cell
    averages differ from cell to cell. A window is written whole: the Data Worker replaces it."""
    from contracts import EdgeWeight, TimeWindow as WorkerWindow
    from spatial_model.inference.cache import write_pollution_weight

    start = _window()
    weights = []
    for e in range(1, 25):
        pm25, no2 = 5.0 * e, (None if e == 24 else 40.0)
        index = 0.5 * (pm25 / 15 + no2 / 25) if no2 is not None else pm25 / 15
        weights.append(EdgeWeight(edge_id=e, time_window_start=start, pm25_ugm3=pm25, no2_ugm3=no2,
                                  exposure_index=index, confidence_score=round(0.02 * e, 2),
                                  background_source=None, estimation_source="idw"))
    write_pollution_weight(writer, weights, WorkerWindow.starting_at(start), "v1", "idw-p2-k8", coverage_ratio=1.0,
                           trace=trace_for_weights(weights, start, "v1"))


def _surface(client, zoom, bbox=AROUND_GRID):
    resp = client.get("/v1/exposure-surface", params={"bbox": bbox, "zoom": zoom})
    assert resp.status_code == 200, resp.text
    return SurfaceFeatureCollection.model_validate(resp.json()), resp.json()


def test_edges_at_zoom_15(writer, client):
    write_graph(writer, "v1")
    _write_varied(writer)
    fc, raw = _surface(client, 15)
    assert fc.metadata.kind == "edge" and fc.metadata.graph_version == "v1" and fc.metadata.feature_count == 24
    by_id = {f["properties"]["edge_id"]: f for f in raw["features"]}
    assert set(by_id) == set(range(1, 25))
    assert by_id[1]["properties"]["pm25_ugm3"] == 5.0 and by_id[24]["properties"]["pm25_ugm3"] == 120.0
    assert by_id[24]["properties"]["no2_ugm3"] is None  # missing stays null, never zero (PF-06)
    assert by_id[1]["properties"]["exposure_index"] == pytest.approx(0.5 * (5 / 15 + 40 / 25), abs=1e-4)
    assert by_id[1]["geometry"]["coordinates"] == [[106.8, -6.2], [106.801, -6.2]]  # from edge_pollution.geom
    # A smaller bbox returns only the edges it touches.
    corner, _ = _surface(client, 16, "106.7995,-6.2005,106.8005,-6.1995")
    assert 0 < corner.metadata.feature_count < 24


@pytest.mark.parametrize("zoom", [13, 12, 11])
def test_grid_cells_match_an_independent_aggregation(writer, client, zoom):
    write_graph(writer, "v1")
    _write_varied(writer)
    _, edges = _surface(client, 15)
    fc, raw = _surface(client, zoom)
    assert fc.metadata.kind == "grid" and fc.metadata.cell_size_m == {13: 250, 12: 500, 11: 1000}[zoom]

    # Expected: each edge in the cell holding the centre of its bounding box, length-weighted means.
    grid = Grid.covering(BBox.parse(AROUND_GRID), fc.metadata.cell_size_m)
    cells: dict[str, list] = defaultdict(list)
    for f in edges["features"]:
        (x1, y1), (x2, y2) = f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][-1]
        ix, iy = math.floor((min(x1, x2) + max(x1, x2)) / 2 / grid.dlon), math.floor((min(y1, y2) + max(y1, y2)) / 2 / grid.dlat)
        cells[f"{grid.cell_m}m:{ix}:{iy}"].append((math.hypot(x2 - x1, y2 - y1), f["properties"]))
    got = {f["properties"]["cell_id"]: f["properties"] for f in raw["features"]}
    assert set(got) == set(cells)
    for cell_id, members in cells.items():
        assert got[cell_id]["edge_count"] == len(members)
        for key in ("exposure_index", "pm25_ugm3", "no2_ugm3", "confidence_score"):
            known = [(w, p[key]) for w, p in members if p[key] is not None]  # averaged over edges with a value
            expected = sum(w * v for w, v in known) / sum(w for w, _ in known) if known else None
            assert got[cell_id][key] == (pytest.approx(expected, abs=1e-3) if known else None)
        assert got[cell_id]["exposure_index_max"] == pytest.approx(max(p["exposure_index"] for _, p in members), abs=1e-4)
    assert sum(p["edge_count"] for p in got.values()) == 24


def test_only_the_active_graph_and_complete_windows_are_shown(writer, client):
    write_graph(writer, "v1")
    write_weights(writer, _window(), range(1, 25), "v1")
    write_graph(writer, "v2", first_edge_id=1000)  # pollution not re-estimated for v2 yet
    fc, _ = _surface(client, 15)
    assert fc.metadata.graph_version == "v2" and fc.metadata.feature_count == 0
    writer.execute("UPDATE pollution.pollution_window SET status = 'writing'")
    error = client.get("/v1/exposure-surface", params={"bbox": AROUND_GRID, "zoom": 15}).json()["error"]
    assert (error["code"], error["reason"]) == ("cache_unavailable", "cache_empty")


def test_route_on_worker_data(writer, client):
    write_graph(writer, "v1")
    write_weights(writer, _window(), range(1, 25), "v1")
    resp = client.post("/v1/routes", json=route_body())
    assert resp.status_code == 200, resp.text
    fc = RouteFeatureCollection.model_validate(resp.json())
    assert fc.metadata.graph_version == "v1" and fc.features[0].geometry.coordinates[0] == [106.8, -6.2]
    assert fc.features[0].properties.distance_m == pytest.approx(660.0)
