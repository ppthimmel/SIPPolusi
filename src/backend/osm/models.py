"""Domain types for the OSM Cache DB.

Column names follow the live schema `osm` (handoff_database.md); structure
names follow Tabel 3.3 of the design document. Coordinates are WGS 84
(EPSG:4326). Whenever a coordinate pair is a tuple it is (lon, lat), the same
order as GeoJSON and PostGIS (x = lon, y = lat).
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

EARTH_RADIUS_M = 6_371_008.8
# Metres per degree of latitude near the equator and of longitude at the
# equator (WGS 84). 110 574 is the smallest of the two, so expanding a box by
# margin_m / 110 574 degrees always grows it by at least margin_m.
_M_PER_DEG_LAT = 110_574.0
_M_PER_DEG_LON_EQUATOR = 111_320.0
_M_PER_DEG_MIN = _M_PER_DEG_LAT


@dataclass(frozen=True, slots=True)
class Point:
    lon: float
    lat: float

    def __post_init__(self) -> None:
        if not (-180.0 <= self.lon <= 180.0) or not (-90.0 <= self.lat <= 90.0):
            raise ValueError("Point is outside WGS 84 range (lon -180..180, lat -90..90)")


@dataclass(frozen=True, slots=True)
class BBox:
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def __post_init__(self) -> None:
        Point(self.min_lon, self.min_lat)
        Point(self.max_lon, self.max_lat)
        if self.min_lon >= self.max_lon or self.min_lat >= self.max_lat:
            raise ValueError("BBox needs min_lon < max_lon and min_lat < max_lat")

    @classmethod
    def parse(cls, text: str) -> BBox:
        """Parse "min_lon,min_lat,max_lon,max_lat"."""
        parts = text.split(",")
        if len(parts) != 4:
            raise ValueError("bbox must be min_lon,min_lat,max_lon,max_lat")
        try:
            values = [float(p) for p in parts]
        except ValueError as exc:
            raise ValueError("bbox values must be numbers") from exc
        if not all(math.isfinite(v) for v in values):
            raise ValueError("bbox values must be finite")
        return cls(*values)

    @classmethod
    def around(cls, points: Iterable[Point], margin_m: float) -> BBox:
        """Smallest box holding every point, grown by at least margin_m on each side."""
        pts = list(points)
        if not pts:
            raise ValueError("BBox.around needs at least one point")
        mid_lat = sum(p.lat for p in pts) / len(pts)
        dlat = margin_m / _M_PER_DEG_MIN
        dlon = margin_m / (_M_PER_DEG_MIN * max(math.cos(math.radians(mid_lat)), 1e-6))
        return cls(
            max(min(p.lon for p in pts) - dlon, -180.0),
            max(min(p.lat for p in pts) - dlat, -90.0),
            min(max(p.lon for p in pts) + dlon, 180.0),
            min(max(p.lat for p in pts) + dlat, 90.0),
        )

    def contains(self, point: Point) -> bool:
        return self.min_lon <= point.lon <= self.max_lon and self.min_lat <= point.lat <= self.max_lat

    def area_km2(self) -> float:
        mid_lat = math.radians((self.min_lat + self.max_lat) / 2)
        width = (self.max_lon - self.min_lon) * _M_PER_DEG_LON_EQUATOR * math.cos(mid_lat)
        height = (self.max_lat - self.min_lat) * _M_PER_DEG_LAT
        return width * height / 1e6

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]


def haversine_m(a: Point, b: Point) -> float:
    """Great-circle distance in metres."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


@dataclass(frozen=True, slots=True)
class GraphVersion:
    version: str
    status: str
    osm_timestamp: datetime | None
    built_at: datetime | None
    node_count: int | None
    edge_count: int | None


@dataclass(frozen=True, slots=True)
class RoadNode:
    node_id: int
    lon: float
    lat: float
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def point(self) -> Point:
        return Point(self.lon, self.lat)


@dataclass(frozen=True, slots=True)
class RoadEdge:
    edge_id: int
    u: int
    v: int
    length_m: float | None
    highway: str | None
    name: str | None
    oneway: bool
    walk_allowed: bool
    bike_allowed: bool
    osm_way_id: int | None = None
    # [(lon, lat), ...] drawn from u to v; None when geometry was not requested.
    coordinates: list[list[float]] | None = None

    @property
    def is_self_loop(self) -> bool:
        return self.u == self.v


class Mode(str, Enum):
    """Travel mode and its access rules (handoff_database.md, bagian 3 butir 7)."""

    WALK = "walk"
    BIKE = "bike"

    @property
    def access_column(self) -> str:
        return "walk_allowed" if self is Mode.WALK else "bike_allowed"

    def allows(self, edge: RoadEdge) -> bool:
        return edge.walk_allowed if self is Mode.WALK else edge.bike_allowed

    def directions(self, edge: RoadEdge) -> Iterator[tuple[int, int]]:
        """(from, to) pairs this mode may travel on the edge.

        Pedestrians ignore oneway. Bicycles follow it: u -> v always, v -> u
        only when oneway is false. Geometry is always stored u -> v.
        """
        if not self.allows(edge):
            return
        yield edge.u, edge.v
        if self is Mode.WALK or not edge.oneway:
            yield edge.v, edge.u


@dataclass(frozen=True, slots=True)
class ModeProfile:
    mode: Mode
    speed_mps: float

    @property
    def access_column(self) -> str:
        return self.mode.access_column


@dataclass(slots=True)
class RoadGraph:
    """Nodes and edges of the active graph_version inside a bbox, filtered by mode."""

    graph_version: str
    mode: Mode
    bbox: BBox
    nodes: dict[int, RoadNode]
    edges: list[RoadEdge]


@dataclass(slots=True)
class Subgraph:
    """Request subgraph: the part of RoadGraph reachable from the origin node.

    Pollution weights (I_e,t, confidence score) are attached later by the
    Backend's attach_edge_weights; this module only fills the topology.
    """

    graph_version: str
    mode: Mode
    bbox: BBox
    origin_node: int
    destination_node: int
    origin_snap_distance_m: float
    destination_snap_distance_m: float
    nodes: dict[int, RoadNode]
    edges: list[RoadEdge]


@dataclass(frozen=True, slots=True)
class SnapResult:
    node_id: int
    distance_m: float


@dataclass(frozen=True, slots=True)
class NearestNode:
    node_id: int
    lon: float
    lat: float
    distance_m: float


@dataclass(frozen=True, slots=True)
class NearestEdge:
    edge_id: int
    u: int
    v: int
    name: str | None
    highway: str | None
    length_m: float | None
    oneway: bool
    distance_m: float
    fraction_along: float
    snapped_lon: float
    snapped_lat: float


@dataclass(frozen=True, slots=True)
class NodeDetail:
    node_id: int
    graph_version: str
    lon: float
    lat: float
    tags: dict[str, str]


@dataclass(frozen=True, slots=True)
class EdgeDetail:
    edge_id: int
    graph_version: str
    u: int
    v: int
    length_m: float | None
    highway: str | None
    name: str | None
    oneway: bool
    walk_allowed: bool
    bike_allowed: bool
    osm_way_id: int | None
    tags: dict[str, str]
    geometry: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class Neighbor:
    edge_id: int
    next_node: int
    length_m: float | None
    highway: str | None
    name: str | None
    oneway: bool


@dataclass(frozen=True, slots=True)
class StreetMatch:
    name: str
    segments: int
    total_m: float
    center_lon: float
    center_lat: float
    bbox: BBox | None


# osm.road_edge.highway values present in the import (handoff bagian 8).
HIGHWAY_VALUES = frozenset({
    "residential", "living_street", "service", "tertiary", "footway", "primary",
    "secondary", "trunk", "unclassified", "path", "busway", "steps",
    "motorway_link", "motorway", "primary_link", "trunk_link", "secondary_link",
    "tertiary_link", "pedestrian", "track", "cycleway", "corridor",
})
