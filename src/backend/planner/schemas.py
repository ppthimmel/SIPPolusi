"""API models: RouteRequest in (Kode 3.1) and solver input out (IRouteSolve, Kode 3.3)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from osm.models import Mode
from osm.schemas import PointModel


class PreferenceIn(BaseModel):
    alpha: float = Field(ge=0, le=1, description="Bobot waktu tempuh α, 0–1")
    beta: float = Field(ge=0, le=1, description="Bobot paparan β, 0–1; α + β = 1")


class RouteRequestIn(BaseModel):
    """RouteRequest (Tabel 3.3, Kode 3.1)."""

    origin: PointModel
    destination: PointModel
    mode: Mode
    pollutant: Literal["pm25", "no2"] = Field("pm25", description="Polutan untuk concentration_ugm3 per ruas")
    preference: PreferenceIn


class SnappedPointOut(BaseModel):
    lat: float
    lon: float
    node_id: int
    snap_distance_m: float


class PreferenceOut(BaseModel):
    alpha: float
    beta: float


class RouteContextOut(BaseModel):
    mode: Mode
    speed_mps: float
    preference: PreferenceOut
    pollutant: str
    bbox: list[float]
    origin: SnappedPointOut
    destination: SnappedPointOut
    same_node: bool


class WeightStatusOut(BaseModel):
    time_window_start: datetime
    time_window_end: datetime
    data_stale: bool
    age_minutes: float
    edge_count: int
    estimated_edge_count: int
    missing_edge_count: int = Field(description="Ruas tanpa estimasi; exposure_index diisi median, confidence 0")
    imputed_exposure_index: float | None = Field(description="Median exposure_index bbox yang dipakai mengisi ruas tanpa estimasi")
    concentration_missing_count: int = Field(description="Ruas tanpa concentration_ugm3 untuk polutan yang diminta (tidak diisi)")
    excluded_rows: dict[str, int]


class SourceOut(BaseModel):
    graph_version: str
    osm_timestamp: datetime | None
    pollution_model_version: str | None
    pollution_coverage_ratio: float | None
    estimation_sources: dict[str, int]
    background_sources: dict[str, int]
    rejected_graph_rows: dict[str, int]


class SolverNodeOut(BaseModel):
    id: int
    lon: float
    lat: float


class SolverEdgeOut(BaseModel):
    id: int
    u: int
    v: int
    length_m: float
    highway: str | None
    oneway: bool
    bidirectional: bool = Field(description="Moda ini juga boleh v → u")
    travel_time_s: float
    exposure_index: float
    confidence_score: float
    weight_source: Literal["estimate", "imputed_median"]
    concentration_ugm3: float | None
    pm25_ugm3: float | None
    no2_ugm3: float | None
    estimation_source: str | None


class SolverInputOut(BaseModel):
    request_id: str
    context: RouteContextOut
    weights: WeightStatusOut
    source: SourceOut
    origin_node: int
    destination_node: int
    node_count: int
    edge_count: int
    nodes: list[SolverNodeOut] | None = Field(description="null bila summary=true")
    edges: list[SolverEdgeOut] | None = Field(description="null bila summary=true")
