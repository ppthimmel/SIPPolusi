"""Compact examples shown in the OpenAPI document (/docs). Full responses produced on
the local test database are in public_api/examples/. test_openapi.py validates both
against the response schemas, so an example cannot drift from the contract."""
from __future__ import annotations

import copy
from typing import Any

from solver.geojson import COORDINATE_ORDER, CRS84, DISCLAIMER, EXPOSURE_INDEX_DEFINITION, ROUTE_UNITS

from .surface import CELL_UNITS, EDGE_UNITS, EXPOSURE_INDEX_CLASSES, GRID_AGGREGATION

_REQUEST_ID = "7f3c2a9e-51d4-4b8e-9a0f-2c1e5d6b8a13"


def error_example(code: str, message: str, reason: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "request_id": _REQUEST_ID}
    if reason is not None:
        error["reason"] = reason
    return {"error": error}


def _route(role: str, source: str, applied: bool, coords, metrics: dict, edge_ids) -> dict[str, Any]:
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"role": role, "route_source": source, "preference_applied": applied, **metrics,
                           "edge_ids": edge_ids}}


_REC = [[106.8272, -6.1754], [106.8269, -6.1801], [106.8251, -6.1873], [106.823, -6.195]]
_CMP = [[106.8272, -6.1754], [106.8262, -6.1830], [106.823, -6.195]]

ROUTE_EXAMPLE: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        _route("recommended", "policy", True, _REC,
               {"travel_time_min": 28.4, "distance_m": 2215.0, "exposure_index_min": 51.36,
                "exposure_ug_min_m3": 1392.6, "concentration_coverage": 1.0, "confidence_score": 0.71,
                "imputed_edge_count": 0}, [87108, 87112, 90215]),
        _route("comparison", "baseline", False, _CMP,
               {"travel_time_min": 24.1, "distance_m": 1930.0, "exposure_index_min": 75.42,
                "exposure_ug_min_m3": 2046.3, "concentration_coverage": 1.0, "confidence_score": 0.68,
                "imputed_edge_count": 0}, [87108, 88990]),
    ],
    "metadata": {
        "request_id": _REQUEST_ID, "mode": "walk", "pollutant": "pm25", "preference": {"alpha": 0.5, "beta": 0.5},
        "route_source": "policy", "preference_applied": True, "fallback_reason": None,
        "time_window_start": "2026-10-26T05:00:00Z", "data_stale": False, "missing_edge_count": 3,
        "graph_version": "jakarta-20261005",
        "model_version": {"downscaling": "idw-p2-k8", "route_policy": "4"},
        "solver": "http", "comparison": {"delta_travel_time_min": 4.3, "delta_exposure_pct": -31.9},
        "notes": [], "crs": CRS84, "coordinate_order": COORDINATE_ORDER, "units": ROUTE_UNITS,
        "exposure_index_definition": EXPOSURE_INDEX_DEFINITION, "disclaimer": DISCLAIMER,
    },
}

# Fallback: the recommended route is the fastest route, marked as baseline.
FALLBACK_ROUTE_EXAMPLE: dict[str, Any] = copy.deepcopy(ROUTE_EXAMPLE)
FALLBACK_ROUTE_EXAMPLE["features"][0] = copy.deepcopy(ROUTE_EXAMPLE["features"][1])
FALLBACK_ROUTE_EXAMPLE["features"][0]["properties"]["role"] = "recommended"
FALLBACK_ROUTE_EXAMPLE["metadata"].update({
    "route_source": "baseline", "preference_applied": False, "fallback_reason": "model_timeout",
    "model_version": {"downscaling": "idw-p2-k8", "route_policy": "astar-baseline"}, "solver": "baseline",
    "comparison": {"delta_travel_time_min": 0.0, "delta_exposure_pct": 0.0},
    "notes": ["Layanan model rute tidak dapat dipakai untuk permintaan ini; rute rekomendasi adalah rute tercepat "
              "(baseline) dan preferensi waktu-paparan tidak diterapkan."],
})

_SURFACE_META = {
    "layer": "combined", "time_window_start": "2026-10-26T05:00:00Z",
    "time_window_end": "2026-10-26T06:00:00Z", "data_stale": False, "graph_version": "jakarta-20261005",
    "model_version": {"downscaling": "idw-p2-k8"}, "crs": CRS84, "coordinate_order": COORDINATE_ORDER,
    "exposure_index_definition": EXPOSURE_INDEX_DEFINITION, "exposure_index_classes": EXPOSURE_INDEX_CLASSES,
    "disclaimer": DISCLAIMER,
}

EDGE_SURFACE_EXAMPLE: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "geometry": {"type": "LineString", "coordinates": [[106.8268, -6.1745], [106.8271, -6.1762]]},
         "properties": {"edge_id": 87108, "exposure_index": 1.8467, "pm25_ugm3": 31.2, "no2_ugm3": 45.1,
                        "confidence_score": 0.74, "estimation_source": "idw"}},
    ],
    "metadata": {**_SURFACE_META, "kind": "edge", "zoom": 15, "bbox": [106.822, -6.18, 106.832, -6.17],
                 "cell_size_m": None, "aggregation": None, "feature_count": 1, "units": EDGE_UNITS},
}

CELL_SURFACE_EXAMPLE: dict[str, Any] = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[106.823596, -6.177513], [106.828113, -6.177513],
                                                          [106.828113, -6.172992], [106.823596, -6.172992],
                                                          [106.823596, -6.177513]]]},
         "properties": {"cell_id": "500m:23636:-1366", "edge_count": 41, "exposure_index": 1.6213,
                        "exposure_index_max": 2.4102, "pm25_ugm3": 27.9, "no2_ugm3": 41.3, "confidence_score": 0.7}},
    ],
    "metadata": {**_SURFACE_META, "kind": "grid", "zoom": 12, "bbox": [106.79, -6.22, 106.87, -6.15],
                 "cell_size_m": 500, "aggregation": GRID_AGGREGATION, "feature_count": 1, "units": CELL_UNITS},
}
