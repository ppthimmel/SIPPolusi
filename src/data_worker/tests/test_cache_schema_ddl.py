"""init_db (model ORM) dan osm_cache.schema_sql() (salinan struktur Railway) memberi skema yang sama.

Pada stack lokal baru, Data Worker (``serve --now``) menjalankan init_db sebelum
``seed_graph`` menjalankan schema_sql(). Dulu schema_sql() gagal dengan
DuplicateTable, dan tabel osm dari model ORM tidak punya kolom ``tags`` dan kolom
lain yang dibaca Cache API dan ditulis write_road_graph (12 November 2026).
"""

from __future__ import annotations

import datetime as dt
import uuid

from osm_cache import GraphEdge, GraphNode, RoadGraphData, schema_sql, write_road_graph

_COLUMNS = """
SELECT table_name, column_name, data_type, is_nullable,
       replace(coalesce(column_default, ''), %(schema)s || '.', '') AS column_default
FROM information_schema.columns
WHERE table_schema = %(schema)s
"""


def _columns(conn, schema: str) -> set[tuple]:
    return set(conn.execute(_COLUMNS, {"schema": schema}).fetchall())


def _grid(version: str, size: int = 3) -> RoadGraphData:
    lon0, lat0, step = 106.80, -6.20, 0.001
    nodes = [GraphNode(r * size + c + 1, lon0 + c * step, lat0 + r * step, {"highway": "crossing"})
             for r in range(size) for c in range(size)]
    by_id = {n.node_id: n for n in nodes}
    edges, eid = [], 1
    for r in range(size):
        for c in range(size):
            a = r * size + c + 1
            for b in ([a + 1] if c < size - 1 else []) + ([a + size] if r < size - 1 else []):
                edges.append(GraphEdge(eid, a, b, 110.0, "residential", True, True,
                                       ((by_id[a].lon, by_id[a].lat), (by_id[b].lon, by_id[b].lat)),
                                       osm_way_id=1000 + eid, name="Jalan Uji", tags={"surface": "asphalt"}))
                eid += 1
    return RoadGraphData(version, nodes, edges, osm_timestamp=dt.datetime(2026, 10, 5, tzinfo=dt.timezone.utc))


def _write_and_read_back(conn) -> None:
    result = write_road_graph(conn, _grid("jakarta-uji"))
    assert (result.node_count, result.edge_count) == (9, 12)
    assert conn.execute("SELECT tags FROM osm.road_node WHERE node_id = 1").fetchone()[0] == {"highway": "crossing"}
    assert conn.execute("SELECT osm_way_id, name, oneway, tags FROM osm.road_edge WHERE edge_id = 1").fetchone() == (
        1001, "Jalan Uji", False, {"surface": "asphalt"})


def _indexes(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'osm'").fetchall()}


def test_init_db_dan_schema_sql_memberi_kolom_yang_sama(cache_db):
    import psycopg

    from database import init_db

    engine, dsn = cache_db
    init_db(engine)
    suffix = uuid.uuid4().hex[:10]
    osm, pollution = f"o_{suffix}", f"p_{suffix}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql(osm, pollution))
        assert _columns(conn, "osm") == _columns(conn, osm)
        assert _columns(conn, "pollution") == _columns(conn, pollution)


def test_seed_graph_setelah_init_db(cache_db):
    """Urutan stack lokal baru: init_db Data Worker, lalu seed_graph (schema_sql + write_road_graph)."""
    import psycopg

    from database import init_db

    engine, dsn = cache_db
    init_db(engine)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql())
        conn.execute(schema_sql())  # seed_graph aman dijalankan ulang
        _write_and_read_back(conn)
        assert {"idx_road_edge_name_trgm", "road_edge_edge_id_key", "idx_road_edge_u", "idx_road_edge_v",
                "idx_road_edge_osm_way_id", "idx_road_node_geom", "idx_road_edge_geom"} <= _indexes(conn)
    init_db(engine)  # siklus berikutnya


def test_schema_sql_lalu_init_db(cache_db):
    """Urutan seperti Railway: struktur sudah ada sebelum Data Worker berjalan."""
    import psycopg

    from database import init_db

    engine, dsn = cache_db
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(schema_sql())
    init_db(engine)
    with psycopg.connect(dsn, autocommit=True) as conn:
        _write_and_read_back(conn)


def test_schema_sql_melengkapi_tabel_osm_dari_model_lama(cache_db):
    """Volume lokal yang dibuat Data Worker sebelum model ORM disamakan dengan Railway."""
    import psycopg

    from database import init_db

    engine, dsn = cache_db
    init_db(engine)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("ALTER TABLE osm.road_node DROP COLUMN tags")
        conn.execute("ALTER TABLE osm.road_edge DROP COLUMN osm_way_id, DROP COLUMN name, DROP COLUMN oneway, "
                     "DROP COLUMN tags")
        conn.execute(schema_sql())
        _write_and_read_back(conn)
