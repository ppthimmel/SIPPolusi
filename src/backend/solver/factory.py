"""Choose the solver provider from configuration (ROUTE_SOLVER_PROVIDER), not from the endpoint."""
from __future__ import annotations

from .config import SolverSettings
from .contract import RouteSolver


def make_solver(settings: SolverSettings) -> RouteSolver:
    if settings.provider == "mock":
        from .mock import MockSolver
        return MockSolver(settings.fault)
    if settings.provider == "baseline":
        from .baseline import BaselineSolver
        return BaselineSolver()
    if settings.provider == "weighted":
        from .weighted import WeightedSolver
        return WeightedSolver()
    from .http_client import HttpSolver
    return HttpSolver(settings.url, settings.grace_ms)
