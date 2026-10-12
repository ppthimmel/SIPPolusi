"""REST endpoints over the OSM Cache DB, mounted at /v1/osm.

All endpoints are read-only. Errors use the uniform schema in errors.py.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from .config import OsmSettings
from .errors import InvalidRequest, NotFound, OutsideStudyArea, PointNotOnNetwork
from .graph import assemble_request_subgraph, compute_request_bbox
from .models import HIGHWAY_VALUES, BBox, Mode, Point
from .repository import OsmRepository
from .schemas import (
    EdgeOut,
    GraphVersionOut,
    NearestEdgeOut,
    NearestNodeOut,
    NeighborOut,
    NeighborsOut,
    NodeOut,
    RoadGraphOut,
    StreetOut,
    StreetsOut,
    SubgraphOut,
    SubgraphRequest,
)

router = APIRouter(prefix="/v1/osm", tags=["osm"])

GEOJSON_MEDIA_TYPE = "application/geo+json"
# Responses only change when a new graph_version is imported.
_CACHE_CONTROL = "public, max-age=300"


def get_repository(request: Request) -> OsmRepository:
    return request.app.state.osm_repository


def get_osm_settings(request: Request) -> OsmSettings:
    return request.app.state.osm_settings


Repo = Annotated[OsmRepository, Depends(get_repository)]
Settings = Annotated[OsmSettings, Depends(get_osm_settings)]
Lon = Annotated[float, Query(ge=-180, le=180, description="Longitude WGS 84")]
Lat = Annotated[float, Query(ge=-90, le=90, description="Latitude WGS 84 (negative in Jakarta)")]
BBoxParam = Annotated[str, Query(description="min_lon,min_lat,max_lon,max_lat", examples=["106.82,-6.18,106.83,-6.17"])]
SnapDistance = Annotated[float | None, Query(gt=0, le=1000, description="Default OSM_SNAP_MAX_DISTANCE_M (200 m)")]
NodeId = Annotated[int, Path(ge=1)]
EdgeId = Annotated[int, Path(ge=1)]


def _parse_bbox(text: str, settings: OsmSettings) -> BBox:
    try:
        bbox = BBox.parse(text)
    except ValueError as exc:
        raise InvalidRequest("bbox tidak valid; gunakan min_lon,min_lat,max_lon,max_lat (WGS 84) dengan min < max.") from exc
    if bbox.area_km2() > settings.max_bbox_km2:
        raise InvalidRequest(
            f"bbox terlalu luas ({bbox.area_km2():.1f} km²); batas {settings.max_bbox_km2:g} km²."
        )
    return bbox


def _point_in_study_area(lon: float, lat: float, settings: OsmSettings, label: str = "Titik") -> Point:
    point = Point(lon, lat)
    if not settings.study_area.contains(point):
        raise OutsideStudyArea(f"{label} berada di luar wilayah studi (DKI Jakarta).")
    return point


@router.get("/version", response_model=GraphVersionOut, summary="Active graph_version")
async def get_version(repo: Repo):
    return GraphVersionOut.of(await repo.get_active_version())


@router.get("/graph", response_model=RoadGraphOut, summary="read_road_graph(bbox, mode)")
async def read_graph(
    repo: Repo,
    settings: Settings,
    bbox: BBoxParam,
    mode: Mode,
    include_geometry: bool = True,
):
    """RoadGraph of the active version inside bbox, only edges the mode may use."""
    box = _parse_bbox(bbox, settings)
    graph = await repo.read_road_graph(box, mode, include_geometry=include_geometry)
    return RoadGraphOut.build(graph.graph_version, graph.mode, graph.bbox, graph.nodes, graph.edges)


@router.get(
    "/roads",
    response_class=Response,
    summary="Roads in a map viewport as GeoJSON",
    responses={200: {"content": {GEOJSON_MEDIA_TYPE: {}}}},
)
async def get_roads(
    repo: Repo,
    settings: Settings,
    bbox: BBoxParam,
    mode: Mode | None = None,
    highway: Annotated[list[str] | None, Query(description="Repeat to filter, e.g. highway=primary&highway=secondary")] = None,
):
    box = _parse_bbox(bbox, settings)
    if highway:
        unknown = sorted(set(highway) - HIGHWAY_VALUES)
        if unknown:
            raise InvalidRequest(f"Nilai highway tidak dikenal: {', '.join(unknown)}")
    version, body = await repo.roads_geojson(box, mode, highway)
    return Response(
        content=body,
        media_type=GEOJSON_MEDIA_TYPE,
        headers={"Cache-Control": _CACHE_CONTROL, "X-Graph-Version": version},
    )


@router.get("/nearest-node", response_model=NearestNodeOut, summary="Nearest node usable by mode")
async def nearest_node(repo: Repo, settings: Settings, lon: Lon, lat: Lat, mode: Mode,
                       max_distance_m: SnapDistance = None):
    point = _point_in_study_area(lon, lat, settings)
    radius = max_distance_m or settings.snap_max_distance_m
    node = await repo.find_nearest_node(point, mode, radius)
    if node is None:
        raise PointNotOnNetwork(
            f"Tidak ada simpul yang dapat dilalui moda {mode.value} dalam radius {radius:g} m dari titik."
        )
    version = (await repo.get_active_version()).version
    return NearestNodeOut.of(version, mode, node)


@router.get("/nearest-edge", response_model=NearestEdgeOut, summary="Snap a position to the nearest usable edge")
async def nearest_edge(repo: Repo, settings: Settings, lon: Lon, lat: Lat, mode: Mode,
                       max_distance_m: SnapDistance = None):
    point = _point_in_study_area(lon, lat, settings)
    radius = max_distance_m or settings.snap_max_distance_m
    edge = await repo.find_nearest_edge(point, mode, radius)
    if edge is None:
        raise PointNotOnNetwork(
            f"Tidak ada ruas yang dapat dilalui moda {mode.value} dalam radius {radius:g} m dari titik."
        )
    version = (await repo.get_active_version()).version
    return NearestEdgeOut.of(version, mode, edge)


@router.get("/nodes/{node_id}", response_model=NodeOut, summary="Node of the active version")
async def get_node(repo: Repo, node_id: NodeId):
    node = await repo.get_node(node_id)
    if node is None:
        raise NotFound(f"Simpul {node_id} tidak ada pada graph_version aktif.")
    return NodeOut.of(node)


@router.get("/nodes/{node_id}/neighbors", response_model=NeighborsOut, summary="Edges the mode may take out of a node")
async def get_neighbors(repo: Repo, node_id: NodeId, mode: Mode):
    node = await repo.get_node(node_id)
    if node is None:
        raise NotFound(f"Simpul {node_id} tidak ada pada graph_version aktif.")
    neighbors = await repo.get_neighbors(node_id, mode)
    return NeighborsOut(
        graph_version=node.graph_version, mode=mode, node_id=node_id,
        neighbors=[NeighborOut.of(n) for n in neighbors],
    )


@router.get("/edges/{edge_id}", response_model=EdgeOut, summary="Edge detail with OSM tags and geometry")
async def get_edge(repo: Repo, edge_id: EdgeId):
    edge = await repo.get_edge(edge_id)
    if edge is None:
        raise NotFound(f"Ruas {edge_id} tidak ditemukan.")
    return EdgeOut.of(edge)


@router.get("/streets", response_model=StreetsOut, summary="Search named streets")
async def search_streets(
    repo: Repo,
    q: Annotated[str, Query(min_length=3, max_length=100, description="Part of the street name")],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
):
    query = q.strip()
    if len(query) < 3:
        raise InvalidRequest("q minimal 3 karakter.")
    results = await repo.search_streets(query, limit)
    version = (await repo.get_active_version()).version
    return StreetsOut(graph_version=version, query=query, results=[StreetOut.of(s) for s in results])


@router.post("/subgraph", response_model=SubgraphOut, summary="Request subgraph for an origin-destination pair")
async def build_subgraph(repo: Repo, settings: Settings, body: SubgraphRequest):
    """OSM part of R8 to R10: compute_request_bbox, read_road_graph, snap_to_graph
    and assemble_request_subgraph. Pollution weights are not attached here."""
    origin = _point_in_study_area(body.origin.lon, body.origin.lat, settings, "Titik asal")
    destination = _point_in_study_area(body.destination.lon, body.destination.lat, settings, "Titik tujuan")
    bbox = compute_request_bbox(origin, destination, body.buffer_m)
    road_graph = await repo.read_road_graph(bbox, body.mode, include_geometry=body.include_graph)
    subgraph = await run_in_threadpool(
        assemble_request_subgraph, road_graph, origin, destination, settings.snap_max_distance_m
    )
    return SubgraphOut.of(subgraph, body.include_graph)
