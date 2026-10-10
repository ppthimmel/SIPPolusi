"""Evaluate an already selected route, independently of the search algorithm."""
from dataclasses import dataclass
import math
from typing import Mapping


@dataclass(frozen=True)
class ExposureSnapshot:
    """One pollutant at one time window; missing edges are never zero-filled."""
    graph_version: str
    time_window: str
    pollutant: str
    concentration_ug_m3: Mapping[int, float]


def evaluate_route(edges, graph_version, exposure=None):
    """Sum time, length and exposure for baseline or policy route edges.

    Each edge supplies edge_id, length_m and time_s. This function neither
    searches for a route nor changes its edge order or membership.
    """
    edges = list(edges)
    if not edges:
        raise ValueError("Route evaluation requires at least one edge")
    if exposure is not None:
        if (not isinstance(exposure, ExposureSnapshot) or exposure.graph_version != graph_version
                or not exposure.time_window or not exposure.pollutant
                or not isinstance(exposure.concentration_ug_m3, Mapping)
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v < 0 for v in exposure.concentration_ug_m3.values())):
            raise ValueError("Exposure requires matching graph, window, pollutant and finite nonnegative values")
    seconds, length, cumulative_exposure, covered = 0.0, 0.0, 0.0, 0
    for edge in edges:
        if not all(math.isfinite(v) and v > 0 for v in (edge.length_m, edge.time_s)):
            raise ValueError("Edge length and time must be positive and finite")
        seconds += edge.time_s
        length += edge.length_m
        value = exposure.concentration_ug_m3.get(edge.edge_id) if exposure is not None else None
        if value is not None:
            covered += 1
            cumulative_exposure += value * edge.time_s
    return {
        "travel_time_s": seconds,
        "length_m": length,
        "exposure_ug_s_m3": cumulative_exposure if covered == len(edges) else None,
        "exposure_status": "complete" if covered == len(edges) else "partial" if covered else "unavailable",
        "exposure_edge_coverage": covered / len(edges),
        "pollutant": exposure.pollutant if exposure else None,
        "time_window": exposure.time_window if exposure else None,
    }
