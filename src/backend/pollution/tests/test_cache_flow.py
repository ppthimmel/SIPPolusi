"""TI-SE-04: write with the Data Worker, read with the Backend, on a local PostGIS.

Writers are the real Data Worker functions: osm_cache.write_road_graph and
spatial_model.inference.cache.write_pollution_weight (TI-AI-05). Readers are the Backend
repositories (osm.repository, pollution.repository) and the HTTP API.
Run with TEST_DATABASE_URL pointing at src/backend/testdb.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import asyncpg
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
    from spatial_model.inference.cache import write_pollution_weight

    weights = [EdgeWeight(edge_id=e, time_window_start=start, pm25_ugm3=pm25, no2_ugm3=40.0,
                          exposure_index=0.5 * (pm25 / 15 + 40 / 25), confidence_score=0.3,
                          background_source=None, estimation_source="idw") for e in edge_ids]
    return write_pollution_weight(conn, weights, WorkerWindow.starting_at(start), graph_version, model,
                                  coverage_ratio=1.0, trace=trace_for_weights(weights, start, graph_version))


def trace_for_weights(weights, start, graph_version):
    """One source cell per synthetic edge, shared by cache and public API fixtures."""
    import dataclasses, hashlib
    import numpy as np
    import pandas as pd
    from spatial_model.data.grid import GridSpec
    from spatial_model.inference.provenance import InferenceTrace
    spec = GridSpec(x0=700000., y0=9310000., nx=len(weights), ny=1)
    frame = pd.DataFrame([dataclasses.asdict(w) for w in weights])
    grid = frame[["pm25_ugm3", "no2_ugm3", "confidence_score"]].copy()
    grid["cell_id"] = np.arange(len(weights))
    grid["x"], grid["y"] = spec.cell_centers()
    grid["pm25_source"] = grid["no2_source"] = "idw"
    pieces = pd.DataFrame(dict(edge_id=frame.edge_id.to_numpy(), cell_id=np.arange(len(weights)), length_m=100.))
    fingerprint = hashlib.sha256(frame.to_json().encode()).hexdigest()
    return InferenceTrace(f"fixture-{start.isoformat()}-{graph_version}-{fingerprint}", grid, pieces,
                           spec.as_dict(), fingerprint, {}, {})


def test_additive_provenance_migration_preserves_historical_rows(writer):
    """Upgrade a cache without metadata, twice, preserving existing concentrations."""
    from pathlib import Path
    write_graph(writer, "migration-fixture")
    write_weights(writer, W1, [1], "migration-fixture", pm25=27.0)
    writer.execute("ALTER TABLE pollution.edge_pollution DROP COLUMN model_metadata")
    migration = (Path(__file__).resolve().parents[3] / "data_worker/database/spatial_migration.sql").read_text(encoding="utf-8")
    for _ in range(2):
        with writer.transaction():
            writer.execute(migration)
    row = writer.execute("SELECT pm25_ugm3, model_metadata FROM pollution.edge_pollution WHERE edge_id=1").fetchone()
    assert row == (27.0, {})
    writer.execute("UPDATE pollution.edge_pollution SET estimation_source='hybrid' WHERE edge_id=1")


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
            run_id = body["weights"][0]["trace_run_id"]
            assert run_id
            trace_response = client.get("/v1/pollution/edge-weights/1/trace")
            assert trace_response.status_code == 200
            trace = trace_response.json()
            assert trace["status"] == "available" and trace["run_id"] == run_id
            assert trace["manifest"]["graph_version"] == "v1"
            assert trace["cells"][0]["pm25_ugm3"] == 20.
            assert trace["cells"][0]["length_m"] == 100.
            assert body["time_window_start"] == "2026-10-13T08:00:00Z"
            windows = client.get("/v1/pollution/windows").json()
            assert windows["latest_complete"]["status"] == "complete"
            health = client.get("/health").json()
            assert health["graph_version"] == "v1" and health["pollution_time_window"] == "2026-10-13T08:00:00Z"
    finally:
        get_settings.cache_clear()
        get_pollution_settings.cache_clear()


def test_http_api_before_the_worker_created_the_schemas(bare_dsn, monkeypatch):
    """Fresh environment: the database answers but has no osm/pollution schema yet.

    /health must report degraded (the image HEALTHCHECK depends on it) and reads
    answer 503 cache_empty, not 500 UndefinedTableError.
    """
    from fastapi.testclient import TestClient

    from osm.app import create_app

    monkeypatch.setenv("DATABASE_URL", bare_dsn)
    get_settings.cache_clear()
    get_pollution_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            health = client.get("/health")
            assert health.status_code == 200
            assert (health.json()["status"], health.json()["database"]) == ("degraded", "ok")
            for path, params in [("/v1/osm/version", None), ("/v1/pollution/windows", None),
                                 ("/v1/pollution/edge-weights", {"bbox": "106.79,-6.21,106.81,-6.19"})]:
                resp = client.get(path, params=params)
                assert resp.status_code == 503, (path, resp.text)
                error = resp.json()["error"]
                assert (error["code"], error["reason"]) == ("cache_unavailable", "cache_empty"), path
    finally:
        get_settings.cache_clear()
        get_pollution_settings.cache_clear()


async def test_missing_table_in_existing_schemas_is_not_cache_empty(writer, backend):
    """The schemas exist but a table is gone (renamed table, partial migration): a deployment
    bug that must stay an error, not look like a cache the Data Worker has not filled yet."""
    osm, pollution, _ = backend
    writer.execute("DROP TABLE pollution.pollution_window CASCADE")
    with pytest.raises(asyncpg.UndefinedTableError):
        await pollution.latest_complete_window()
    # The other schema is untouched, so its reads still work (an empty cache answers cache_empty).
    with pytest.raises(CacheUnavailable) as err:
        await osm.get_active_version()
    assert err.value.reason == CacheUnavailable.CACHE_EMPTY


async def test_legacy_cache_readable_without_claiming_cell_trace(writer, backend):
    _, pollution, _ = backend
    write_graph(writer, "legacy")
    write_weights(writer, W2, [1], "legacy")
    writer.execute("UPDATE pollution.edge_pollution SET trace_run_id = NULL")
    result = await pollution.read_pollution_weight(BBOX_ALL)
    assert len(result.weights) == 1 and result.weights[0].trace_run_id is None
    trace = await pollution.read_edge_trace(1)
    assert trace["status"] == "legacy_unavailable" and trace["cells"] == []


async def test_route_read_without_lineage_matches_full_read(writer, cache_dsn):
    write_graph(writer, "v1", footway_edge=0)
    write_weights(writer, W2, range(1, 25), "v1")
    db = OsmDatabase(OsmSettings(_env_file=None, DATABASE_URL=cache_dsn))
    try:
        pollution = PollutionRepository(db, OsmRepository(db, version_cache_ttl_s=0), clock=lambda: NOW,
                                        weights_work_mem="32MB")
        full = await pollution.read_pollution_weight(BBOX_ALL, mode=Mode.BIKE)
        lean = await pollution.read_pollution_weight(BBOX_ALL, mode=Mode.BIKE, lineage=False)
        assert full.weights[0].model_metadata and full.weights[0].trace_run_id and full.weights[0].written_at

        def without_lineage(w):
            return dataclasses.replace(w, written_at=None, model_metadata={}, trace_run_id=None)

        assert [without_lineage(w) for w in full.weights] == lean.weights
        assert lean.excluded == full.excluded == {"not_allowed_for_mode": 1}
        # SET LOCAL work_mem ends with the read's transaction; the pooled connection keeps the default.
        async with db.connection() as conn:
            assert await conn.fetchval("SHOW work_mem") != "32MB"
    finally:
        await db.close()
