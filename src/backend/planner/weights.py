"""attach_edge_weights (Tabel 3.6 Resolver Bobot Ruas dan Confidence Score, R11, UT-BE-07).

Each subgraph edge gets the exposure index I_e,t and confidence score of its
EdgeWeight. An edge without a usable estimate (no row, or exposure_index NULL)
gets the median exposure index of the bbox with confidence 0 and is counted in
missing_edge_count; it is never a silent zero. When the bbox has no usable
estimate at all there is no median to use, and CacheUnavailable(cache_empty) is
raised instead of inventing one.
"""
from __future__ import annotations

from collections import Counter
from statistics import median

from osm.errors import CacheUnavailable
from osm.models import Mode, ModeProfile, RoadEdge, Subgraph
from pollution.models import EdgeWeight, PollutionRead

from .models import ESTIMATE, IMPUTED_MEDIAN, PlannedEdge, WeightStatus


def _concentration(weight: EdgeWeight | None, pollutant: str) -> float | None:
    if weight is None:
        return None
    return weight.pm25_ugm3 if pollutant == "pm25" else weight.no2_ugm3


def _travel_time_s(edge: RoadEdge, profile: ModeProfile) -> float:
    # profile.speed_mps comes from the configurable OSM mode speed settings.
    # Keep defaults and assumptions aligned with route_model/astar_osm.py and
    # docs/route-time-exposure.md when changing the time calculation.
    return edge.length_m / profile.speed_mps  # length_m > 0, validated by assemble_road_graph


def attach_edge_weights(
    subgraph: Subgraph,
    read: PollutionRead,
    profile: ModeProfile,
    pollutant: str = "pm25",
) -> tuple[list[PlannedEdge], WeightStatus]:
    """Pair every subgraph edge with its EdgeWeight by edge_id (UT-BE-07)."""
    by_edge = read.weights_by_edge_id()
    usable = [w.exposure_index for w in read.weights if w.exposure_index is not None]
    if not usable:
        raise CacheUnavailable(
            f"Tidak ada estimasi polusi yang dapat dipakai di wilayah permintaan "
            f"(time window {read.window.time_window_start:%Y-%m-%dT%H:%M:%SZ}, "
            f"0 dari {len(subgraph.edges)} ruas subgraf).",
            CacheUnavailable.CACHE_EMPTY,
        )
    fill = float(median(usable))

    edges: list[PlannedEdge] = []
    missing = concentration_missing = 0
    for e in subgraph.edges:
        weight = by_edge.get(e.edge_id)
        estimated = weight is not None and weight.exposure_index is not None
        if not estimated:
            missing += 1
        concentration = _concentration(weight, pollutant)
        if concentration is None:
            concentration_missing += 1
        edges.append(PlannedEdge(
            edge_id=e.edge_id, u=e.u, v=e.v, length_m=e.length_m, highway=e.highway, oneway=e.oneway,
            bidirectional=profile.mode is Mode.WALK or not e.oneway,
            travel_time_s=round(_travel_time_s(e, profile), 3),
            exposure_index=weight.exposure_index if estimated else fill,
            confidence_score=(weight.confidence_score or 0.0) if estimated else 0.0,
            weight_source=ESTIMATE if estimated else IMPUTED_MEDIAN,
            concentration_ugm3=concentration,
            pm25_ugm3=weight.pm25_ugm3 if weight else None,
            no2_ugm3=weight.no2_ugm3 if weight else None,
            estimation_source=weight.estimation_source if weight else None,
        ))

    status = WeightStatus(
        time_window_start=read.window.time_window_start,
        time_window_end=read.window.time_window.end,
        data_stale=read.data_stale,
        age_minutes=round(read.age_s / 60, 1),
        edge_count=len(edges),
        estimated_edge_count=len(edges) - missing,
        missing_edge_count=missing,
        imputed_exposure_index=round(fill, 4) if missing else None,
        concentration_missing_count=concentration_missing,
        excluded_rows=dict(read.excluded),
    )
    return edges, status


def count_sources(edges: list[PlannedEdge], weights: dict[int, EdgeWeight]) -> tuple[dict[str, int], dict[str, int]]:
    """Counts of estimation_source and background_source over the subgraph's estimates."""
    estimation: Counter[str] = Counter()
    background: Counter[str] = Counter()
    for e in edges:
        w = weights.get(e.edge_id)
        if e.weight_source == ESTIMATE and w is not None:
            estimation[w.estimation_source or "unknown"] += 1
            background[w.background_source or "none"] += 1
    return dict(estimation), dict(background)


def downscaling_fallbacks(edges: list[PlannedEdge], model_metadata: dict) -> tuple[str, ...]:
    """Pollutants whose estimates came from the fallback instead of the registered
    downscaling model (model_metadata[pollutant].fallback_reason, TI-AI-05).

    model_metadata is the window's (PollutionRepository.window_model_metadata); it
    applies only when the subgraph has estimates at all."""
    if not any(e.weight_source == ESTIMATE for e in edges):
        return ()
    return tuple(sorted(p for p, m in model_metadata.items() if isinstance(m, dict) and m.get("fallback_reason")))
