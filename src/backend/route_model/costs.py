"""Raw route time, exposure and normalized weighted cost calculations.

Time is in seconds and exposure in index-seconds (or concentration-seconds
when concentrations are supplied). Reference values must use the same units.
"""
from __future__ import annotations

import math
from collections.abc import Iterable


def _nonnegative(value: float, name: str) -> float:
    """Return a finite nonnegative number, rejecting missing or invalid data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and nonnegative")
    return float(value)


def travel_time(length_m: float, speed_mps: float) -> float:
    """Return edge travel time in seconds from length and speed."""
    length = _nonnegative(length_m, "length_m")
    speed = _nonnegative(speed_mps, "speed_mps")
    if speed == 0:
        raise ValueError("speed_mps must be positive")
    return length / speed


def edge_exposure(time_s: float, exposure_index: float) -> float:
    """Return an edge's exposure in index-seconds."""
    return _nonnegative(time_s, "time_s") * _nonnegative(exposure_index, "exposure_index")


def route_totals(edges: Iterable[tuple[float, float]]) -> tuple[float, float]:
    """Return total seconds and index-seconds for pairs of edge time and index."""
    times, exposures = [], []
    for time_s, index in edges:
        times.append(_nonnegative(time_s, "time_s"))
        exposures.append(edge_exposure(time_s, index))
    return math.fsum(times), math.fsum(exposures)


def normalize(value: float, reference: float) -> float:
    """Normalize a nonnegative value, using 1 as denominator for zero reference.

    The zero-reference policy preserves the existing mock's behavior. It avoids
    division by zero but does not make a zero-reference objective relative to a
    positive baseline; callers should retain and report the original reference.
    """
    value = _nonnegative(value, "value")
    reference = _nonnegative(reference, "reference")
    return value / (reference if reference else 1.0)


def weighted_cost(time_s: float, exposure: float, time_ref: float, exposure_ref: float,
                  alpha: float, beta: float) -> float:
    """Return the dimensionless weighted time and exposure objective."""
    alpha = _nonnegative(alpha, "alpha")
    beta = _nonnegative(beta, "beta")
    if alpha > 1 or beta > 1 or abs(alpha + beta - 1) > 1e-6:
        raise ValueError("alpha and beta must be in [0, 1] and sum to 1")
    return alpha * normalize(time_s, time_ref) + beta * normalize(exposure, exposure_ref)
