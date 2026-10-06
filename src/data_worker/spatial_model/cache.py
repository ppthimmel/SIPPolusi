"""Operasi Spatial Pollution Cache DB dan pembacaan OSM Cache DB (Tabel 3.11, 3.17).

* ``write_pollution_weight``: satu transaksi. pollution_window ditandai
  ``writing``, baris EdgeWeight disalin (COPY) ke tabel sementara lalu
  di-upsert dengan kunci (edge_id, time_window_start), baris lama time window
  yang sama yang tidak lagi ada dihapus, lalu pollution_window ditandai
  ``complete``. Galat di tengah jalan membatalkan seluruhnya, sehingga
  time window sebelumnya tetap berlaku (UT-OPS-02, IT-K-08). Geometri ruas
  diambil dari ``osm.road_edge`` di dalam SQL, tidak dikirim dari Python.
* ``read_pollution_weight``: baris time window terakhir berstatus ``complete``
  yang memotong bbox (UT-OPS-01).

Nama skema dapat diganti agar uji berjalan pada skema sementara.
"""

from __future__ import annotations

import datetime as dt
from typing import Callable, Iterable

import numpy as np
import pandas as pd
from psycopg import sql

from contracts import BBox, EdgeWeight, TimeWindow

EDGE_WEIGHT_COLUMNS = ["edge_id", "pm25_ugm3", "no2_ugm3", "exposure_index", "confidence_score",
                       "background_source", "estimation_source"]
WRITE_STATEMENT_TIMEOUT = "5min"     # Tabel 3.19, IPollutionWeightWrite


def _ident(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


# ------------------------------------------------------------------ OSM Cache DB


def read_active_graph_version(conn, osm_schema: str = "osm") -> str | None:
    row = conn.execute(
        sql.SQL("SELECT version FROM {} WHERE status = 'active'").format(_ident(osm_schema, "graph_version"))
    ).fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, dict) else row["version"]


def read_road_edges(conn, graph_version: str, osm_schema: str = "osm"):
    """Geometri seluruh ruas satu versi graf.

    Mengembalikan (edge_ids, coords_lonlat, coord_edge_index) untuk
    ``aggregate.edge_cell_pieces``.
    """
    import shapely

    cur = conn.cursor()
    cur.execute(
        sql.SQL("SELECT edge_id, ST_AsBinary(geom) FROM {} WHERE graph_version = %s AND geom IS NOT NULL "
                "ORDER BY edge_id").format(_ident(osm_schema, "road_edge")),
        (graph_version,),
    )
    rows = cur.fetchall()
    if not rows:
        raise LookupError(f"graf {graph_version!r} tidak memiliki ruas")
    first = rows[0]
    if isinstance(first, dict):
        keys = list(first)
        rows = [(r[keys[0]], r[keys[1]]) for r in rows]
    edge_ids = np.fromiter((r[0] for r in rows), dtype=np.int64, count=len(rows))
    geoms = shapely.from_wkb([bytes(r[1]) for r in rows])
    coords, index = shapely.get_coordinates(geoms, return_index=True)
    return edge_ids, coords, index


# ------------------------------------------------------------------ Spatial Pollution Cache DB


def _as_frame(edge_weights) -> pd.DataFrame:
    if isinstance(edge_weights, pd.DataFrame):
        df = edge_weights.copy()
    else:
        df = pd.DataFrame([{c: getattr(w, c) for c in EDGE_WEIGHT_COLUMNS} for w in edge_weights],
                          columns=EDGE_WEIGHT_COLUMNS)
    missing = [c for c in EDGE_WEIGHT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"kolom EdgeWeight hilang: {missing}")
    if df["edge_id"].duplicated().any():
        raise ValueError("edge_id ganda pada EdgeWeight satu time window")
    if not df["estimation_source"].isin(["stgnn", "idw"]).all():
        raise ValueError("estimation_source wajib 'stgnn' atau 'idw'")
    return df[EDGE_WEIGHT_COLUMNS]


def _nullable(value):
    if value is None:
        return None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    return value


def write_pollution_weight(
    conn,
    edge_weights,
    time_window: TimeWindow,
    graph_version: str,
    model_version: str,
    coverage_ratio: float | None = None,
    pollution_schema: str = "pollution",
    osm_schema: str = "osm",
    before_commit: Callable[[], None] | None = None,
) -> int:
    """Menulis EdgeWeight satu time window dalam satu transaksi (Tabel 3.11, B26–B27).

    ``edge_weights`` berupa list[EdgeWeight] atau DataFrame berkolom
    ``EDGE_WEIGHT_COLUMNS``. ``before_commit`` dipanggil tepat sebelum
    transaksi ditutup; dipakai uji untuk menyuntikkan galat (UT-OPS-02c).
    Mengembalikan jumlah baris yang ditulis.
    """
    df = _as_frame(edge_weights)
    window_start = time_window.start
    pollutants = ",".join(p for p, c in (("pm25", "pm25_ugm3"), ("no2", "no2_ugm3")) if df[c].notna().any())
    edge_table = _ident(pollution_schema, "edge_pollution")
    window_table = _ident(pollution_schema, "pollution_window")
    with conn.transaction():
        conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(WRITE_STATEMENT_TIMEOUT)))
        conn.execute(
            sql.SQL("""INSERT INTO {} (time_window_start, pollutant, status, coverage_ratio, model_version)
                       VALUES (%s, %s, 'writing', %s, %s)
                       ON CONFLICT (time_window_start) DO UPDATE
                       SET status = 'writing', pollutant = EXCLUDED.pollutant,
                           coverage_ratio = EXCLUDED.coverage_ratio, model_version = EXCLUDED.model_version,
                           completed_at = NULL""").format(window_table),
            (window_start, pollutants, coverage_ratio, model_version),
        )
        conn.execute("""CREATE TEMP TABLE edge_weight_incoming (
                            edge_id BIGINT PRIMARY KEY, pm25_ugm3 REAL, no2_ugm3 REAL, exposure_index REAL,
                            confidence_score REAL, background_source TEXT, estimation_source TEXT
                        ) ON COMMIT DROP""")
        with conn.cursor().copy(
            "COPY edge_weight_incoming (edge_id, pm25_ugm3, no2_ugm3, exposure_index, confidence_score, "
            "background_source, estimation_source) FROM STDIN"
        ) as copy:
            for row in df.itertuples(index=False, name=None):
                copy.write_row([_nullable(v) for v in row])
        written = conn.execute(
            sql.SQL("""INSERT INTO {edge} (edge_id, time_window_start, pm25_ugm3, no2_ugm3, exposure_index,
                                           confidence_score, background_source, estimation_source,
                                           graph_version, model_version, geom, written_at)
                       SELECT t.edge_id, %(w)s, t.pm25_ugm3, t.no2_ugm3, t.exposure_index, t.confidence_score,
                              t.background_source, t.estimation_source, %(g)s, %(m)s, e.geom, now()
                       FROM edge_weight_incoming t
                       LEFT JOIN {road} e ON e.edge_id = t.edge_id AND e.graph_version = %(g)s
                       ON CONFLICT (edge_id, time_window_start) DO UPDATE SET
                           pm25_ugm3 = EXCLUDED.pm25_ugm3, no2_ugm3 = EXCLUDED.no2_ugm3,
                           exposure_index = EXCLUDED.exposure_index, confidence_score = EXCLUDED.confidence_score,
                           background_source = EXCLUDED.background_source,
                           estimation_source = EXCLUDED.estimation_source,
                           graph_version = EXCLUDED.graph_version, model_version = EXCLUDED.model_version,
                           geom = EXCLUDED.geom, written_at = EXCLUDED.written_at""").format(
                edge=edge_table, road=_ident(osm_schema, "road_edge")),
            {"w": window_start, "g": graph_version, "m": model_version},
        ).rowcount
        # Baris lama time window yang sama yang tidak termasuk penulisan ini.
        conn.execute(
            sql.SQL("""DELETE FROM {} p WHERE p.time_window_start = %s
                       AND NOT EXISTS (SELECT 1 FROM edge_weight_incoming t WHERE t.edge_id = p.edge_id)""")
            .format(edge_table),
            (window_start,),
        )
        conn.execute(
            sql.SQL("UPDATE {} SET status = 'complete', completed_at = now() WHERE time_window_start = %s")
            .format(window_table),
            (window_start,),
        )
        if before_commit is not None:
            before_commit()
    return int(written)


def prune_pollution_windows(conn, keep_windows: int, pollution_schema: str = "pollution") -> int:
    """Hapus time window complete selain ``keep_windows`` terakhir beserta barisnya.

    Satu time window seluruh graf memakan ±184 MB (TI-AI-05), sedangkan Backend
    hanya membaca time window complete terakhir (data_stale bila > 3 jam).
    Time window berstatus writing tidak disentuh. Mengembalikan jumlah time
    window yang dihapus.
    """
    if keep_windows < 1:
        raise ValueError("keep_windows minimal 1")
    edge_table = _ident(pollution_schema, "edge_pollution")
    window_table = _ident(pollution_schema, "pollution_window")
    with conn.transaction():
        old = [r[0] if not isinstance(r, dict) else r["time_window_start"] for r in conn.execute(
            sql.SQL("""SELECT time_window_start FROM {} WHERE status = 'complete'
                       ORDER BY time_window_start DESC OFFSET %s""").format(window_table),
            (keep_windows,),
        ).fetchall()]
        if old:
            conn.execute(sql.SQL("DELETE FROM {} WHERE time_window_start = ANY(%s)").format(edge_table), (old,))
            conn.execute(sql.SQL("DELETE FROM {} WHERE time_window_start = ANY(%s)").format(window_table), (old,))
    return len(old)


def latest_complete_window(conn, pollution_schema: str = "pollution") -> dt.datetime | None:
    row = conn.execute(
        sql.SQL("SELECT max(time_window_start) FROM {} WHERE status = 'complete'")
        .format(_ident(pollution_schema, "pollution_window"))
    ).fetchone()
    value = row[0] if not isinstance(row, dict) else next(iter(row.values()))
    return value


def window_record(conn, time_window_start: dt.datetime, pollution_schema: str = "pollution") -> dict | None:
    cur = conn.cursor()
    cur.execute(
        sql.SQL("SELECT time_window_start, pollutant, status, coverage_ratio, model_version, completed_at "
                "FROM {} WHERE time_window_start = %s").format(_ident(pollution_schema, "pollution_window")),
        (time_window_start,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(zip([c.name for c in cur.description], row))


def read_pollution_weight(
    conn,
    bbox: BBox,
    time_window: TimeWindow | str = "latest",
    pollution_schema: str = "pollution",
) -> tuple[list[EdgeWeight], dt.datetime | None]:
    """EdgeWeight time window berstatus complete yang memotong bbox (Tabel 3.11, UT-OPS-01).

    ``time_window`` "latest" memilih time window complete terakhir. Bila tidak
    ada time window complete, mengembalikan ([], None), yang diteruskan Backend
    sebagai cache_unavailable.
    """
    if time_window == "latest":
        window_start = latest_complete_window(conn, pollution_schema)
    else:
        record = window_record(conn, time_window.start, pollution_schema)
        window_start = time_window.start if record and record["status"] == "complete" else None
    if window_start is None:
        return [], None
    cur = conn.cursor()
    cur.execute(
        sql.SQL("""SELECT edge_id, time_window_start, pm25_ugm3, no2_ugm3, exposure_index, confidence_score,
                          background_source, estimation_source
                   FROM {} WHERE time_window_start = %s
                   AND ST_Intersects(geom, ST_MakeEnvelope(%s, %s, %s, %s, 4326))
                   ORDER BY edge_id""").format(_ident(pollution_schema, "edge_pollution")),
        (window_start, *bbox.as_tuple()),
    )
    names = [c.name for c in cur.description]
    rows: Iterable = cur.fetchall()
    weights = [EdgeWeight(**(r if isinstance(r, dict) else dict(zip(names, r)))) for r in rows]
    return weights, window_start
