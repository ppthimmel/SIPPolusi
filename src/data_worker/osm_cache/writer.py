"""write_road_graph: menulis RoadGraph sebagai versi baru OSM Cache DB.

Kontrak (Dokumen Desain Tabel 3.11, IRoadGraphWrite Tabel 3.14/3.15, UT-OPS-04, IT-K-09):

1. Transaksi pertama menulis ``graph_version`` berstatus ``building`` dan menyalin
   node serta ruas dengan COPY. Setelah commit, versi ini terlihat tetapi Backend
   tetap membaca versi ``active`` sebelumnya.
2. Pemeriksaan di basis data: jumlah baris, node ujung ruas, panjang, geometri,
   kecocokan ujung geometri dengan node, ``edge_id`` tidak dipakai versi lain, dan
   komponen terhubung terbesar mencakup sekurang-kurangnya 95% node (Tabel 3.9).
3. Bila lolos, transaksi kedua mengubah versi active lama menjadi ``retired`` dan
   versi baru menjadi ``active``. Bila gagal di langkah mana pun, versi ``building``
   dibuang dan versi active sebelumnya tetap berlaku.

Versi bersifat tetap: menulis ulang versi berstatus active atau retired ditolak.
Sisa versi ``building`` dari penulisan yang terputus diganti, bukan diduplikasi.

``edge_id`` unik secara global karena pollution.edge_pollution hanya merujuk
``edge_id``. Penulis memeriksa ini sebelum menulis, dan bila basis data memiliki
unique index pada ``road_edge (edge_id)``, pelanggaran saat COPY juga dilaporkan
sebagai GraphCheckFailed. Pakai ``next_edge_id`` untuk nomor awal versi baru.

Koneksi psycopg 3; disarankan ``autocommit=True`` seperti spatial_model/cache.py.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import pathlib
import re
from collections import Counter, deque
from typing import Callable

from psycopg import errors as pg_errors
from psycopg import sql

MIN_LARGEST_COMPONENT_RATIO = 0.95   # build_road_graph, Tabel 3.9
WRITE_STATEMENT_TIMEOUT = "10min"    # IRoadGraphWrite, Tabel 3.15
# Ujung geometri boleh berbeda dari node sejauh pembulatan koordinat (±1 m).
ENDPOINT_TOLERANCE_DEG = 1e-5

log = logging.getLogger(__name__)

_SCHEMA_FILE = pathlib.Path(__file__).with_name("schema.sql")
_SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


@dataclasses.dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: int
    lon: float
    lat: float
    tags: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True, slots=True)
class GraphEdge:
    """Ruas dari u ke v. ``coordinates`` berisi (lon, lat) berurutan dari u ke v."""

    edge_id: int
    u: int
    v: int
    length_m: float
    highway: str | None
    walk_allowed: bool
    bike_allowed: bool
    coordinates: tuple[tuple[float, float], ...]
    osm_way_id: int | None = None
    name: str | None = None
    oneway: bool = False
    tags: dict[str, str] = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(slots=True)
class RoadGraphData:
    """Graf hasil build_road_graph yang siap ditulis sebagai satu versi."""

    version: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    osm_timestamp: dt.datetime | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class WriteResult:
    version: str
    node_count: int
    edge_count: int
    largest_component_ratio: float
    retired_version: str | None


class GraphWriteError(Exception):
    pass


class GraphVersionExists(GraphWriteError):
    pass


class GraphCheckFailed(GraphWriteError):
    def __init__(self, version: str, problems: dict[str, float]):
        super().__init__(f"graf {version!r} gagal pemeriksaan: {problems}")
        self.version = version
        self.problems = problems


def schema_sql(osm: str = "osm", pollution: str = "pollution") -> str:
    """DDL schema osm dan pollution dengan nama schema yang dapat diganti (untuk uji)."""
    for name in (osm, pollution):
        if not _SCHEMA_NAME_RE.match(name):
            raise ValueError(f"nama schema tidak valid: {name!r}")
    return _SCHEMA_FILE.read_text(encoding="utf-8").replace("__OSM__", osm).replace("__POLLUTION__", pollution)


def largest_component_ratio(nodes: list[GraphNode], edges: list[GraphEdge]) -> float:
    """Porsi node pada komponen terhubung terbesar, tanpa memandang arah dan moda."""
    if not nodes:
        return 0.0
    adjacency: dict[int, list[int]] = {n.node_id: [] for n in nodes}
    for e in edges:
        if e.u in adjacency and e.v in adjacency and e.u != e.v:
            adjacency[e.u].append(e.v)
            adjacency[e.v].append(e.u)
    seen: set[int] = set()
    largest = 0
    for start in adjacency:
        if start in seen:
            continue
        seen.add(start)
        size, queue = 0, deque([start])
        while queue:
            current = queue.popleft()
            size += 1
            for nxt in adjacency[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        largest = max(largest, size)
    return largest / len(adjacency)


def next_edge_id(conn, osm_schema: str = "osm") -> int:
    """edge_id pertama yang belum dipakai versi mana pun; edge_id unik secara global."""
    row = conn.execute(
        sql.SQL("SELECT coalesce(max(edge_id), 0) + 1 FROM {}").format(_table(osm_schema, "road_edge"))
    ).fetchone()
    return int(row[0])


def _table(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def _point_ewkt(lon: float, lat: float) -> str:
    return f"SRID=4326;POINT({lon!r} {lat!r})"


def _line_ewkt(coordinates) -> str:
    return "SRID=4326;LINESTRING(" + ", ".join(f"{x!r} {y!r}" for x, y in coordinates) + ")"


def _edge_ids_in_use(conn, osm_schema: str, edge_ids: list[int]) -> int:
    """Berapa edge_id input yang sudah ada di road_edge (versi mana pun)."""
    with conn.transaction():
        row = conn.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE edge_id = ANY(%s)").format(_table(osm_schema, "road_edge")),
            (edge_ids,),
        ).fetchone()
    return int(row[0])


def _input_problems(graph: RoadGraphData) -> dict[str, float]:
    """Masalah yang membuat COPY gagal; diperiksa sebelum menulis apa pun."""
    problems: dict[str, float] = {}
    if not graph.nodes or not graph.edges:
        problems["empty_graph"] = 1
    for label, ids in (("duplicate_node_id", [n.node_id for n in graph.nodes]),
                       ("duplicate_edge_id", [e.edge_id for e in graph.edges])):
        dupes = sum(c - 1 for c in Counter(ids).values() if c > 1)
        if dupes:
            problems[label] = dupes
    short = sum(1 for e in graph.edges if len(e.coordinates) < 2)
    if short:
        problems["missing_geometry"] = short
    return problems


_SQL_CHECKS = """
SELECT
  (SELECT count(*) FROM {node} WHERE graph_version = %(v)s) AS nodes,
  (SELECT count(*) FROM {edge} WHERE graph_version = %(v)s) AS edges,
  (SELECT count(*) FROM {edge} e
     LEFT JOIN {node} nu ON nu.node_id = e.u AND nu.graph_version = e.graph_version
     LEFT JOIN {node} nv ON nv.node_id = e.v AND nv.graph_version = e.graph_version
   WHERE e.graph_version = %(v)s AND (nu.node_id IS NULL OR nv.node_id IS NULL)) AS missing_endpoint_node,
  (SELECT count(*) FROM {edge} e
     JOIN {node} nu ON nu.node_id = e.u AND nu.graph_version = e.graph_version
     JOIN {node} nv ON nv.node_id = e.v AND nv.graph_version = e.graph_version
   WHERE e.graph_version = %(v)s
     AND (NOT ST_DWithin(ST_StartPoint(e.geom), nu.geom, %(tol)s)
          OR NOT ST_DWithin(ST_EndPoint(e.geom), nv.geom, %(tol)s))) AS geometry_endpoint_mismatch,
  (SELECT count(*) FROM {edge} WHERE graph_version = %(v)s
     AND (length_m IS NULL OR NOT (length_m > 0) OR length_m IN ('Infinity', 'NaN'))) AS invalid_length,
  (SELECT count(*) FROM {edge} WHERE graph_version = %(v)s
     AND (geom IS NULL OR ST_NPoints(geom) < 2)) AS missing_geometry,
  (SELECT count(*) FROM {edge} e WHERE e.graph_version = %(v)s
     AND EXISTS (SELECT 1 FROM {edge} o WHERE o.edge_id = e.edge_id AND o.graph_version <> e.graph_version))
    AS edge_id_used_by_other_version
"""


def _database_problems(conn, graph: RoadGraphData, osm_schema: str, ratio: float,
                       min_ratio: float) -> dict[str, float]:
    with conn.transaction():
        conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(WRITE_STATEMENT_TIMEOUT)))
        cur = conn.cursor()
        cur.execute(sql.SQL(_SQL_CHECKS).format(node=_table(osm_schema, "road_node"),
                                                edge=_table(osm_schema, "road_edge")),
                    {"v": graph.version, "tol": ENDPOINT_TOLERANCE_DEG})
        row = dict(zip([c.name for c in cur.description], cur.fetchone()))
    problems = {k: v for k, v in row.items() if k not in ("nodes", "edges") and v}
    if row["nodes"] != len(graph.nodes):
        problems["node_count_mismatch"] = row["nodes"]
    if row["edges"] != len(graph.edges):
        problems["edge_count_mismatch"] = row["edges"]
    if ratio < min_ratio:
        problems["largest_component_ratio"] = round(ratio, 4)
    return problems


def _discard_building(conn, version: str, osm_schema: str) -> None:
    with conn.transaction():
        status = conn.execute(
            sql.SQL("SELECT status FROM {} WHERE version = %s FOR UPDATE").format(_table(osm_schema, "graph_version")),
            (version,),
        ).fetchone()
        if status is not None and status[0] != "building":
            return  # never touch an active or retired version
        for table in ("road_edge", "road_node"):
            conn.execute(sql.SQL("DELETE FROM {} WHERE graph_version = %s").format(_table(osm_schema, table)),
                         (version,))
        conn.execute(sql.SQL("DELETE FROM {} WHERE version = %s AND status = 'building'")
                     .format(_table(osm_schema, "graph_version")), (version,))


def write_road_graph(
    conn,
    graph: RoadGraphData,
    *,
    min_component_ratio: float = MIN_LARGEST_COMPONENT_RATIO,
    osm_schema: str = "osm",
    before_activate: Callable[[], None] | None = None,
) -> WriteResult:
    """Menulis graf sebagai versi baru lalu mengaktifkannya (B9 dan B10). Mengembalikan WriteResult.

    ``before_activate`` dipanggil di dalam transaksi pengaktifan, tepat sebelum versi
    baru menjadi active; dipakai uji untuk memeriksa pembacaan selama ``building``
    (IT-K-09) dan untuk menyuntikkan galat.
    """
    version = graph.version.strip()
    if not version:
        raise ValueError("graph.version wajib diisi")
    problems = _input_problems(graph)
    if problems:
        raise GraphCheckFailed(version, problems)

    version_table = _table(osm_schema, "graph_version")
    with conn.transaction():
        existing = conn.execute(sql.SQL("SELECT status FROM {} WHERE version = %s FOR UPDATE").format(version_table),
                                (version,)).fetchone()
    if existing is not None and existing[0] != "building":
        raise GraphVersionExists(f"versi {version!r} sudah ada dengan status {existing[0]!r}")
    if existing is not None:
        _discard_building(conn, version, osm_schema)  # sisa penulisan yang terputus

    reused = _edge_ids_in_use(conn, osm_schema, [e.edge_id for e in graph.edges])
    if reused:
        raise GraphCheckFailed(version, {"edge_id_used_by_other_version": reused})

    try:
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(WRITE_STATEMENT_TIMEOUT)))
            conn.execute(
                sql.SQL("INSERT INTO {} (version, status, osm_timestamp, node_count, edge_count) "
                        "VALUES (%s, 'building', %s, %s, %s)").format(version_table),
                (version, graph.osm_timestamp, len(graph.nodes), len(graph.edges)),
            )
            cur = conn.cursor()
            with cur.copy(sql.SQL("COPY {} (node_id, graph_version, geom, tags) FROM STDIN")
                          .format(_table(osm_schema, "road_node"))) as copy:
                for n in graph.nodes:
                    copy.write_row((n.node_id, version, _point_ewkt(n.lon, n.lat), json.dumps(n.tags)))
            with cur.copy(sql.SQL("COPY {} (edge_id, graph_version, u, v, length_m, highway, walk_allowed, "
                                  "bike_allowed, geom, osm_way_id, name, oneway, tags) FROM STDIN")
                          .format(_table(osm_schema, "road_edge"))) as copy:
                for e in graph.edges:
                    copy.write_row((e.edge_id, version, e.u, e.v, e.length_m, e.highway, e.walk_allowed,
                                    e.bike_allowed, _line_ewkt(e.coordinates), e.osm_way_id, e.name, e.oneway,
                                    json.dumps(e.tags)))
    except pg_errors.UniqueViolation as exc:
        # Unique index on road_edge (edge_id): another writer took an id after the check above.
        # The transaction rolled back, so nothing of this version was written.
        raise GraphCheckFailed(version, {"edge_id_unique_violation": 1}) from exc

    try:
        ratio = largest_component_ratio(graph.nodes, graph.edges)
        problems = _database_problems(conn, graph, osm_schema, ratio, min_component_ratio)
        if problems:
            raise GraphCheckFailed(version, problems)
        with conn.transaction():
            conn.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(WRITE_STATEMENT_TIMEOUT)))
            old = conn.execute(sql.SQL("SELECT version FROM {} WHERE status = 'active' FOR UPDATE")
                               .format(version_table)).fetchone()
            conn.execute(sql.SQL("UPDATE {} SET status = 'retired' WHERE status = 'active'").format(version_table))
            if before_activate is not None:
                before_activate()
            activated = conn.execute(
                sql.SQL("UPDATE {} SET status = 'active', built_at = now() WHERE version = %s AND status = 'building'")
                .format(version_table), (version,),
            ).rowcount
            if activated != 1:
                raise GraphWriteError(f"versi {version!r} tidak lagi berstatus building")
    except BaseException:
        try:
            _discard_building(conn, version, osm_schema)
        except Exception:  # noqa: BLE001 - keep the original error; a later write replaces the leftover
            log.exception("versi building %r gagal dibuang", version)
        raise
    return WriteResult(version=version, node_count=len(graph.nodes), edge_count=len(graph.edges),
                       largest_component_ratio=ratio, retired_version=old[0] if old else None)
