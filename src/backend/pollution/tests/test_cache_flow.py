"""TI-SE-04: write with the Data Worker, read with the Backend, on a local PostGIS.

Writers are the real Data Worker functions: osm_cache.write_road_graph and
spatial_model.cache.write_pollution_weight (TI-AI-05). Readers are the Backend
repositories (osm.repository, pollution.repository) and the HTTP API.
Run with TEST_DATABASE_URL pointing at src/backend/testdb.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from osm.config import OsmSettings, get_settings
from osm.db import OsmDatabase
from osm.errors import CacheUnavailable, NotFound
from osm.models import BBox, Mode
from osm.repository import OsmRepository
from pollution.config import get_pollution_settings
from pollution.models import TimeWindow
from pollution.repository import PollutionRepository

pytestmark = pytest.mark.cacheflow

UTC = timezone.utc
LON0, LAT0, STEP = 106.80, -6.20, 0.001
BBOX_ALL = BBox(106.79, -6.21, 106.81, -6.19)
NOW = datetime(2026, 10, 13, 9, 30, tzinfo=UTC)
W1 = datetime(2026, 10, 13, 7, tzinfo=UTC)
W2 = datetime(2026, 10, 13, 8, tzinfo=UTC)


# ---------------------------------------------------------------- Data Worker side

def _graph(version, first_edge_id=1, size=4, footway_edge=None):
    """size x size grid; footway_edge (index) becomes a walk-only footway."""
    from osm_cache import GraphEdge, GraphNode, RoadGraphData

    nodes = [GraphNode(r * size + c + 1, LON0 + c * STEP, LAT0 + r * STEP) for r in range(size) for c in range(size)]
    by_id = {n.node_id: n for n in nodes}
    edges = []
    for r in range(size):
        for c in range(size):
            a = r * size + c + 1
            for b in ([a + 1] if c < size - 1 else []) + ([a + size] if r < size - 1 else []):
                i = len(edges)
                foot = i == footway_edge
                edges.append(GraphEdge(first_edge_id + i, a, b, 110.0, "footway" if foot else "residential",
                                       True, not foot, ((by_id[a].lon, by_id[a].lat), (by_id[b].lon, by_id[b].lat))))
    return RoadGraphData(version, nodes, edges, osm_timestamp=W1)


def write_graph(conn, version, **kw):
    from osm_cache import write_road_graph

    return write_road_graph(conn, _graph(version, **kw))


def write_weights(conn, start, edge_ids, graph_version, pm25=20.0, model="idw-p2-k8"):
    from contracts import EdgeWeight, TimeWindow as WorkerWindow
    from spatial_model.cache import write_pollution_weight

    weights = [EdgeWeight(edge_id=e, time_window_start=start, pm25_ugm3=pm25, no2_ugm3=40.0,
                          exposure_index=0.5 * (pm25 / 15 + 40 / 25), confidence_score=0.3,
                          background_source=None, estimation_source="idw") for e in edge_ids]
    return write_pollution_weight(conn, weights, WorkerWindow.starting_at(start), graph_version, model,
                                  coverage_ratio=1.0)


# ---------------------------------------------------------------- Backend side

@pytest.fixture
async def backend(cache_dsn):
    db = OsmDatabase(OsmSettings(_env_file=None, DATABASE_URL=cache_dsn))
    osm = OsmRepository(db, version_cache_ttl_s=0)  # see version switches immediately
    pollution = PollutionRepository(db, osm, clock=lambda: NOW)
    yield osm, pollution, db
    await db.close()


# ---------------------------------------------------------------- OSM Cache DB

async def test_graph_written_by_worker_is_read_by_backend(writer, backend):
    osm, _, _ = backend
    write_graph(writer, "jakarta-20261001", footway_edge=0)
    walk = await osm.read_road_graph(BBOX_ALL, Mode.WALK)
    bike = await osm.read_road_graph(BBOX_ALL, Mode.BIKE)
    assert walk.graph_version == "jakarta-20261001"
    assert len(walk.edges) == 24 and len(bike.edges) == 23 and walk.rejected == bike.rejected == {}
    assert 1 not in bike.edges_by_id()  # walk-only footway
    # Spatial filter: a bbox around the first column only.
    column = await osm.read_road_graph(BBox(106.7995, -6.2005, 106.8005, -6.1965), Mode.WALK)
    assert 0 < len(column.edges) < 24


async def test_backend_follows_graph_version_switch(writer, backend):
    osm, _, _ = backend
    write_graph(writer, "v1")
    assert (await osm.get_active_version()).version == "v1"
    write_graph(writer, "v2", first_edge_id=1000)
    assert (await osm.get_active_version()).version == "v2"
    graph = await osm.read_road_graph(BBOX_ALL, Mode.WALK)
    assert min(graph.edges_by_id()) == 1000


# ---------------------------------------------------------------- Spatial Pollution Cache DB

async def test_empty_cache_is_cache_empty(writer, backend):
    _, pollution, _ = backend
    write_graph(writer, "v1")
    with pytest.raises(CacheUnavailable) as exc:
        await pollution.read_pollution_weight(BBOX_ALL)
    assert exc.value.reason == CacheUnavailable.CACHE_EMPTY
    # A window still being written is not readable either.
    writer.execute("INSERT INTO pollution.pollution_window (time_window_start, status) VALUES (%s, 'writing')", (W2,))
    with pytest.raises(CacheUnavailable) as exc:
        await pollution.read_pollution_weight(BBOX_ALL)
    assert exc.value.reason == CacheUnavailable.CACHE_EMPTY
    with pytest.raises(CacheUnavailable):
        await pollution.read_pollution_weight(BBOX_ALL, TimeWindow(W2))
    with pytest.raises(NotFound):
        await pollution.read_pollution_weight(BBOX_ALL, TimeWindow(W1))


async def test_weights_written_by_worker_are_read_by_backend(writer, backend):
    _, pollution, _ = backend
    write_graph(writer, "v1")
    assert write_weights(writer, W1, range(1, 25), "v1", pm25=30.0) == 24
    assert write_weights(writer, W2, range(1, 25), "v1", pm25=20.0) == 24

    latest = await pollution.read_pollution_weight(BBOX_ALL)
    assert latest.window.time_window_start == W2 and latest.window.status == "complete"
    assert len(latest.weights) == 24 and latest.excluded == {}
    w = latest.weights_by_edge_id()[1]
    assert (w.pm25_ugm3, w.no2_ugm3, w.estimation_source, w.graph_version, w.model_version) == \
        (20.0, 40.0, "idw", "v1", "idw-p2-k8")
    assert latest.data_stale is False and latest.age_s == 30 * 60

    older = await pollution.read_pollution_weight(BBOX_ALL, TimeWindow(W1))
    assert older.weights_by_edge_id()[1].pm25_ugm3 == 30.0

    corner = await pollution.read_pollution_weight(BBox(106.7995, -6.2005, 106.8005, -6.1965))
    assert 0 < len(corner.weights) < 24


async def test_upsert_does_not_duplicate(writer, backend):
    _, pollution, _ = backend
    write_graph(writer, "v1")
    write_weights(writer, W2, range(1, 25), "v1", pm25=20.0)
    write_weights(writer, W2, range(1, 25), "v1", pm25=25.0)  # same key (edge_id, time_window_start)
    rows, windows = writer.execute(
        "SELECT (SELECT count(*) FROM pollution.edge_pollution), (SELECT count(*) FROM pollution.pollution_window)"
    ).fetchone()
    assert (rows, windows) == (24, 1)
    read = await pollution.read_pollution_weight(BBOX_ALL)
    assert {w.pm25_ugm3 for w in read.weights} == {25.0}


async def test_weights_filtered_by_mode(writer, backend):
    _, pollution, _ = backend
    write_graph(writer, "v1", footway_edge=0)
    write_weights(writer, W2, range(1, 25), "v1")
    bike = await pollution.read_pollution_weight(BBOX_ALL, mode=Mode.BIKE)
    walk = await pollution.read_pollution_weight(BBOX_ALL, mode=Mode.WALK)
    assert len(walk.weights) == 24
    assert len(bike.weights) == 23 and bike.excluded == {"not_allowed_for_mode": 1}


async def test_weights_of_an_older_graph_version_are_excluded(writer, backend):
    _, pollution, _ = backend
    write_graph(writer, "v1")
    write_weights(writer, W2, range(1, 25), "v1")
    write_graph(writer, "v2", first_edge_id=1000)  # new edge ids, pollution not re-estimated yet
    active = await pollution.read_pollution_weight(BBOX_ALL)
    assert active.graph_version == "v2" and active.weights == []
    assert active.excluded == {"other_graph_version": 24}
    anyv = await pollution.read_pollution_weight(BBOX_ALL, graph_version=None)
    assert len(anyv.weights) == 24
    v1 = await pollution.read_pollution_weight(BBOX_ALL, graph_version="v1")
    assert len(v1.weights) == 24


async def test_stale_window_is_flagged(writer, cache_dsn):
    write_graph(writer, "v1")
    write_weights(writer, W2, range(1, 25), "v1")  # ends 09:00
    db = OsmDatabase(OsmSettings(_env_file=None, DATABASE_URL=cache_dsn))
    osm = OsmRepository(db)
    try:
        fresh = PollutionRepository(db, osm, clock=lambda: W2 + timedelta(hours=3, minutes=59))
        stale = PollutionRepository(db, osm, clock=lambda: W2 + timedelta(hours=4, minutes=1))
        assert (await fresh.read_pollution_weight(BBOX_ALL)).data_stale is False
        read = await stale.read_pollution_weight(BBOX_ALL)
        assert read.data_stale is True and len(read.weights) == 24  # still served (Tabel 3.15)
    finally:
        await db.close()


async def test_database_failures_have_their_own_reason(cache_dsn):
    down = OsmDatabase(OsmSettings(_env_file=None, DATABASE_URL="postgresql://nobody:x@127.0.0.1:1/none",
                                   connect_timeout_s=2))
    with pytest.raises(CacheUnavailable) as exc:
        await PollutionRepository(down, OsmRepository(down)).read_pollution_weight(BBOX_ALL)
    assert exc.value.reason == CacheUnavailable.DATABASE_UNAVAILABLE

    slow = OsmDatabase(OsmSettings(_env_file=None, DATABASE_URL=cache_dsn, statement_timeout_ms=100))
    try:
        with pytest.raises(CacheUnavailable) as exc:
            async with slow.connection() as conn:
                await conn.execute("SELECT pg_sleep(1)")
        assert exc.value.reason == CacheUnavailable.QUERY_TIMEOUT
    finally:
        await slow.close()


# ---------------------------------------------------------------- HTTP API end to end

def test_http_api_reads_what_the_worker_wrote(writer, cache_dsn, monkeypatch):
    from fastapi.testclient import TestClient

    from osm.app import create_app

    monkeypatch.setenv("DATABASE_URL", cache_dsn)
    get_settings.cache_clear()
    get_pollution_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            health = client.get("/health").json()
            assert (health["status"], health["database"], health["graph_version"]) == ("degraded", "ok", None)
            empty = client.get("/v1/osm/version").json()["error"]
            assert empty["code"] == "cache_unavailable" and empty["reason"] == "cache_empty"

            write_graph(writer, "v1")
            assert client.get("/v1/osm/version").json()["version"] == "v1"
            empty = client.get("/v1/pollution/edge-weights", params={"bbox": "106.79,-6.21,106.81,-6.19"}).json()
            assert empty["error"]["reason"] == "cache_empty"

            write_weights(writer, W2, range(1, 25), "v1")
            body = client.get("/v1/pollution/edge-weights",
                              params={"bbox": "106.79,-6.21,106.81,-6.19", "mode": "walk"}).json()
            assert body["count"] == 24 and body["graph_version"] == "v1"
            assert body["time_window_start"] == "2026-10-13T08:00:00Z"
            windows = client.get("/v1/pollution/windows").json()
            assert windows["latest_complete"]["status"] == "complete"
            health = client.get("/health").json()
            assert health["graph_version"] == "v1" and health["pollution_time_window"] == "2026-10-13T08:00:00Z"
    finally:
        get_settings.cache_clear()
        get_pollution_settings.cache_clear()
