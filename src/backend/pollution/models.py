"""Domain types for the Spatial Pollution Cache DB (Tabel 3.3 and 3.17)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
WINDOW_LENGTH = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class TimeWindow:
    """One-hour window identified by its start, in UTC, on the hour (Tabel 3.3)."""

    start: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None:
            raise ValueError("time_window_start wajib memiliki zona waktu, mis. 2026-10-13T05:00:00Z")
        utc = self.start.astimezone(UTC)
        if (utc.minute, utc.second, utc.microsecond) != (0, 0, 0):
            raise ValueError("time_window_start wajib tepat di awal jam, mis. 2026-10-13T05:00:00Z")
        object.__setattr__(self, "start", utc)

    @property
    def end(self) -> datetime:
        return self.start + WINDOW_LENGTH

    @classmethod
    def parse(cls, text: str) -> TimeWindow:
        try:
            value = datetime.fromisoformat(text.strip().replace("Z", "+00:00").replace("z", "+00:00"))
        except ValueError as exc:
            raise ValueError("time_window_start harus ISO 8601, mis. 2026-10-13T05:00:00Z") from exc
        return cls(value)


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A row of pollution.pollution_window."""

    time_window_start: datetime
    status: str
    pollutant: str | None
    coverage_ratio: float | None
    model_version: str | None
    completed_at: datetime | None

    @property
    def time_window(self) -> TimeWindow:
        return TimeWindow(self.time_window_start)


@dataclass(frozen=True, slots=True)
class EdgeWeight:
    """Estimated concentrations for one edge in one time window.

    pm25_ugm3 and no2_ugm3 may be None when that pollutant is unavailable, which is
    different from zero (PF-06). graph_version and model_version trace the row back
    to the road graph and the model that produced it.
    """

    edge_id: int
    time_window_start: datetime
    pm25_ugm3: float | None
    no2_ugm3: float | None
    exposure_index: float | None
    confidence_score: float | None
    background_source: str | None
    estimation_source: str | None
    graph_version: str | None
    model_version: str | None
    written_at: datetime | None = None
    model_metadata: dict = field(default_factory=dict)
    trace_run_id: str | None = None


@dataclass(slots=True)
class PollutionRead:
    """Result of read_pollution_weight for one bbox and one time window."""

    window: WindowInfo
    graph_version: str | None
    weights: list[EdgeWeight]
    data_stale: bool
    age_s: float
    # Rows inside the bbox left out, per reason: other_graph_version,
    # not_allowed_for_mode, edge_not_in_graph.
    excluded: dict[str, int] = field(default_factory=dict)

    def weights_by_edge_id(self) -> dict[int, EdgeWeight]:
        return {w.edge_id: w for w in self.weights}
