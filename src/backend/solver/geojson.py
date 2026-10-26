"""build_geojson_response (Tabel 3.6, R18): SolveResult -> GeoJSON FeatureCollection (Kode 3.2).

Two Features (recommended, comparison) with LineString geometry in [lon, lat]
(RFC 7946) and route properties; request and data metadata go in the
"metadata" foreign member, as in Kode 3.2. Each Feature says where it came
from (route_source, preference_applied), and metadata.fallback_reason plus
notes say why when the recommendation is a fallback, so a baseline route is
never presented as a preference-aware one (PF-12, PNF-07).
"""
from __future__ import annotations

from typing import Any

from .contract import RouteResult, SolveResult

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
            "solver": result.solver,
            "comparison": {
                "delta_travel_time_min": result.delta_travel_time_min,
                "delta_exposure_pct": result.delta_exposure_pct,
            },
            "notes": list(result.notes),
            "disclaimer": DISCLAIMER,
        },
    }
