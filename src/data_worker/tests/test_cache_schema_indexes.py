"""Setiap foreign key skema cache punya index yang diawali kolom-kolomnya.

Tanpa index itu, ON DELETE CASCADE dan pemeriksaan FK memindai seluruh tabel anak
untuk setiap baris induk yang dihapus. Di Railway, pemangkasan satu time window
(111 ribu sel grid, 1,7 juta baris edge_cell) berjalan berjam-jam, sehingga siklus
per jam tertinggal (28 Oktober 2026).
"""

from __future__ import annotations

import uuid
from urllib.parse import urlparse

import pytest

# Foreign key tanpa index yang diawali kolom-kolomnya (urutan kolom bebas).
_FK_WITHOUT_INDEX = """
SELECT c.conrelid::regclass::text AS tabel,
       array_agg(a.attname::text ORDER BY k.ord) AS kolom
FROM pg_constraint c
CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord)
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.contype = 'f'
  AND c.connamespace::regnamespace::text = ANY(%s)
  AND NOT EXISTS (
      SELECT 1 FROM pg_index i
      WHERE i.indrelid = c.conrelid
        AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] @> c.conkey
        AND (i.indkey::int2[])[0:cardinality(c.conkey) - 1] <@ c.conkey)
GROUP BY c.oid, c.conrelid
ORDER BY 1, 2
"""


def _fk_without_index(conn, schemas: list[str]) -> list[tuple[str, list[str]]]:
    return [(t, list(cols)) for t, cols in conn.execute(_FK_WITHOUT_INDEX, (schemas,)).fetchall()]


def test_init_db_memberi_index_untuk_setiap_foreign_key(cache_db):
    import psycopg

    from database import init_db

    engine, dsn = cache_db
    init_db(engine)
    with psycopg.connect(dsn) as conn:
        assert _fk_without_index(conn, ["osm", "pollution"]) == []
        names = {r[0] for r in conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'pollution'").fetchall()}
    assert {"edge_cell_run_cell_idx", "edge_pollution_trace_run_idx"} <= names
    # Idempoten: siklus berikutnya menjalankan migrasi yang sama.
    init_db(engine)


def test_skema_uji_osm_cache_memberi_index_untuk_setiap_foreign_key(pg_dsn):
    import psycopg

    from osm_cache import schema_sql

    if urlparse(pg_dsn).hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.skip("uji tulis hanya boleh ke basis data lokal")
    suffix = uuid.uuid4().hex[:10]
    osm, pollution = f"o_{suffix}", f"p_{suffix}"
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        try:
            conn.execute(schema_sql(osm, pollution))
            assert _fk_without_index(conn, [osm, pollution]) == []
        finally:
            conn.execute(f'DROP SCHEMA IF EXISTS "{osm}" CASCADE')
            conn.execute(f'DROP SCHEMA IF EXISTS "{pollution}" CASCADE')


def test_hapus_berantai_edge_cell_memakai_index(cache_db):
    """Pencarian yang dijalankan ON DELETE CASCADE per sel grid tidak memindai edge_cell."""
    import psycopg

    from database import init_db

    engine, dsn = cache_db
    init_db(engine)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("""INSERT INTO pollution.pollution_window (time_window_start, pollutant, status, model_version)
                        VALUES ('2026-10-28 03:00+00', 'pm25,no2', 'complete', 'idw')""")
        conn.execute("""INSERT INTO pollution.inference_run (run_id, time_window_start, manifest, trace_sha256)
                        VALUES ('run-1', '2026-10-28 03:00+00', '{}', 'x')""")
        conn.execute("""INSERT INTO pollution.grid_prediction (run_id, cell_id, x, y, pm25_source, no2_source)
                        SELECT 'run-1', g, 0, 0, 'idw', 'idw' FROM generate_series(1, 2000) g""")
        conn.execute("""INSERT INTO pollution.edge_cell (run_id, edge_id, cell_id, length_m)
                        SELECT 'run-1', e, 1 + e % 2000, 10 FROM generate_series(1, 20000) e""")
        conn.execute("ANALYZE pollution.edge_cell")
        plan = "\n".join(r[0] for r in conn.execute(
            "EXPLAIN SELECT 1 FROM pollution.edge_cell WHERE run_id = 'run-1' AND cell_id = 7").fetchall())
        assert "edge_cell_run_cell_idx" in plan, plan
        # Pemangkasan time window menghapus semuanya secara berantai.
        conn.execute("DELETE FROM pollution.pollution_window WHERE time_window_start = '2026-10-28 03:00+00'")
        assert conn.execute("SELECT count(*) FROM pollution.edge_cell").fetchone()[0] == 0
