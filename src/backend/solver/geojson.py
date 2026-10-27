"""build_geojson_response (Tabel 3.6, R18): SolveResult -> GeoJSON FeatureCollection (Kode 3.2).

Two Features (recommended, comparison) with LineString geometry in [lon, lat]
(RFC 7946) and route properties; request and data metadata go in the
"metadata" foreign member, as in Kode 3.2. Each Feature says where it came
from (route_source, preference_applied), and metadata.fallback_reason plus
notes say why when the recommendation is a fallback, so a baseline route is
never presented as a preference-aware one (PF-12, PNF-07).

Coordinates are WGS 84 longitude, latitude (RFC 7946 section 4). RFC 7946 removed
the top-level "crs" member, so the reference system and the unit of every metric
are stated in metadata ("crs", "coordinate_order", "units") instead (TI-SE-08).
"""
from __future__ import annotations

from typing import Any

from .contract import RouteResult, SolveResult

# RFC 7946 section 4: GeoJSON coordinates are always this CRS, longitude first.
CRS84 = "urn:ogc:def:crs:OGC::CRS84"
COORDINATE_ORDER = ["lon", "lat"]
# Subbab 3.3.1: WHO 2021 24-hour guidelines, 15 µg/m³ PM2.5 and 25 µg/m³ NO2.
EXPOSURE_INDEX_DEFINITION = (
    "I = ½ · (PM2.5 / 15 + NO2 / 25), konsentrasi dalam µg/m³ dibagi pedoman WHO 2021 (rata-rata 24 jam); "
    "bila hanya satu polutan tersedia, I = PM2.5 / 15 atau NO2 / 25. Tanpa satuan; 1,0 = tepat pada pedoman.")

# Unit of each numeric field in route Features and metadata.comparison.
ROUTE_UNITS = {
    "travel_time_min": "min",
    "distance_m": "m",
    "exposure_index_min": "index·min",
    "exposure_ug_min_m3": "µg·min/m³",
    "concentration_coverage": "fraction 0–1",
    "confidence_score": "fraction 0–1",
    "imputed_edge_count": "count",
    "delta_travel_time_min": "min",
    "delta_exposure_pct": "%",
}

DISCLAIMER = (
    "Estimasi konsentrasi polutan merupakan keluaran model, bukan hasil pengukuran langsung, dan dapat "
    "berbeda dari kondisi udara sebenarnya. Gunakan rute ini sebagai alat bantu keputusan."
)


def _feature(route: RouteResult) -> dict[str, Any]:
    m = route.metrics
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": route.coordinates},
        "properties": {
            "role": route.role,
            "route_source": route.route_source,
            "preference_applied": route.preference_applied,
            "travel_time_min": m.travel_time_min,
            "distance_m": m.distance_m,
            "exposure_index_min": m.exposure_index_min,
            "exposure_ug_min_m3": m.exposure_ug_min_m3,
            "concentration_coverage": m.concentration_coverage,
            "confidence_score": m.confidence_score,
            "imputed_edge_count": m.imputed_edge_count,
            "edge_ids": list(route.edge_ids),
        },
    }


def estimation_summary(estimation_sources: dict[str, int], fallback: tuple[str, ...]) -> dict[str, Any]:
    """Where the route's pollution estimates came from (TI-SE-10, D-07).

    source: the subgraph's single estimation_source (idw = interpolation of ground
    sensors only; stgnn or hybrid = the downscaling model took part), "mixed" when
    the edges disagree, null when the cache did not record it."""
    known = set(estimation_sources) - {"unknown"}
    if len(estimation_sources) == 1 and known:
        source = known.pop()
    else:
        source = "mixed" if len(estimation_sources) > 1 else None
    return {"source": source, "fallback_pollutants": list(fallback)}


def build_geojson_response(result: SolveResult) -> dict[str, Any]:
    c = result.context
    return {
        "type": "FeatureCollection",
        "features": [_feature(result.recommended), _feature(result.comparison)],
        "metadata": {
            "request_id": c.request_id,
            "mode": c.mode.value,
            "pollutant": c.pollutant,
            "preference": {"alpha": c.preference.alpha, "beta": c.preference.beta},
            "route_source": result.route_source,
            "preference_applied": result.preference_applied,
            "fallback_reason": result.fallback_reason,
            "time_window_start": c.time_window_start.isoformat().replace("+00:00", "Z"),
            "data_stale": c.data_stale,
            "missing_edge_count": c.missing_edge_count,
            "graph_version": c.graph_version,
            "model_version": {"downscaling": c.pollution_model_version, "route_policy": result.model_version},
            "estimation": estimation_summary(c.estimation_sources, c.downscaling_fallback),
            "solver": result.solver,
            "comparison": {
                "delta_travel_time_min": result.delta_travel_time_min,
                "delta_exposure_pct": result.delta_exposure_pct,
            },
            "notes": list(result.notes),
            "crs": CRS84,
            "coordinate_order": COORDINATE_ORDER,
            "units": ROUTE_UNITS,
            "exposure_index_definition": EXPOSURE_INDEX_DEFINITION,
            "disclaimer": DISCLAIMER,
        },
    }
