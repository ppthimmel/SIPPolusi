"""API request and response models for /v1/osm.

Points use {lat, lon} like Point in Tabel 3.3; geometries are GeoJSON with
[lon, lat] order (RFC 7946). Distances are metres.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, model_validator

from .models import (
    BBox,
    EdgeDetail,
    GraphVersion,
    Mode,
    NearestEdge,
    NearestNode,
    Neighbor,
    NodeDetail,
    RoadEdge,
    RoadNode,
    StreetMatch,
    Subgraph,
)


class PointModel(BaseModel):
    lat: float = Field(ge=-90, le=90, strict=True, description="Latitude WGS 84, degrees")
    lon: float = Field(ge=-180, le=180, strict=True, description="Longitude WGS 84, degrees")


def _lonlat(value: list[float]) -> list[float]:
    PointModel(lon=value[0], lat=value[1])
    return value


LonLat = Annotated[
    list[Annotated[float, Field(strict=True)]],
    Field(min_length=2, max_length=2, json_schema_extra={
        "prefixItems": [{"type": "number", "minimum": -180, "maximum": 180},
                        {"type": "number", "minimum": -90, "maximum": 90}],
        "items": False,
    }),
    AfterValidator(_lonlat),
]


def _bbox(value: list[float]) -> list[float]:
    BBox(*value)
    return value


BBoxCoordinates = Annotated[list[float], Field(min_length=4, max_length=4), AfterValidator(_bbox)]


class GraphVersionOut(BaseModel):
    version: str
    status: str
    osm_timestamp: datetime | None
    built_at: datetime | None
    node_count: int | None
    edge_count: int | None

    @classmethod
    def of(cls, v: GraphVersion) -> GraphVersionOut:
        return cls(
            version=v.version, status=v.status, osm_timestamp=v.osm_timestamp,
            built_at=v.built_at, node_count=v.node_count, edge_count=v.edge_count,
        )


class NodeOut(BaseModel):
    node_id: int
    graph_version: str
    lon: float
    lat: float
    tags: dict[str, str]

    @classmethod
    def of(cls, n: NodeDetail) -> NodeOut:
        return cls(node_id=n.node_id, graph_version=n.graph_version, lon=n.lon, lat=n.lat, tags=n.tags)


class EdgeOut(BaseModel):
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

    @classmethod
    def of(cls, e: EdgeDetail) -> EdgeOut:
        return cls(**{name: getattr(e, name) for name in cls.model_fields})


class NearestNodeOut(BaseModel):
    graph_version: str
    mode: Mode
    node_id: int
    lon: float
    lat: float
    distance_m: float

    @classmethod
    def of(cls, version: str, mode: Mode, n: NearestNode) -> NearestNodeOut:
        return cls(graph_version=version, mode=mode, node_id=n.node_id, lon=n.lon, lat=n.lat, distance_m=n.distance_m)


class NearestEdgeOut(BaseModel):
    graph_version: str
    mode: Mode
    edge_id: int
    u: int
    v: int
    name: str | None
    highway: str | None
    length_m: float | None
    oneway: bool
    distance_m: float
    fraction_along: float = Field(description="Position on the edge: 0 at u, 1 at v")
    snapped_point: PointModel

    @classmethod
    def of(cls, version: str, mode: Mode, e: NearestEdge) -> NearestEdgeOut:
        return cls(
            graph_version=version, mode=mode, edge_id=e.edge_id, u=e.u, v=e.v, name=e.name,
            highway=e.highway, length_m=e.length_m, oneway=e.oneway, distance_m=e.distance_m,
            fraction_along=e.fraction_along, snapped_point=PointModel(lat=e.snapped_lat, lon=e.snapped_lon),
        )


class NeighborOut(BaseModel):
    edge_id: int
    next_node: int
    length_m: float | None
    highway: str | None
    name: str | None
    oneway: bool

    @classmethod
    def of(cls, n: Neighbor) -> NeighborOut:
        return cls(**{name: getattr(n, name) for name in cls.model_fields})


class NeighborsOut(BaseModel):
    graph_version: str
    mode: Mode
    node_id: int
    neighbors: list[NeighborOut]


class StreetOut(BaseModel):
    name: str
    segments: int
    total_m: float
    center: PointModel
    bbox: list[float] | None = Field(description="[min_lon, min_lat, max_lon, max_lat]")

    @classmethod
    def of(cls, s: StreetMatch) -> StreetOut:
        return cls(
            name=s.name, segments=s.segments, total_m=round(s.total_m, 1),
            center=PointModel(lat=s.center_lat, lon=s.center_lon),
            bbox=s.bbox.as_list() if s.bbox else None,
        )


class StreetsOut(BaseModel):
    graph_version: str
    query: str
    results: list[StreetOut]


class GraphNodeOut(BaseModel):
    node_id: int
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    tags: dict[str, str]

    @classmethod
    def of(cls, n: RoadNode) -> GraphNodeOut:
        return cls(node_id=n.node_id, lon=n.lon, lat=n.lat, tags=n.tags)


class GraphEdgeOut(BaseModel):
    edge_id: int
    u: int
    v: int
    length_m: float = Field(gt=0, allow_inf_nan=False, description="Length, metres")
    highway: str | None
    name: str | None
    oneway: bool
    walk_allowed: bool
    bike_allowed: bool
    osm_way_id: int | None
    coordinates: list[LonLat] | None = Field(min_length=2, description="[[lon, lat], ...] from u to v; null if not requested")

    @classmethod
    def of(cls, e: RoadEdge) -> GraphEdgeOut:
        return cls(**{name: getattr(e, name) for name in cls.model_fields})


class RoadGraphOut(BaseModel):
    graph_version: str = Field(min_length=1)
    mode: Mode
    bbox: BBoxCoordinates
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    rejected: dict[str, int] = Field(description="Rows left out by validation, per reason")
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]

    @model_validator(mode="after")
    def validate_topology(self) -> RoadGraphOut:
        nodes = {n.node_id for n in self.nodes}
        if len(nodes) != len(self.nodes) or len({e.edge_id for e in self.edges}) != len(self.edges):
            raise ValueError("RoadGraph node_id and edge_id must be unique")
        if self.node_count != len(self.nodes) or self.edge_count != len(self.edges):
            raise ValueError("RoadGraph counts must match nodes and edges")
        for edge in self.edges:
            if edge.u not in nodes or edge.v not in nodes:
                raise ValueError("RoadGraph edge endpoints must refer to nodes")
            if not (edge.walk_allowed if self.mode is Mode.WALK else edge.bike_allowed):
                raise ValueError("RoadGraph edges must allow the requested mode")
        return self

    @classmethod
    def build(cls, graph_version: str, mode: Mode, bbox: BBox, nodes: dict[int, RoadNode],
              edges: list[RoadEdge], rejected: dict[str, int] | None = None) -> RoadGraphOut:
        return cls(
            graph_version=graph_version, mode=mode, bbox=bbox.as_list(),
            node_count=len(nodes), edge_count=len(edges), rejected=rejected or {},
            nodes=[GraphNodeOut.of(n) for n in nodes.values()],
            edges=[GraphEdgeOut.of(e) for e in edges],
        )


class SubgraphRequest(BaseModel):
    origin: PointModel
    destination: PointModel
    mode: Mode
    buffer_m: float | None = Field(None, ge=0, le=10_000, description="Default max(500 m, 25% of distance)")
    include_graph: bool = Field(False, description="Also return the subgraph nodes and edges")


class SubgraphOut(BaseModel):
    graph_version: str
    mode: Mode
    bbox: list[float]
    origin_node: int
    destination_node: int
    origin_snap_distance_m: float
    destination_snap_distance_m: float
    node_count: int
    edge_count: int
    rejected: dict[str, int] = Field(description="Rows left out by validation while reading the bbox, per reason")
    graph: RoadGraphOut | None = None

    @classmethod
    def of(cls, s: Subgraph, include_graph: bool) -> SubgraphOut:
        return cls(
            graph_version=s.graph_version, mode=s.mode, bbox=s.bbox.as_list(),
            origin_node=s.origin_node, destination_node=s.destination_node,
            origin_snap_distance_m=round(s.origin_snap_distance_m, 2),
            destination_snap_distance_m=round(s.destination_snap_distance_m, 2),
            node_count=len(s.nodes), edge_count=len(s.edges), rejected=s.rejected,
            graph=RoadGraphOut.build(s.graph_version, s.mode, s.bbox, s.nodes, s.edges) if include_graph else None,
        )


class HealthOut(BaseModel):
    status: str
    database: str
    graph_version: str | None
    pollution_time_window: datetime | None = Field(None, description="Time window complete terakhir; null bila belum ada")
    pollution_data_stale: bool | None = None
