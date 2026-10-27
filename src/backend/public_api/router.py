"""Public API (TI-SE-08), the two paths the API Gateway routes to the Cache API:

    POST /v1/routes             IRouteQuery: RouteRequest (Kode 3.1) -> GeoJSON (Kode 3.2)
    GET  /v1/exposure-surface   IExposureSurface: bbox, zoom -> GeoJSON of edges or grid cells

Errors use the uniform schema {"error": {"code", "message", "request_id"[, "reason"]}}
with the codes of Tabel 3.16. 429, 502 and 504 come from the API Gateway and are
documented here so the Frontend has one contract.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from osm.router import Repo as OsmRepo
from osm.router import Settings as OsmSettingsDep
from osm.router import _ex
from planner.router import RouteRequestBody
from pollution.router import Repo as PollutionRepo
from solver.geojson import build_geojson_response
from solver.router import Solver, SolverConfig, solve_from_request

from .config import ApiSettings
from .openapi_examples import (
    CELL_SURFACE_EXAMPLE,
    EDGE_SURFACE_EXAMPLE,
    FALLBACK_ROUTE_EXAMPLE,
    ROUTE_EXAMPLE,
    error_example,
)
from .schemas import ErrorResponse, RouteFeatureCollection, SurfaceFeatureCollection
from .surface import (
    CACHE_CONTROL,
    etag_matches,
    get_exposure_surface,
    parse_surface_bbox,
    surface_etag,
    surface_source,
)

router = APIRouter(tags=["public"])

GEOJSON = "application/geo+json"


class GeoJSONResponse(JSONResponse):
    media_type = GEOJSON


def get_api_settings(request: Request) -> ApiSettings:
    return request.app.state.api_settings


ApiConfig = Annotated[ApiSettings, Depends(get_api_settings)]

_REQUEST_ID_HEADER = {"X-Request-ID": {"description": "Penanda request untuk penelusuran log",
                                       "schema": {"type": "string"}}}


def _errors(description: str, *examples: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {"model": ErrorResponse, "description": description, "headers": _REQUEST_ID_HEADER,
            "content": {"application/json": {"examples": {name: {"value": value} for name, value in examples}}}}


_GATEWAY_RESPONSES: dict[int | str, dict[str, Any]] = {
    429: _errors("Dari API Gateway: rate limit terlampaui; header Retry-After menyatakan detik tunggu "
                 "(Tabel 3.15). Belum diaktifkan di gateway.",
                 ("rate_limited", error_example("rate_limited", "Terlalu banyak permintaan. Coba lagi dalam 3 detik."))),
    500: _errors("Galat internal yang tidak terduga",
                 ("internal_error", error_example("internal_error", "Terjadi galat internal pada server."))),
    502: _errors("Dari API Gateway: Cache API tidak dapat dihubungi",
                 ("upstream_unavailable", error_example("upstream_unavailable",
                                                        "Layanan sedang tidak dapat dihubungi. Silakan coba lagi."))),
    504: _errors("Dari API Gateway: Cache API tidak merespons dalam 10 detik",
                 ("upstream_timeout", error_example("upstream_timeout",
                                                    "Layanan tidak merespons tepat waktu. Silakan coba lagi."))),
}

_CACHE_ERRORS = [
    ("cache_empty", error_example("cache_unavailable", "Spatial Pollution Cache DB belum memiliki time window "
                                  "berstatus complete.", "cache_empty")),
    ("database_unavailable", error_example("cache_unavailable", "Basis data cache tidak dapat dihubungi.",
                                           "database_unavailable")),
    ("query_timeout", error_example("cache_unavailable", "Query ke cache melewati batas waktu.", "query_timeout")),
]

ROUTE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": RouteFeatureCollection,
        "description": ("Rute rekomendasi dan pembanding, termasuk saat rute rekomendasi berasal dari fallback "
                        "(route_source baseline, preference_applied false, metadata.fallback_reason terisi)."),
        "headers": {**_REQUEST_ID_HEADER,
                    "Cache-Control": {"description": "no-store: berisi koordinat perjalanan (PNF-06)",
                                      "schema": {"type": "string"}}},
        "content": {GEOJSON: {"examples": {"policy": {"summary": "Rute dari model", "value": ROUTE_EXAMPLE},
                                           "fallback": {"summary": "Fallback karena model lambat",
                                                        "value": FALLBACK_ROUTE_EXAMPLE}}}},
    },
    400: _errors(
        "Skema tidak sesuai, α atau β di luar [0, 1], α + β ≠ 1, moda tidak dikenal, atau JSON rusak",
        ("preferensi", error_example("invalid_request", "preference.alpha + preference.beta harus 1 (sekarang 1.1).")),
        ("moda", error_example("invalid_request", "mode: Input should be 'walk' or 'bike'")),
        ("json_rusak", error_example("invalid_request", "5: JSON decode error")),
    ),
    413: _errors("Body melebihi API_MAX_BODY_BYTES (bawaan 16 KiB)",
                 ("payload_too_large", error_example("payload_too_large", "Body permintaan melebihi batas 16384 byte."))),
    422: _errors(
        "Titik di luar wilayah studi, tidak dekat ruas yang dapat dilalui, atau asal dan tujuan pada simpul yang sama",
        ("outside_study_area", error_example("outside_study_area", "Titik tujuan berada di luar wilayah studi (DKI Jakarta).")),
        ("point_not_on_network", error_example("point_not_on_network", "Titik tujuan: tidak ada simpul yang dapat "
                                               "dilalui moda walk dalam radius 200 m.")),
        ("no_route_found", error_example("no_route_found", "Titik asal dan tujuan ter-snap ke simpul yang sama; "
                                         "tidak ada perjalanan.", "same_node")),
    ),
    503: _errors(
        "Cache tidak dapat dibaca atau kosong, atau Route Model gagal dan fallback tidak tersedia",
        *_CACHE_ERRORS,
        ("route_service_unavailable", error_example("route_service_unavailable", "Layanan penentuan rute tidak "
                                                    "merespons tepat waktu. Silakan coba lagi.", "solver_timeout")),
        ("solver_no_route", error_example("route_service_unavailable", "Layanan penentuan rute tidak dapat "
                                          "menghasilkan rute. Silakan coba lagi.", "solver_no_route")),
    ),
    **_GATEWAY_RESPONSES,
}

SURFACE_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "model": SurfaceFeatureCollection,
        "description": "Ruas (zoom ≥ 14) atau sel grid (zoom < 14) dengan exposure_index, pm25_ugm3, no2_ugm3 "
                       "dan confidence_score. Kosong bila tidak ada ruas di bbox.",
        "headers": {**_REQUEST_ID_HEADER,
                    "Cache-Control": {"description": "public, no-cache: boleh disimpan, tetapi wajib divalidasi "
                                                     "ulang (If-None-Match) sebelum dipakai lagi",
                                      "schema": {"type": "string"}},
                    "ETag": {"description": "ETag lemah atas versi graf, time window (awal, waktu selesai, model), "
                                            "data_stale, dan parameter permintaan",
                             "schema": {"type": "string"}}},
        "content": {GEOJSON: {"examples": {"ruas": {"summary": "zoom 15, per ruas", "value": EDGE_SURFACE_EXAMPLE},
                                           "grid": {"summary": "zoom 12, sel 500 m", "value": CELL_SURFACE_EXAMPLE}}}},
    },
    304: {"description": "If-None-Match cocok dengan ETag: data dan parameter tidak berubah; pakai respons yang "
                         "tersimpan. Time window complete yang lebih baru mengubah ETag",
          "headers": {**_REQUEST_ID_HEADER, "ETag": {"schema": {"type": "string"}}}},
    400: _errors(
        "bbox tidak valid, terlalu luas untuk zoom yang diminta, zoom di luar 0–22, atau pollutant tidak dikenal",
        ("bbox_terlalu_luas", error_example("invalid_request", "bbox terlalu luas untuk zoom 15 (30.1 km²); per ruas "
                                            "(zoom ≥ 14) batasnya 25 km². Perkecil bbox atau gunakan zoom lebih kecil.")),
        ("bbox_format", error_example("invalid_request", "bbox tidak valid; gunakan min_lon,min_lat,max_lon,max_lat "
                                      "(WGS 84) dengan min < max.")),
    ),
    503: _errors("Cache tidak dapat dibaca atau belum memiliki time window complete", *_CACHE_ERRORS),
    **_GATEWAY_RESPONSES,
}


# Response documentation per operation, also used by openapi.restore_response_examples.
DOCUMENTED_RESPONSES = {("/v1/routes", "post"): ROUTE_RESPONSES, ("/v1/exposure-surface", "get"): SURFACE_RESPONSES}


@router.post(
    "/v1/routes",
    response_class=GeoJSONResponse,
    summary="Rute rekomendasi dan pembanding (IRouteQuery, Kode 3.1 → Kode 3.2)",
    responses=ROUTE_RESPONSES,
    operation_id="planRoute",
)
async def plan_route(request: Request, osm: OsmRepo, pollution: PollutionRepo, osm_settings: OsmSettingsDep,
                     solver: Solver, solver_settings: SolverConfig, body: RouteRequestBody):
    """Menentukan rute rekomendasi sesuai preferensi α/β dan rute tercepat sebagai pembanding.

    Waktu tempuh dalam menit, jarak dalam meter, paparan kumulatif dalam indeks·menit dan
    µg·min/m³ (lihat `metadata.units`). Koordinat [lon, lat] WGS 84 (`metadata.crs`).
    Bila Route Model gagal, rute rekomendasi adalah rute tercepat dengan
    `route_source: "baseline"`, `preference_applied: false`, dan `metadata.fallback_reason`."""
    result = await solve_from_request(request, osm, pollution, osm_settings, solver, solver_settings, body)
    return GeoJSONResponse(build_geojson_response(result), headers={"Cache-Control": "no-store"})


SurfaceBBox = Annotated[str, Query(
    description="min_lon,min_lat,max_lon,max_lat (WGS 84). Zoom ≥ 14: maks. 25 km²; zoom < 14: maks. "
                "API_SURFACE_MAX_CELLS sel (bawaan 10 000)",
    openapi_examples={
        "monas_z15": _ex("Sekitar Monas (±1,2 km²), pakai zoom 15", "106.822,-6.180,106.832,-6.170"),
        "jakarta_pusat_z12": _ex("Jakarta Pusat (±60 km²), pakai zoom 12", "106.79,-6.22,106.87,-6.15"),
        "jakarta_z11": _ex("Seluruh DKI Jakarta, pakai zoom 11", "106.68,-6.38,106.98,-6.08"),
        "terlalu_luas_z15": _ex("Seluruh Jakarta dengan zoom 15 → 400 invalid_request", "106.68,-6.38,106.98,-6.08"),
        "format_salah": _ex("Hanya 3 angka → 400 invalid_request", "106.82,-6.18,106.83"),
    },
)]
Zoom = Annotated[int, Query(ge=0, le=22, description="Zoom peta (web mercator). ≥ 14: per ruas; 13: sel 250 m; "
                                                     "12: sel 500 m; ≤ 11: sel 1 km",
                            openapi_examples={"ruas": _ex("15 → per ruas", 15), "grid_500": _ex("12 → sel 500 m", 12),
                                              "grid_1km": _ex("11 → sel 1 km", 11),
                                              "di_luar_rentang": _ex("23 → 400 invalid_request", 23)})]
Layer = Annotated[Literal["combined", "pm25", "no2"], Query(
    description="Lapisan yang ditampilkan Frontend (set_heatmap_layer). Hanya dicatat di metadata.layer: "
                "ketiga nilai selalu dikirim, sehingga ganti lapisan tidak perlu request baru")]


@router.get(
    "/v1/exposure-surface",
    response_class=GeoJSONResponse,
    summary="Permukaan paparan untuk heatmap (IExposureSurface)",
    responses=SURFACE_RESPONSES,
    operation_id="getExposureSurface",
)
async def exposure_surface(request: Request, osm: OsmRepo, pollution: PollutionRepo, settings: ApiConfig,
                           bbox: SurfaceBBox, zoom: Zoom, pollutant: Layer = "combined"):
    """Indeks paparan gabungan, PM2.5, NO2 dan confidence score dari time window complete terakhir.

    Zoom ≥ 14 mengembalikan setiap ruas (LineString); zoom < 14 mengagregasi ruas ke sel grid
    (Polygon) 250 m, 500 m, atau 1 km. Satuan di `metadata.units`, koordinat [lon, lat]
    WGS 84 (`metadata.crs`), batas kelas warna di `metadata.exposure_index_classes`.

    Body tidak memuat request_id, sehingga dapat disimpan cache (ID ada di header
    X-Request-ID). Kirim If-None-Match dengan ETag sebelumnya: 304 bila data dan parameter
    belum berubah."""
    box, grid = parse_surface_bbox(bbox, zoom, settings)
    source = await surface_source(osm, pollution)
    etag = surface_etag(source, box, zoom, pollutant)
    headers = {"Cache-Control": CACHE_CONTROL, "ETag": etag}
    if etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    body = await get_exposure_surface(bbox=box, grid=grid, zoom=zoom, layer=pollutant, source=source,
                                      pollution=pollution)
    return GeoJSONResponse(body, headers=headers)
