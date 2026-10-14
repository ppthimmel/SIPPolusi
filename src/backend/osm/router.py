"""REST endpoints over the OSM Cache DB, mounted at /v1/osm.

All endpoints are read-only. Errors use the uniform schema in errors.py.
"""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, Request, Response
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
# Street search needs 3 consecutive letters/digits: pg_trgm extracts no trigram
# from shorter runs, so the query would fall back to a full table scan.
_SEARCHABLE_RE = re.compile(r"[^\W_]{3}")
# Responses only change when a new graph_version is imported.
_CACHE_CONTROL = "public, max-age=300"


def get_repository(request: Request) -> OsmRepository:
    return request.app.state.osm_repository


def get_osm_settings(request: Request) -> OsmSettings:
    return request.app.state.osm_settings


Repo = Annotated[OsmRepository, Depends(get_repository)]
Settings = Annotated[OsmSettings, Depends(get_osm_settings)]
# openapi_examples prefill "Try it out" in /docs. Every value below exists in
# graph_version jakarta-20261005; README "Contoh input per endpoint" lists the
# expected responses.
def _ex(summary: str, value) -> dict:
    return {"summary": summary, "value": value}


# node_id / edge_id are PostgreSQL bigint; larger values must be a 400, not a driver error.
_MAX_BIGINT = 2**63 - 1


Lon = Annotated[float, Query(ge=-180, le=180, description="Longitude WGS 84",
                             openapi_examples={"monas": _ex("Monas", 106.8272)})]
Lat = Annotated[float, Query(ge=-90, le=90, description="Latitude WGS 84 (negative in Jakarta)",
                             openapi_examples={"monas": _ex("Monas", -6.1754)})]
ModeParam = Annotated[Mode, Query(description="walk = jalan kaki, bike = sepeda",
                                  openapi_examples={"walk": _ex("Jalan kaki", "walk"),
                                                    "bike": _ex("Sepeda", "bike")})]
BBoxParam = Annotated[str, Query(
    description="min_lon,min_lat,max_lon,max_lat (WGS 84), maks. OSM_MAX_BBOX_KM2 (25 km²)",
    openapi_examples={
        "monas_kecil": _ex("Sekitar Monas, ±0,2 km²", "106.825,-6.177,106.829,-6.173"),
        "monas_1km": _ex("Sekitar Monas, ±1,2 km²", "106.822,-6.180,106.832,-6.170"),
        "laut_kosong": _ex("Di laut, tanpa jalan → 200 kosong", "106.85,-6.09,106.86,-6.08"),
        "terlalu_luas": _ex("Seluruh Jakarta → 400 invalid_request", "106.68,-6.38,106.98,-6.08"),
        "format_salah": _ex("Hanya 3 angka → 400 invalid_request", "106.82,-6.18,106.83"),
        "terbalik": _ex("min > max → 400 invalid_request", "106.83,-6.17,106.82,-6.18"),
        "bukan_angka": _ex("Bukan angka → 400 invalid_request", "a,b,c,d"),
    },
)]
SnapDistance = Annotated[float | None, Query(
    gt=0, le=1000, description="Radius pencarian (m), 0 < nilai ≤ 1000; default OSM_SNAP_MAX_DISTANCE_M (200 m)",
    openapi_examples={"default": _ex("200 m (default)", 200),
                      "terlalu_kecil": _ex("5 m, simpul terdekat Monas 11,7 m → 422 point_not_on_network", 5),
                      "maksimum": _ex("1000 m (batas atas)", 1000),
                      "melebihi": _ex("1001 m → 400 invalid_request", 1001)},
)]
NodeId = Annotated[int, Path(ge=1, le=_MAX_BIGINT, description="node_id OSM pada versi aktif",
                             openapi_examples={"lampu": _ex("Lampu lalu lintas dekat Monas", 7114808048),
                                               "monas": _ex("Simpul terdekat Monas", 10792891204),
                                               "terisolasi": _ex("Simpul di komponen terisolasi (18 simpul)", 3776134358),
                                               "tidak_ada": _ex("Tidak ada → 404 not_found", 42),
                                               "nol": _ex("0 → 400 invalid_request", 0),
                                               "melebihi_bigint": _ex("> batas bigint → 400 invalid_request",
                                                                      _MAX_BIGINT + 1)})]
EdgeId = Annotated[int, Path(ge=1, le=_MAX_BIGINT, description="edge_id (unik global)",
                             openapi_examples={"merdeka_barat": _ex("Jalan Medan Merdeka Barat", 87108),
                                               "tidak_ada": _ex("Tidak ada → 404 not_found", 999999999),
                                               "negatif": _ex("Negatif → 400 invalid_request", -1),
                                               "melebihi_bigint": _ex("> batas bigint → 400 invalid_request",
                                                                      _MAX_BIGINT + 1)})]
HighwayParam = Annotated[list[str] | None, Query(
    description="Filter tipe jalan; ulangi untuk beberapa nilai, mis. highway=primary&highway=secondary",
    openapi_examples={"pejalan": _ex("Jalur pejalan kaki", ["footway", "pedestrian"]),
                      "utama": _ex("Jalan utama (pakai bbox ±1,2 km²)", ["primary", "secondary"]),
                      "tidak_dikenal": _ex("Nilai tidak dikenal → 400 invalid_request", ["rocket"])},
)]
StreetQuery = Annotated[str, Query(
    min_length=3, max_length=100, description="Bagian nama jalan, minimal 3 huruf/angka berurutan",
    openapi_examples={"sudirman": _ex("Sudirman", "sudirman"),
                      "thamrin": _ex("Husni Thamrin", "Husni Thamrin"),
                      "huruf_besar": _ex("Huruf besar, tidak peka besar-kecil", "SUDIRMAN"),
                      "spasi_tepi": _ex("Spasi di tepi dipangkas", "   sudirman   "),
                      "wildcard": _ex("% diperlakukan sebagai teks → 200 kosong", "sudirman%"),
                      "tidak_ditemukan": _ex("Tidak ada jalan bernama ini → 200 kosong", "zzzqqq"),
                      "terlalu_pendek": _ex("2 karakter → 400 invalid_request", "ab"),
                      "tanpa_huruf": _ex("Tanpa huruf → 400 invalid_request", "%%%"),
                      "spasi_saja": _ex("Hanya spasi → 400 invalid_request", "     ")},
)]
StreetLimit = Annotated[int, Query(ge=1, le=20, description="Jumlah hasil, 1–20",
                                   openapi_examples={"default": _ex("10 (default)", 10),
                                                     "satu": _ex("1", 1),
                                                     "nol": _ex("0 → 400 invalid_request", 0),
                                                     "melebihi": _ex("21 → 400 invalid_request", 21)})]
SubgraphBody = Annotated[SubgraphRequest, Body(openapi_examples={
    "walk_monas_bundaran_hi": _ex("Jalan kaki Monas → Bundaran HI (±2,2 km)", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.1950, "lon": 106.8230},
        "mode": "walk"}),
    "bike_monas_bundaran_hi": _ex("Sepeda Monas → Bundaran HI", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.1950, "lon": 106.8230},
        "mode": "bike"}),
    "walk_pendek_dengan_graf": _ex("Jalan kaki Monas → Jl. Medan Merdeka Utara (±440 m), sertakan node dan ruas", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.17148, "lon": 106.8277989},
        "mode": "walk", "buffer_m": 200, "include_graph": True}),
    "tujuan_di_laut": _ex("Tujuan di laut → 422 point_not_on_network", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.085, "lon": 106.86},
        "mode": "walk"}),
    "tujuan_kepulauan_seribu": _ex("Tujuan Kepulauan Seribu → 422 outside_study_area", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -5.75, "lon": 106.55},
        "mode": "walk"}),
    "asal_kepulauan_seribu": _ex("Asal Kepulauan Seribu → 422 outside_study_area (titik asal)", {
        "origin": {"lat": -5.75, "lon": 106.55},
        "destination": {"lat": -6.1754, "lon": 106.8272},
        "mode": "walk"}),
    "asal_sama_dengan_tujuan": _ex("Asal = tujuan → 200, simpul asal = simpul tujuan", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.1754, "lon": 106.8272},
        "mode": "walk"}),
    "tujuan_terisolasi": _ex("Tujuan di komponen jalan terisolasi → 422 no_route_found", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.1819823, "lon": 106.8198083},
        "mode": "walk"}),
    "buffer_terlalu_kecil": _ex("buffer_m 100 memutus jalur → 422 no_route_found (tanpa buffer_m: terhubung)", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.1760, "lon": 106.8245},
        "mode": "walk", "buffer_m": 100}),
    "luar_dki_dalam_kotak": _ex("Tangerang, di dalam kotak wilayah studi → 422 point_not_on_network", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.20, "lon": 106.69},
        "mode": "walk"}),
    "buffer_melebihi": _ex("buffer_m > 10000 → 400 invalid_request", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.1950, "lon": 106.8230},
        "mode": "walk", "buffer_m": 20000}),
    "mode_tidak_dikenal": _ex("mode \"car\" → 400 invalid_request", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "destination": {"lat": -6.1950, "lon": 106.8230},
        "mode": "car"}),
    "koordinat_tidak_valid": _ex("lat 106 (lon/lat tertukar) → 400 invalid_request", {
        "origin": {"lat": 106.8272, "lon": -6.1754},
        "destination": {"lat": -6.1950, "lon": 106.8230},
        "mode": "walk"}),
    "field_hilang": _ex("Tanpa destination → 400 invalid_request", {
        "origin": {"lat": -6.1754, "lon": 106.8272},
        "mode": "walk"}),
})]


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
    mode: ModeParam,
    include_geometry: bool = True,
):
    """RoadGraph of the active version inside bbox, only edges the mode may use."""
    box = _parse_bbox(bbox, settings)
    graph = await repo.read_road_graph(box, mode, include_geometry=include_geometry)
    return RoadGraphOut.build(graph.graph_version, graph.mode, graph.bbox, graph.nodes, graph.edges, graph.rejected)


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
    mode: Annotated[Mode | None, Query(description="Kosong = semua ruas")] = None,
    highway: HighwayParam = None,
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
async def nearest_node(repo: Repo, settings: Settings, lon: Lon, lat: Lat, mode: ModeParam,
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
async def nearest_edge(repo: Repo, settings: Settings, lon: Lon, lat: Lat, mode: ModeParam,
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
async def get_neighbors(repo: Repo, node_id: NodeId, mode: ModeParam):
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
    q: StreetQuery,
    limit: StreetLimit = 10,
):
    query = q.strip()
    if not _SEARCHABLE_RE.search(query):
        raise InvalidRequest("q harus memuat minimal 3 huruf atau angka berurutan.")
    results = await repo.search_streets(query, limit)
    version = (await repo.get_active_version()).version
    return StreetsOut(graph_version=version, query=query, results=[StreetOut.of(s) for s in results])


@router.post("/subgraph", response_model=SubgraphOut, summary="Request subgraph for an origin-destination pair")
async def build_subgraph(repo: Repo, settings: Settings, body: SubgraphBody):
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
