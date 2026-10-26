"""IRouteSolve: the contract between the Backend and any route solver.

A solver receives the solver input built by the planner (TI-SE-05) and a
deadline, and replies with two routes as ordered edge_ids from the snapped
origin node to the snapped destination node:

- recommended: the preference-conditioned route (policy), or the baseline when
  the policy fell back;
- comparison: the fastest route (baseline, PF-09).

The Backend then checks the routes against the subgraph and computes geometry
and metrics itself, so every provider (mock, in-process baseline, HTTP service)
is evaluated the same way (route_model README: "do not independently resnap").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

from osm.errors import OsmApiError
from osm.models import Mode
from planner.models import Preference, SolverInput

RouteSource = Literal["policy", "baseline"]

# SolverError / RouteServiceUnavailable reasons.
SOLVER_TIMEOUT = "solver_timeout"            # no reply within deadline_ms + grace
SOLVER_UNREACHABLE = "solver_unreachable"    # cannot connect to the solver
SOLVER_ERROR = "solver_error"                # the solver raised or answered 5xx/4xx
INVALID_SOLVER_RESPONSE = "invalid_solver_response"  # reply breaks the contract
DEADLINE_EXCEEDED = "deadline_exceeded"      # no time left before calling the solver


class SolverError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class RouteServiceUnavailable(OsmApiError):
    """503 route_service_unavailable (Tabel 3.16) with a reason."""

    status_code = 503
    code = "route_service_unavailable"

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SolveRequest:
    solver_input: SolverInput
    deadline_ms: int

    @property
    def request_id(self) -> str:
        return self.solver_input.request_id


@dataclass(frozen=True, slots=True)
class SolverReply:
    """What a solver returns (IRouteSolve, Kode 3.3), before Backend checks."""

    status: Literal["ok", "no_route"]
    route_source: RouteSource
    preference_applied: bool
    fallback_reason: str | None
    model_version: str | None
    recommended: tuple[int, ...] = ()
    comparison: tuple[int, ...] = ()


class RouteSolver(Protocol):
    """IRouteSolve. Implementations: MockSolver, BaselineSolver, HttpSolver."""

    name: str

    async def solve(self, request: SolveRequest) -> SolverReply:
        """Raise SolverError for failures; return status "no_route" when no route exists."""
        ...


@dataclass(frozen=True, slots=True)
class RouteMetrics:
    """compute_route_metrics (Tabel 3.7): T(R), E(R), distance, confidence."""

    travel_time_min: float
    distance_m: float
    # E(R) = sum(I_e,t * tau_e) in index-minutes (Subbab 3.3.1).
    exposure_index_min: float
    # sum(c_e * tau_e) for the requested pollutant in µg·min/m³; None unless every
    # edge has a concentration (route_model convention: no partial totals).
    exposure_ug_min_m3: float | None
    concentration_coverage: float
    # Mean edge confidence weighted by edge exposure I_e,t * tau_e (Tabel 3.7).
    confidence_score: float
    edge_count: int
    imputed_edge_count: int


@dataclass(frozen=True, slots=True)
class RouteResult:
    role: Literal["recommended", "comparison"]
    route_source: RouteSource
    preference_applied: bool
    edge_ids: tuple[int, ...]
    coordinates: list[list[float]]  # LineString [lon, lat] from origin node to destination node
    metrics: RouteMetrics


@dataclass(frozen=True, slots=True)
class SolveContext:
    """Request and data context carried to the response (downstream metadata)."""

    request_id: str
    mode: Mode
    preference: Preference
    pollutant: str
    origin_node: int
    destination_node: int
    origin_snap_distance_m: float
    destination_snap_distance_m: float
    graph_version: str
    time_window_start: datetime
    data_stale: bool
    missing_edge_count: int
    pollution_model_version: str | None


@dataclass(frozen=True, slots=True)
class SolveResult:
    """SolveResult (Tabel 3.3): both routes, route source and metadata."""

    solver: str
    route_source: RouteSource
    preference_applied: bool
    fallback_reason: str | None
    model_version: str | None
    recommended: RouteResult
    comparison: RouteResult
    delta_travel_time_min: float
    delta_exposure_pct: float | None
    deadline_ms: int
    solver_elapsed_ms: float
    context: SolveContext
    notes: list[str] = field(default_factory=list)
