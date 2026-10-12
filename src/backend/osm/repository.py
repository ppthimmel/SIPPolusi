"""Queries against schema `osm` (handoff_database.md bagian 9).

Rules applied to every query:
- road_node and road_edge are filtered by the active graph_version, except
  edge lookup by edge_id, which is globally unique.
- node/edge joins use both columns (node_id, graph_version).
- spatial filters use && or <-> so the GiST indexes are used.
- all values are bound parameters; the only SQL fragments chosen at runtime
  are the constant access columns of Mode.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Sequence

from .db import OsmDatabase
from .errors import CacheUnavailable
from .graph import assemble_road_graph
from .models import (
    BBox,
    EdgeDetail,
    GraphVersion,
    Mode,
    NearestEdge,
    NearestNode,
    Neighbor,
    NodeDetail,
    Point,
    RoadGraph,
    StreetMatch,
)

logger = logging.getLogger("osm")

# Degrees per metre used to bound nearest-neighbour scans. 1/110 000 is slightly
# more than the true value at Jakarta's latitude, so the box never cuts off a
# candidate that is within the distance.
_DEG_PER_M = 1 / 110_000
# Candidates fetched by <-> (planar degrees) before re-ranking by geodesic metres.
_KNN_CANDIDATES = 5

_SQL_ACTIVE_VERSION = """
SELECT version, status, osm_timestamp, built_at, node_count, edge_count
FROM osm.graph_version
WHERE status = 'active'
"""

_SQL_GRAPH_EDGES = """
SELECT e.edge_id, e.u, e.v, round(e.length_m::numeric, 2)::float8 AS length_m, e.highway, e.name, e.oneway,
       e.walk_allowed, e.bike_allowed, e.osm_way_id,
       (e.geom IS NOT NULL AND ST_NPoints(e.geom) >= 2) AS has_geometry{geometry}
FROM osm.road_edge e
WHERE e.graph_version = $1
  AND e.geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
  AND e.{access}
"""

# Endpoints of the same edge set, resolved server-side so this query can run
# concurrently with _SQL_GRAPH_EDGES instead of waiting for its node ids.
_SQL_GRAPH_NODES = """
WITH e AS (
    SELECT e.u, e.v
    FROM osm.road_edge e
    WHERE e.graph_version = $1
      AND e.geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
      AND e.{access}
)
SELECT n.node_id, ST_X(n.geom) AS lon, ST_Y(n.geom) AS lat, n.tags
FROM osm.road_node n
WHERE n.graph_version = $1
  AND n.node_id IN (SELECT u FROM e UNION SELECT v FROM e)
"""

_POINT = "ST_SetSRID(ST_MakePoint($1, $2), 4326)"

# Nearest node that touches at least one edge the mode may use (UT-BE-05b).
_SQL_NEAREST_NODE = f"""
SELECT n.node_id, ST_X(n.geom) AS lon, ST_Y(n.geom) AS lat,
       ST_Distance(n.geom::geography, {_POINT}::geography) AS distance_m
FROM osm.road_node n
WHERE n.graph_version = $3
  AND n.geom && ST_Expand({_POINT}, $4)
  AND (EXISTS (SELECT 1 FROM osm.road_edge e
               WHERE e.graph_version = n.graph_version AND e.u = n.node_id AND e.{{access}})
       OR EXISTS (SELECT 1 FROM osm.road_edge e
               WHERE e.graph_version = n.graph_version AND e.v = n.node_id AND e.{{access}}))
ORDER BY n.geom <-> {_POINT}
LIMIT {_KNN_CANDIDATES}
"""

_SQL_NEAREST_EDGE = f"""
SELECT e.edge_id, e.u, e.v, e.name, e.highway, round(e.length_m::numeric, 2)::float8 AS length_m, e.oneway,
       ST_Distance(e.geom::geography, {_POINT}::geography) AS distance_m,
       ST_LineLocatePoint(e.geom, {_POINT}) AS fraction_along,
       ST_X(ST_ClosestPoint(e.geom, {_POINT})) AS snapped_lon,
       ST_Y(ST_ClosestPoint(e.geom, {_POINT})) AS snapped_lat
FROM osm.road_edge e
WHERE e.graph_version = $3
  AND e.geom && ST_Expand({_POINT}, $4)
  AND e.{{access}}
ORDER BY e.geom <-> {_POINT}
LIMIT {_KNN_CANDIDATES}
"""

_SQL_ROADS_GEOJSON = """
SELECT json_build_object(
         'type', 'FeatureCollection',
         'graph_version', $5::text,
         'features', coalesce(json_agg(json_build_object(
             'type', 'Feature',
             'id', e.edge_id,
             'geometry', ST_AsGeoJSON(e.geom, 6)::json,
             'properties', json_build_object(
                 'edge_id', e.edge_id,
                 'name', e.name,
                 'highway', e.highway,
                 'oneway', e.oneway,
                 'walk_allowed', e.walk_allowed,
                 'bike_allowed', e.bike_allowed,
                 'length_m', round(e.length_m::numeric, 2))
         )), '[]'::json)
       )::text
FROM osm.road_edge e
WHERE e.graph_version = $5
  AND e.geom && ST_MakeEnvelope($1, $2, $3, $4, 4326)
  AND ($6::text[] IS NULL OR e.highway = ANY($6::text[])){access}
"""

_SQL_NODE = """
SELECT node_id, graph_version, ST_X(geom) AS lon, ST_Y(geom) AS lat, tags
FROM osm.road_node
WHERE node_id = $1 AND graph_version = $2
"""

_SQL_EDGE = """
SELECT edge_id, graph_version, u, v, round(length_m::numeric, 2)::float8 AS length_m, highway, name, oneway,
       walk_allowed, bike_allowed, osm_way_id, tags,
       ST_AsGeoJSON(geom, 6)::json AS geometry
FROM osm.road_edge
WHERE edge_id = $1
ORDER BY (graph_version = $2) DESC
LIMIT 1
"""

# Walking ignores oneway; cycling may leave through v only on two-way edges.
_SQL_NEIGHBORS = {
    Mode.WALK: """
SELECT edge_id, CASE WHEN u = $1 THEN v ELSE u END AS next_node,
       round(length_m::numeric, 2)::float8 AS length_m, highway, name, oneway
FROM osm.road_edge
WHERE graph_version = $2 AND walk_allowed AND u <> v
  AND (u = $1 OR v = $1)
ORDER BY edge_id
""",
    Mode.BIKE: """
SELECT edge_id, CASE WHEN u = $1 THEN v ELSE u END AS next_node,
       round(length_m::numeric, 2)::float8 AS length_m, highway, name, oneway
FROM osm.road_edge
WHERE graph_version = $2 AND bike_allowed AND u <> v
  AND (u = $1 OR (v = $1 AND NOT oneway))
ORDER BY edge_id
""",
}

_SQL_STREETS = """
SELECT name, count(*) AS segments, sum(length_m)::float8 AS total_m,
       ST_X(ST_ClosestPoint(ST_Collect(geom), ST_Centroid(ST_Collect(geom)))) AS center_lon,
       ST_Y(ST_ClosestPoint(ST_Collect(geom), ST_Centroid(ST_Collect(geom)))) AS center_lat,
       ST_XMin(ST_Extent(geom)) AS min_lon, ST_YMin(ST_Extent(geom)) AS min_lat,
       ST_XMax(ST_Extent(geom)) AS max_lon, ST_YMax(ST_Extent(geom)) AS max_lat
FROM osm.road_edge
WHERE graph_version = $2
  AND name ILIKE $1 ESCAPE '\\'
GROUP BY name
ORDER BY total_m DESC
LIMIT $3
"""


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _closest(rows: Sequence, max_distance_m: float):
    """Row with the smallest geodesic distance, if within max_distance_m."""
    best = min(rows, key=lambda r: r["distance_m"], default=None)
    if best is None or best["distance_m"] > max_distance_m:
        return None
    return best


class OsmRepository:
    def __init__(
        self,
        db: OsmDatabase,
        version_cache_ttl_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._db = db
        self._ttl = version_cache_ttl_s
        self._clock = clock
        self._version: GraphVersion | None = None
        self._version_fetched_at = 0.0
        self._version_lock = asyncio.Lock()

    async def _fetch(self, sql: str, *args):
        async with self._db.connection() as conn:
            return await conn.fetch(sql, *args)

    def _version_is_fresh(self) -> bool:
        return self._version is not None and self._clock() - self._version_fetched_at < self._ttl

    async def get_active_version(self, *, refresh: bool = False) -> GraphVersion:
        """Active graph_version, cached for version_cache_ttl_s (handoff 9.1).

        A version still in status 'building' is never returned, so readers
        keep the previous graph until the import switches it to 'active'.
        """
        if not refresh and self._version_is_fresh():
            return self._version  # type: ignore[return-value]
        async with self._version_lock:
            if not refresh and self._version_is_fresh():
                return self._version  # type: ignore[return-value]
            async with self._db.connection() as conn:
                row = await conn.fetchrow(_SQL_ACTIVE_VERSION)
            if row is None:
                raise CacheUnavailable("OSM Cache DB belum memiliki graph_version berstatus 'active'.")
            version = GraphVersion(**dict(row))
            if self._version is None or self._version.version != version.version:
                logger.info("Active graph_version: %s", version.version)
            self._version = version
            self._version_fetched_at = self._clock()
            return version

    async def read_road_graph(self, bbox: BBox, mode: Mode, *, include_geometry: bool = True) -> RoadGraph:
        """read_road_graph(bbox, mode) from Tabel 3.11 (R8 and R9).

        Returns edges of the active version whose geometry overlaps bbox and
        that the mode may use. Nodes are the endpoints of those edges, including
        endpoints just outside bbox, so no edge is left dangling. Rows are
        validated by graph.assemble_road_graph; rejects are in graph.rejected.
        """
        version = (await self.get_active_version()).version
        geometry = ",\n       ST_AsGeoJSON(e.geom, 6)::json -> 'coordinates' AS coordinates" if include_geometry else ""
        edges_sql = _SQL_GRAPH_EDGES.format(geometry=geometry, access=mode.access_column)
        nodes_sql = _SQL_GRAPH_NODES.format(access=mode.access_column)
        args = (version, *bbox.as_list())
        started = time.perf_counter()
        edge_rows, node_rows = await asyncio.gather(self._fetch(edges_sql, *args), self._fetch(nodes_sql, *args))

        graph = assemble_road_graph(version, mode, bbox, node_rows, edge_rows)
        if graph.rejected:
            logger.warning("graph_version %s: edges/nodes rejected while assembling RoadGraph: %s",
                           version, graph.rejected)
        logger.debug(
            "read_road_graph %s: %d nodes, %d edges in %.0f ms",
            mode.value, len(graph.nodes), len(graph.edges), (time.perf_counter() - started) * 1000,
        )
        return graph

    async def find_nearest_node(self, point: Point, mode: Mode, max_distance_m: float) -> NearestNode | None:
        """Closest node usable by mode within max_distance_m (handoff 9.2)."""
        version = (await self.get_active_version()).version
        sql = _SQL_NEAREST_NODE.format(access=mode.access_column)
        async with self._db.connection() as conn:
            rows = await conn.fetch(sql, point.lon, point.lat, version, max_distance_m * _DEG_PER_M)
        best = _closest(rows, max_distance_m)
        if best is None:
            return None
        return NearestNode(best["node_id"], best["lon"], best["lat"], best["distance_m"])

    async def find_nearest_edge(self, point: Point, mode: Mode, max_distance_m: float) -> NearestEdge | None:
        """Closest edge usable by mode within max_distance_m (handoff 9.3)."""
        version = (await self.get_active_version()).version
        sql = _SQL_NEAREST_EDGE.format(access=mode.access_column)
        async with self._db.connection() as conn:
            rows = await conn.fetch(sql, point.lon, point.lat, version, max_distance_m * _DEG_PER_M)
        best = _closest(rows, max_distance_m)
        if best is None:
            return None
        return NearestEdge(**dict(best))

    async def roads_geojson(
        self, bbox: BBox, mode: Mode | None = None, highways: Sequence[str] | None = None
    ) -> tuple[str, str]:
        """(graph_version, GeoJSON FeatureCollection text) of edges in bbox (handoff 9.4).

        The JSON is built by PostGIS and passed through untouched.
        """
        version = (await self.get_active_version()).version
        access = f"\n  AND e.{mode.access_column}" if mode is not None else ""
        sql = _SQL_ROADS_GEOJSON.format(access=access)
        async with self._db.connection() as conn:
            text = await conn.fetchval(sql, *bbox.as_list(), version, list(highways) if highways else None)
        return version, text

    async def get_node(self, node_id: int) -> NodeDetail | None:
        version = (await self.get_active_version()).version
        async with self._db.connection() as conn:
            row = await conn.fetchrow(_SQL_NODE, node_id, version)
        if row is None:
            return None
        return NodeDetail(row["node_id"], row["graph_version"], row["lon"], row["lat"], row["tags"] or {})

    async def get_edge(self, edge_id: int) -> EdgeDetail | None:
        """Edge by its globally unique edge_id (handoff 9.7). May be a retired version."""
        version = (await self.get_active_version()).version
        async with self._db.connection() as conn:
            row = await conn.fetchrow(_SQL_EDGE, edge_id, version)
        if row is None:
            return None
        data = dict(row)
        data["tags"] = data["tags"] or {}
        return EdgeDetail(**data)

    async def get_neighbors(self, node_id: int, mode: Mode) -> list[Neighbor]:
        """Edges the mode may take out of node_id, self-loops excluded (handoff 9.5)."""
        version = (await self.get_active_version()).version
        async with self._db.connection() as conn:
            rows = await conn.fetch(_SQL_NEIGHBORS[mode], node_id, version)
        return [Neighbor(**dict(r)) for r in rows]

    async def search_streets(self, query: str, limit: int = 10) -> list[StreetMatch]:
        """Named streets containing query, longest first (handoff 9.8)."""
        version = (await self.get_active_version()).version
        async with self._db.connection() as conn:
            rows = await conn.fetch(_SQL_STREETS, _like_pattern(query), version, limit)
        return [
            StreetMatch(
                name=r["name"], segments=r["segments"], total_m=r["total_m"] or 0.0,
                center_lon=r["center_lon"], center_lat=r["center_lat"],
                bbox=_maybe_bbox(r),
            )
            for r in rows
        ]

    async def ping(self) -> bool:
        return await self._db.ping()


def _maybe_bbox(row) -> BBox | None:
    try:
        return BBox(row["min_lon"], row["min_lat"], row["max_lon"], row["max_lat"])
    except (TypeError, ValueError):
        return None  # a single point or degenerate extent
