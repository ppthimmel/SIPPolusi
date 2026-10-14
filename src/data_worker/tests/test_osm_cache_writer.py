"""write_road_graph pada PostgreSQL + PostGIS sungguhan (UT-OPS-04, IT-K-09).

Butuh TEST_DATABASE_URL ke basis data lokal (lihat src/backend/testdb) atau
Testcontainers. Setiap uji memakai schema sementara; host selain localhost
ditolak agar uji tulis tidak pernah menyentuh basis data bersama.
"""

from __future__ import annotations

import datetime as dt
import uuid
from urllib.parse import urlparse

import pytest

from osm_cache import (
    GraphCheckFailed,
    GraphEdge,
    GraphNode,
    GraphVersionExists,
    RoadGraphData,
    largest_component_ratio,
    next_edge_id,
    schema_sql,
    write_road_graph,
)

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
LON0, LAT0, STEP = 106.80, -6.20, 0.001


def _grid(version: str, size: int = 4, first_edge_id: int = 1, node_offset: int = 0) -> RoadGraphData:
    """size x size grid; nodes offset so two versions can share or differ in node ids."""
    nodes = [GraphNode(node_offset + r * size + c + 1, LON0 + c * STEP, LAT0 + r * STEP)
             for r in range(size) for c in range(size)]
    by_id = {n.node_id: n for n in nodes}
    edges, eid = [], first_edge_id
    for r in range(size):
        for c in range(size):
            a = node_offset + r * size + c + 1
            for b in ([a + 1] if c < size - 1 else []) + ([a + size] if r < size - 1 else []):
                na, nb = by_id[a], by_id[b]
                edges.append(GraphEdge(eid, a, b, 110.0, "residential", True, True,
                                       ((na.lon, na.lat), (nb.lon, nb.lat)), name="Jalan Uji"))
                eid += 1
    return RoadGraphData(version, nodes, edges, osm_timestamp=dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc))


@pytest.fixture
def osm_db(pg_dsn):
    import psycopg

    if urlparse(pg_dsn).hostname not in LOCAL_HOSTS:
        pytest.skip("uji tulis hanya boleh ke basis data lokal")
    suffix = uuid.uuid4().hex[:10]
    osm, pollution = f"o_{suffix}", f"p_{suffix}"
    conn = psycopg.connect(pg_dsn, autocommit=True)
    conn.execute(schema_sql(osm, pollution))
    yield conn, osm
    conn.execute(f'DROP SCHEMA IF EXISTS "{osm}" CASCADE')
    conn.execute(f'DROP SCHEMA IF EXISTS "{pollution}" CASCADE')
    conn.close()


def _versions(conn, osm):
    return dict(conn.execute(f'SELECT version, status FROM "{osm}".graph_version ORDER BY version').fetchall())


def _count(conn, osm, table, version):
    return conn.execute(f'SELECT count(*) FROM "{osm}".{table} WHERE graph_version = %s', (version,)).fetchone()[0]


def test_largest_component_ratio():
    g = _grid("v", size=3)
    assert largest_component_ratio(g.nodes, g.edges) == 1.0
    island = GraphNode(999, LON0 + 1, LAT0)
    assert largest_component_ratio(g.nodes + [island], g.edges) == pytest.approx(9 / 10)
    assert largest_component_ratio([], []) == 0.0


def test_ut_ops_04a_first_and_next_version(osm_db):
    conn, osm = osm_db
    first = write_road_graph(conn, _grid("jakarta-20261001"), osm_schema=osm)
    assert first.retired_version is None and first.largest_component_ratio == 1.0
    assert _versions(conn, osm) == {"jakarta-20261001": "active"}

    second = write_road_graph(conn, _grid("jakarta-20261008", first_edge_id=next_edge_id(conn, osm)),
                              osm_schema=osm)
    assert second.retired_version == "jakarta-20261001"
    assert _versions(conn, osm) == {"jakarta-20261001": "retired", "jakarta-20261008": "active"}
    row = conn.execute(f'SELECT node_count, edge_count, built_at, osm_timestamp FROM "{osm}".graph_version '
                       "WHERE version = 'jakarta-20261008'").fetchone()
    assert row[0] == 16 and row[1] == 24 and row[2] is not None and row[3] is not None
    # Retired data stays readable for pollution rows that still refer to it.
    assert _count(conn, osm, "road_edge", "jakarta-20261001") == 24


def test_written_columns_round_trip(osm_db):
    conn, osm = osm_db
    g = _grid("v1")
    g.nodes[0] = GraphNode(g.nodes[0].node_id, g.nodes[0].lon, g.nodes[0].lat, {"highway": "traffic_signals"})
    write_road_graph(conn, g, osm_schema=osm)
    tags = conn.execute(f'SELECT tags FROM "{osm}".road_node WHERE node_id = 1').fetchone()[0]
    assert tags == {"highway": "traffic_signals"}
    e = conn.execute(f'SELECT u, v, length_m, name, oneway, ST_AsText(geom) FROM "{osm}".road_edge '
                     "WHERE edge_id = 1").fetchone()
    assert e[:5] == (1, 2, 110.0, "Jalan Uji", False)
    assert e[5].startswith("LINESTRING(106.8 -6.2")


def test_ut_ops_04b_failed_component_check_keeps_previous_active(osm_db):
    conn, osm = osm_db
    write_road_graph(conn, _grid("good"), osm_schema=osm)
    bad = _grid("split", first_edge_id=1000)
    # Second component of the same size: the largest covers only half the nodes.
    other = _grid("split", first_edge_id=2000, node_offset=100)
    bad.nodes += [GraphNode(n.node_id, n.lon + 0.05, n.lat, n.tags) for n in other.nodes]
    bad.edges += [GraphEdge(e.edge_id, e.u, e.v, e.length_m, e.highway, True, True,
                            tuple((x + 0.05, y) for x, y in e.coordinates)) for e in other.edges]
    with pytest.raises(GraphCheckFailed) as exc:
        write_road_graph(conn, bad, osm_schema=osm)
    assert exc.value.problems == {"largest_component_ratio": 0.5}
    assert _versions(conn, osm) == {"good": "active"}
    assert _count(conn, osm, "road_edge", "split") == 0 and _count(conn, osm, "road_node", "split") == 0


def test_edge_with_missing_endpoint_or_mismatched_geometry_fails(osm_db):
    conn, osm = osm_db
    g = _grid("broken")
    e0 = g.edges[0]
    g.edges[0] = GraphEdge(e0.edge_id, e0.u, 999_999, e0.length_m, e0.highway, True, True, e0.coordinates)
    e1 = g.edges[1]
    g.edges[1] = GraphEdge(e1.edge_id, e1.u, e1.v, e1.length_m, e1.highway, True, True, e1.coordinates[::-1])
    with pytest.raises(GraphCheckFailed) as exc:
        write_road_graph(conn, g, osm_schema=osm, min_component_ratio=0)
    assert exc.value.problems["missing_endpoint_node"] == 1
    assert exc.value.problems["geometry_endpoint_mismatch"] == 1
    assert _versions(conn, osm) == {}


def test_invalid_input_is_rejected_before_writing(osm_db):
    conn, osm = osm_db
    g = _grid("dupes")
    g.edges.append(g.edges[0])
    g.edges.append(GraphEdge(500, 1, 2, 10.0, None, True, True, ((LON0, LAT0),)))
    with pytest.raises(GraphCheckFailed) as exc:
        write_road_graph(conn, g, osm_schema=osm)
    assert exc.value.problems == {"duplicate_edge_id": 1, "missing_geometry": 1}
    assert _versions(conn, osm) == {}


def test_edge_id_reused_from_another_version_fails(osm_db):
    conn, osm = osm_db
    write_road_graph(conn, _grid("v1"), osm_schema=osm)
    with pytest.raises(GraphCheckFailed) as exc:
        write_road_graph(conn, _grid("v2", first_edge_id=1), osm_schema=osm)
    assert exc.value.problems == {"edge_id_used_by_other_version": 24}
    assert _versions(conn, osm) == {"v1": "active"}


def test_unique_index_on_edge_id_backs_up_the_check(osm_db, monkeypatch):
    """If the pre-write check is bypassed (e.g. a concurrent writer), the unique index on
    road_edge (edge_id) rejects the COPY and nothing of the new version is written."""
    import osm_cache.writer as writer

    conn, osm = osm_db
    write_road_graph(conn, _grid("v1"), osm_schema=osm)
    monkeypatch.setattr(writer, "_edge_ids_in_use", lambda *a: 0)
    with pytest.raises(GraphCheckFailed) as exc:
        write_road_graph(conn, _grid("v2", first_edge_id=1), osm_schema=osm)
    assert exc.value.problems == {"edge_id_unique_violation": 1}
    assert _versions(conn, osm) == {"v1": "active"}
    assert _count(conn, osm, "road_node", "v2") == 0


def test_post_write_check_catches_reuse_without_unique_index(osm_db, monkeypatch):
    """Databases without the unique index (e.g. an older copy of the schema) still reject reuse."""
    import osm_cache.writer as writer

    conn, osm = osm_db
    conn.execute(f'DROP INDEX "{osm}".road_edge_edge_id_key')
    write_road_graph(conn, _grid("v1"), osm_schema=osm)
    monkeypatch.setattr(writer, "_edge_ids_in_use", lambda *a: 0)
    with pytest.raises(GraphCheckFailed) as exc:
        write_road_graph(conn, _grid("v2", first_edge_id=1), osm_schema=osm)
    assert exc.value.problems == {"edge_id_used_by_other_version": 24}
    assert _versions(conn, osm) == {"v1": "active"}
    assert _count(conn, osm, "road_edge", "v2") == 0


def test_same_version_is_not_duplicated(osm_db):
    conn, osm = osm_db
    write_road_graph(conn, _grid("v1"), osm_schema=osm)
    with pytest.raises(GraphVersionExists):
        write_road_graph(conn, _grid("v1", first_edge_id=100), osm_schema=osm)
    # A leftover 'building' version from an interrupted write is replaced, not duplicated.
    conn.execute(f"""INSERT INTO "{osm}".graph_version (version, status) VALUES ('v2', 'building')""")
    conn.execute(f"""INSERT INTO "{osm}".road_node (node_id, graph_version, geom)
                     VALUES (77, 'v2', ST_SetSRID(ST_MakePoint(106.9, -6.1), 4326))""")
    write_road_graph(conn, _grid("v2", first_edge_id=100), osm_schema=osm)
    assert _versions(conn, osm) == {"v1": "retired", "v2": "active"}
    assert _count(conn, osm, "road_node", "v2") == 16 and _count(conn, osm, "road_edge", "v2") == 24


def test_it_k_09_readers_see_old_version_until_switch(osm_db, pg_dsn):
    import psycopg

    conn, osm = osm_db
    write_road_graph(conn, _grid("old"), osm_schema=osm)
    seen = {}

    def peek():
        with psycopg.connect(pg_dsn, autocommit=True) as reader:
            seen["active"] = reader.execute(
                f"""SELECT version FROM "{osm}".graph_version WHERE status = 'active'""").fetchone()
            seen["new_status"] = reader.execute(
                f"""SELECT status FROM "{osm}".graph_version WHERE version = 'new'""").fetchone()[0]

    write_road_graph(conn, _grid("new", first_edge_id=100), osm_schema=osm, before_activate=peek)
    assert seen == {"active": ("old",), "new_status": "building"}
    assert _versions(conn, osm) == {"new": "active", "old": "retired"}


def test_failure_during_activation_rolls_back(osm_db):
    conn, osm = osm_db
    write_road_graph(conn, _grid("old"), osm_schema=osm)

    def boom():
        raise RuntimeError("galat disuntikkan")

    with pytest.raises(RuntimeError):
        write_road_graph(conn, _grid("new", first_edge_id=100), osm_schema=osm, before_activate=boom)
    assert _versions(conn, osm) == {"old": "active"}
    assert _count(conn, osm, "road_edge", "new") == 0
