"""API response model of POST /v1/routes/solve (SolveResult, Tabel 3.3)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from osm.models import Mode
from planner.schemas import PreferenceOut

from .contract import RouteResult, SolveResult


class RouteMetricsOut(BaseModel):
    travel_time_min: float = Field(description="T(R)")
    distance_m: float
    exposure_index_min: float = Field(description="E(R) = Σ I_e,t · τ_e, indeks·menit")
    exposure_ug_min_m3: float | None = Field(description="Σ konsentrasi polutan acuan · τ_e; null bila ada ruas tanpa konsentrasi")
    concentration_coverage: float
    confidence_score: float = Field(description="Rata-rata confidence ruas berbobot paparan")
    edge_count: int
    imputed_edge_count: int


class GeometryOut(BaseModel):
    type: Literal["LineString"] = "LineString"
    coordinates: list[list[float]]


class RouteOut(BaseModel):
    role: Literal["recommended", "comparison"]
    route_source: Literal["policy", "baseline"]
    preference_applied: bool
    edge_ids: list[int]
    geometry: GeometryOut
    metrics: RouteMetricsOut

    @classmethod
    def of(cls, r: RouteResult) -> RouteOut:
        return cls(role=r.role, route_source=r.route_source, preference_applied=r.preference_applied,
                   edge_ids=list(r.edge_ids), geometry=GeometryOut(coordinates=r.coordinates),
                   metrics=RouteMetricsOut(**{f: getattr(r.metrics, f) for f in RouteMetricsOut.model_fields}))


class ComparisonOut(BaseModel):
    delta_travel_time_min: float = Field(description="T(rekomendasi) − T(pembanding)")
    delta_exposure_pct: float | None = Field(description="Perubahan E(R) rekomendasi terhadap pembanding, %")


class SolveContextOut(BaseModel):
    request_id: str
    mode: Mode
    preference: PreferenceOut
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


class SolveResultOut(BaseModel):
    solver: str = Field(description="Penyedia solver: mock, baseline, atau http")
    route_source: Literal["policy", "baseline"]
    preference_applied: bool
    fallback_reason: str | None
    model_version: str | None
    recommended: RouteOut
    comparison: RouteOut
    comparison_summary: ComparisonOut
    deadline_ms: int
    solver_elapsed_ms: float
    context: SolveContextOut
    notes: list[str]

    @classmethod
    def of(cls, s: SolveResult) -> SolveResultOut:
        c = s.context
        return cls(
            solver=s.solver, route_source=s.route_source, preference_applied=s.preference_applied,
            fallback_reason=s.fallback_reason, model_version=s.model_version,
            recommended=RouteOut.of(s.recommended), comparison=RouteOut.of(s.comparison),
            comparison_summary=ComparisonOut(delta_travel_time_min=s.delta_travel_time_min,
                                             delta_exposure_pct=s.delta_exposure_pct),
            deadline_ms=s.deadline_ms, solver_elapsed_ms=s.solver_elapsed_ms,
            context=SolveContextOut(
                request_id=c.request_id, mode=c.mode,
                preference=PreferenceOut(alpha=c.preference.alpha, beta=c.preference.beta),
                pollutant=c.pollutant, origin_node=c.origin_node, destination_node=c.destination_node,
                origin_snap_distance_m=c.origin_snap_distance_m,
                destination_snap_distance_m=c.destination_snap_distance_m,
                graph_version=c.graph_version, time_window_start=c.time_window_start,
                data_stale=c.data_stale, missing_edge_count=c.missing_edge_count,
                pollution_model_version=c.pollution_model_version),
            notes=s.notes,
        )
