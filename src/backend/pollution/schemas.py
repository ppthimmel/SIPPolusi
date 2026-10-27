"""API response models for /v1/pollution. Times are UTC ISO 8601; concentrations µg/m³."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from osm.models import Mode

from .models import EdgeWeight, PollutionRead, WindowInfo


class WindowOut(BaseModel):
    time_window_start: datetime
    time_window_end: datetime
    status: str = Field(description="writing atau complete; hanya complete yang dibaca")
    pollutant: str | None
    coverage_ratio: float | None
    model_version: str | None
    completed_at: datetime | None

    @classmethod
    def of(cls, w: WindowInfo) -> WindowOut:
        return cls(time_window_start=w.time_window_start, time_window_end=w.time_window.end, status=w.status,
                   pollutant=w.pollutant, coverage_ratio=w.coverage_ratio, model_version=w.model_version,
                   completed_at=w.completed_at)


class WindowsOut(BaseModel):
    latest_complete: WindowOut | None
    data_stale: bool | None = Field(description="null bila belum ada time window complete")
    age_minutes: float | None = Field(description="Menit sejak akhir time window complete terakhir")
    windows: list[WindowOut]


class EdgeWeightOut(BaseModel):
    edge_id: int
    pm25_ugm3: float | None
    no2_ugm3: float | None
    exposure_index: float | None
    confidence_score: float | None
    background_source: str | None
    estimation_source: str | None
    model_metadata: dict = Field(default_factory=dict)
    trace_run_id: str | None = None
    graph_version: str | None
    model_version: str | None

    @classmethod
    def of(cls, w: EdgeWeight) -> EdgeWeightOut:
        return cls(**{name: getattr(w, name) for name in cls.model_fields})


class EdgeWeightsOut(BaseModel):
    time_window_start: datetime
    time_window_end: datetime
    model_version: str | None
    coverage_ratio: float | None
    graph_version: str | None = Field(description="Versi graf yang dipakai menyaring baris; null = tanpa filter")
    mode: Mode | None
    bbox: list[float]
    data_stale: bool
    age_minutes: float
    count: int
    excluded: dict[str, int] = Field(description="Baris di dalam bbox yang tidak dikembalikan, per alasan")
    weights: list[EdgeWeightOut]

    @classmethod
    def of(cls, read: PollutionRead, mode: Mode | None, bbox: list[float]) -> EdgeWeightsOut:
        w = read.window
        return cls(
            time_window_start=w.time_window_start, time_window_end=w.time_window.end,
            model_version=w.model_version, coverage_ratio=w.coverage_ratio, graph_version=read.graph_version,
            mode=mode, bbox=bbox, data_stale=read.data_stale, age_minutes=round(read.age_s / 60, 1),
            count=len(read.weights), excluded=read.excluded, weights=[EdgeWeightOut.of(x) for x in read.weights],
        )
