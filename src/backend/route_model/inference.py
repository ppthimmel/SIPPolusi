import logging

from .astar_osm import solve_baseline

logger = logging.getLogger(__name__)


def solve_route(origin: dict, destination: dict, mode: str, alpha: float, beta: float) -> dict:
    """Multi-objective reinforcement learning policy inference (PF-07, PF-08).

    Until the policy is available, return an explicitly labelled baseline
    fallback. Alpha/beta do not influence this fastest-time baseline.
    """
    return solve_baseline_route(origin, destination, mode, fallback_reason="policy_unavailable")


def solve_baseline_route(origin: dict, destination: dict, mode: str, **kwargs) -> dict:
    """Single-objective travel-time baseline via Dijkstra/A* (PF-09).

    Also used as the fallback route when policy inference fails or exceeds
    the latency budget, in which case preference_applied is False.
    """
    return solve_baseline(origin, destination, mode, **kwargs)
