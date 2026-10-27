"""API response models for /v1/pollution. Times are UTC ISO 8601; concentrations µg/m³."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator

from osm.models import Mode

from .models import EdgeWeight, PollutionRead, TimeWindow, WindowInfo

RFC3339_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


def _utc_hour_string(value: str) -> str:
    return TimeWindow.parse(value).start.isoformat().replace("+00:00", "Z")


HourlyTimestamp = Annotated[
    str, Field(pattern=RFC3339_PATTERN, json_schema_extra={"format": "date-time"}),
    AfterValidator(_utc_hour_string),
]


class TimeWindowOut(BaseModel):
    """The start/end envelope used by cache API responses; one UTC hour."""

    time_window_start: datetime
    time_window_end: datetime

    @field_validator("time_window_start", "time_window_end", mode="before")
    @classmethod
    def parse_time(cls, value: object) -> datetime:
        if isinstance(value, str):
            if not re.fullmatch(RFC3339_PATTERN, value):
                raise ValueError("TimeWindow timestamps must use RFC 3339 with a timezone")
            return TimeWindow.parse(value).start
        if not isinstance(value, datetime):
            raise ValueError("TimeWindow timestamps must be ISO 8601 strings")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> TimeWindowOut:
        start = TimeWindow(self.time_window_start).start
        end = TimeWindow(self.time_window_end).start
        if end - start != timedelta(hours=1):
            raise ValueError("TimeWindow wajib berdurasi satu jam")
        self.time_window_start, self.time_window_end = start, end
        return self


class WindowOut(TimeWindowOut):
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
    pm25_ugm3: float | None = Field(ge=0, allow_inf_nan=False)
    no2_ugm3: float | None = Field(ge=0, allow_inf_nan=False)
    exposure_index: float | None = Field(ge=0, allow_inf_nan=False)
    confidence_score: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    background_source: Literal["aod", "reanalysis"] | None
    estimation_source: Literal["stgnn", "idw", "hybrid"] | None
    model_metadata: dict = Field(default_factory=dict)
    trace_run_id: str | None = None
    graph_version: str | None
    model_version: str | None

    @classmethod
    def of(cls, w: EdgeWeight) -> EdgeWeightOut:
        return cls(**{name: getattr(w, name) for name in cls.model_fields})


class EdgeWeightsOut(TimeWindowOut):
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
