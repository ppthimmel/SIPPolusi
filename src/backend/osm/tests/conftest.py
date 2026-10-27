"""Shared fixtures: a synthetic 5 x 5 grid graph (Tabel 5.5) and a fake repository."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from osm.config import OsmSettings
from osm.models import (
    EARTH_RADIUS_M,
    BBox,
    EdgeDetail,
    GraphVersion,
    Mode,
    NearestEdge,
    NearestNode,
    Neighbor,
    NodeDetail,
    Point,
    RoadEdge,
    RoadGraph,
    RoadNode,
    StreetMatch,
)

VERSION = "jakarta-20261005"
ORIGIN_LON, ORIGIN_LAT = 106.80, -6.20
STEP_DEG = 0.001  # about 110 m


def node_id(row: int, col: int) -> int:
    return 100 + row * 5 + col


def offset_north(point: Point, metres: float) -> Point:
    """Point exactly `metres` north on the haversine sphere."""
    return Point(point.lon, point.lat + math.degrees(metres / EARTH_RADIUS_M))


def edge(edge_id: int, u: int, v: int, *, walk=True, bike=True, oneway=False, length_m=110.0) -> RoadEdge:
    return RoadEdge(edge_id=edge_id, u=u, v=v, length_m=length_m, highway="residential", name=None,
                    oneway=oneway, walk_allowed=walk, bike_allowed=bike)


def grid_graph(mode: Mode = Mode.WALK, *, with_island: bool = True) -> RoadGraph:
    """5 x 5 grid, edges between horizontal and vertical neighbours, plus an
    unconnected two-node island 1 km east when with_island is set."""
    nodes: dict[int, RoadNode] = {}
    for r in range(5):
        for c in range(5):
            nodes[node_id(r, c)] = RoadNode(node_id(r, c), ORIGIN_LON + c * STEP_DEG, ORIGIN_LAT + r * STEP_DEG)
    edges: list[RoadEdge] = []
    next_id = 1
    for r in range(5):
        for c in range(5):
            if c < 4:
                edges.append(edge(next_id, node_id(r, c), node_id(r, c + 1)))
                next_id += 1
            if r < 4:
                edges.append(edge(next_id, node_id(r, c), node_id(r + 1, c)))
                next_id += 1
    if with_island:
        nodes[900] = RoadNode(900, ORIGIN_LON + 0.010, ORIGIN_LAT)
        nodes[901] = RoadNode(901, ORIGIN_LON + 0.011, ORIGIN_LAT)
        edges.append(edge(999, 900, 901))
    bbox = BBox(ORIGIN_LON - 0.01, ORIGIN_LAT - 0.01, ORIGIN_LON + 0.02, ORIGIN_LAT + 0.01)
    return RoadGraph(graph_version=VERSION, mode=mode, bbox=bbox, nodes=nodes, edges=edges)


@pytest.fixture
def grid() -> RoadGraph:
    return grid_graph()


ACTIVE = GraphVersion(
    version=VERSION, status="active",
    osm_timestamp=datetime(2026, 10, 5, 9, 50, 56, tzinfo=timezone.utc),
    built_at=datetime(2026, 10, 5, 10, 58, 14, tzinfo=timezone.utc),
    node_count=299067, edge_count=373352,
)


class FakeRepository:
    """Stands in for OsmRepository; set `fail` to an exception to make every call raise it."""

    def __init__(self, graph: RoadGraph | None = None):
        self.graph = graph or grid_graph()
        self.fail: Exception | None = None
        self.calls: list[str] = []

    def _check(self, name: str) -> None:
        self.calls.append(name)
        if self.fail is not None:
            raise self.fail

    async def get_active_version(self, *, refresh: bool = False) -> GraphVersion:
        self._check("get_active_version")
        return ACTIVE

    async def ping(self) -> bool:
        return self.fail is None

    async def read_road_graph(self, bbox: BBox, mode: Mode, *, include_geometry: bool = True) -> RoadGraph:
        self._check("read_road_graph")
        g = self.graph
        return RoadGraph(g.graph_version, mode, bbox, dict(g.nodes), [e for e in g.edges if mode.allows(e)])

    async def find_nearest_node(self, point: Point, mode: Mode, max_distance_m: float):
        self._check("find_nearest_node")
        if point.lat > ORIGIN_LAT + 0.05:
            return None
        return NearestNode(node_id(0, 0), ORIGIN_LON, ORIGIN_LAT, 12.5)

    async def find_nearest_edge(self, point: Point, mode: Mode, max_distance_m: float):
        self._check("find_nearest_edge")
        return NearestEdge(1, node_id(0, 0), node_id(0, 1), "Jalan Uji", "residential", 110.0, False,
                           4.2, 0.25, ORIGIN_LON + 0.00025, ORIGIN_LAT)

    async def roads_geojson(self, bbox: BBox, mode=None, highways=None):
        self._check("roads_geojson")
        return VERSION, '{"type": "FeatureCollection", "graph_version": "%s", "features": []}' % VERSION

    async def get_node(self, nid: int):
        self._check("get_node")
        if nid != node_id(0, 0):
            return None
        return NodeDetail(nid, VERSION, ORIGIN_LON, ORIGIN_LAT, {"highway": "traffic_signals"})

    async def get_edge(self, edge_id: int):
        self._check("get_edge")
        if edge_id != 1:
            return None
        return EdgeDetail(1, VERSION, node_id(0, 0), node_id(0, 1), 110.0, "residential", "Jalan Uji",
                          False, True, True, 123, {"highway": "residential", "lanes": "2"},
                          {"type": "LineString", "coordinates": [[ORIGIN_LON, ORIGIN_LAT], [ORIGIN_LON + STEP_DEG, ORIGIN_LAT]]})

    async def get_neighbors(self, nid: int, mode: Mode):
        self._check("get_neighbors")
        return [Neighbor(1, node_id(0, 1), 110.0, "residential", None, False),
                Neighbor(2, node_id(1, 0), 110.0, "residential", None, False)]

    async def read_edge_geometries(self, edge_ids, graph_version):
        self._check("read_edge_geometries")
        out = {}
        for e in self.graph.edges:
            if e.edge_id in edge_ids:
                a, b = self.graph.nodes[e.u], self.graph.nodes[e.v]
                out[e.edge_id] = [[a.lon, a.lat], [b.lon, b.lat]]
        return out

    async def search_streets(self, query: str, limit: int = 10):
        self._check("search_streets")
        return [StreetMatch("Jalan Jenderal Sudirman", 212, 10649.4, 106.82, -6.21,
                            BBox(106.80, -6.23, 106.83, -6.19))]


@pytest.fixture
def settings() -> OsmSettings:
    return OsmSettings(_env_file=None, DATABASE_URL="postgresql://unused@localhost/unused")
