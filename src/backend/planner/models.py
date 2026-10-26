"""Domain types of the request planner (Tabel 3.3: RouteRequest, ModeProfile, Subgraph)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from osm.models import BBox, Mode, ModeProfile, Point

POLLUTANTS = ("pm25", "no2")

# Where an edge's exposure_index comes from.
ESTIMATE = "estimate"              # pollution.edge_pollution row of the time window
IMPUTED_MEDIAN = "imputed_median"  # no usable estimate: median of the bbox, confidence 0


@dataclass(frozen=True, slots=True)
class Preference:
    alpha: float  # weight of travel time
    beta: float   # weight of exposure


@dataclass(frozen=True, slots=True)
class ValidatedRouteRequest:
    origin: Point
    destination: Point
    profile: ModeProfile
    preference: Preference
    pollutant: str

    @property
    def mode(self) -> Mode:
        return self.profile.mode


@dataclass(frozen=True, slots=True)
class PlannedEdge:
    """One edge of the solver input with travel time and exposure attributes.

    exposure_index is never a silent zero: it is the estimate of the time window
    (weight_source "estimate") or the bbox median (weight_source "imputed_median",
    confidence_score 0). concentration_ugm3 is the requested pollutant and stays
    None when unknown; it is never imputed.
    """

    edge_id: int
    u: int
    v: int
    length_m: float
    highway: str | None
    oneway: bool
    bidirectional: bool  # the mode may also travel v -> u
    travel_time_s: float
    exposure_index: float
    confidence_score: float
    weight_source: str
    concentration_ugm3: float | None
    pm25_ugm3: float | None
    no2_ugm3: float | None
    estimation_source: str | None


@dataclass(frozen=True, slots=True)
class WeightStatus:
    """How complete and how fresh the pollution weights of the subgraph are."""

    time_window_start: datetime
    time_window_end: datetime
    data_stale: bool
    age_minutes: float
    edge_count: int
    estimated_edge_count: int
    missing_edge_count: int
    imputed_exposure_index: float | None
    concentration_missing_count: int
    excluded_rows: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    graph_version: str
    osm_timestamp: datetime | None
    pollution_model_version: str | None
    pollution_coverage_ratio: float | None
    estimation_sources: dict[str, int]
    background_sources: dict[str, int]
    rejected_graph_rows: dict[str, int]


@dataclass(frozen=True, slots=True)
class SnappedPoint:
    point: Point
    node_id: int
    snap_distance_m: float


@dataclass(slots=True)
class SolverInput:
    """Everything the Multi-Objective Route Model needs for one request (IRouteSolve)."""

    request_id: str
    mode: Mode
    speed_mps: float
    preference: Preference
    pollutant: str
    bbox: BBox
    origin: SnappedPoint
    destination: SnappedPoint
    weights: WeightStatus
    source: SourceMetadata
    nodes: dict[int, Point]
    edges: list[PlannedEdge]

    @property
    def same_node(self) -> bool:
        return self.origin.node_id == self.destination.node_id
