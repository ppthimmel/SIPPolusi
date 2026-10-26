"""REST endpoints over the Spatial Pollution Cache DB, mounted at /v1/pollution. Read-only."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from osm.errors import InvalidRequest
from osm.models import Mode
# Shared bbox parameter and limits, so both caches accept the same bbox format.
from osm.router import BBoxParam, Settings, _ex, _parse_bbox

from .models import TimeWindow
from .repository import ACTIVE, PollutionRepository
from .schemas import EdgeWeightsOut, WindowOut, WindowsOut

router = APIRouter(prefix="/v1/pollution", tags=["pollution"])


def get_pollution_repository(request: Request) -> PollutionRepository:
    return request.app.state.pollution_repository


Repo = Annotated[PollutionRepository, Depends(get_pollution_repository)]

TimeWindowParam = Annotated[str, Query(
    description="`latest` (time window complete terakhir) atau awal time window UTC ISO 8601 tepat di awal jam",
    openapi_examples={
        "latest": _ex("Time window complete terakhir", "latest"),
        "tertentu": _ex("Time window tertentu", "2026-10-13T05:00:00Z"),
        "bukan_awal_jam": _ex("Bukan awal jam → 400 invalid_request", "2026-10-13T05:30:00Z"),
        "tanpa_zona": _ex("Tanpa zona waktu → 400 invalid_request", "2026-10-13T05:00:00"),
        "bukan_tanggal": _ex("Bukan tanggal → 400 invalid_request", "kemarin"),
    },
)]
GraphVersionParam = Annotated[str, Query(
    description="`active` = hanya baris untuk graf aktif; `all` = tanpa filter; atau nama versi graf",
    openapi_examples={"active": _ex("Graf aktif", ACTIVE), "all": _ex("Semua versi", "all")},
)]
ModeFilter = Annotated[Mode | None, Query(description="Kosong = semua ruas; walk/bike = hanya ruas yang boleh dilalui")]


def _parse_window(text: str) -> TimeWindow | str:
    if text.strip().lower() == "latest":
        return "latest"
    try:
        return TimeWindow.parse(text)
    except ValueError as exc:
        raise InvalidRequest(str(exc)) from exc


@router.get("/windows", response_model=WindowsOut, summary="Time windows in the cache, newest first")
async def list_windows(repo: Repo, limit: Annotated[int, Query(ge=1, le=168)] = 24):
    windows = await repo.list_windows(limit)
    latest = next((w for w in windows if w.status == "complete"), None)
    if latest is None and windows:
        latest = await repo.latest_complete_window()  # older than the listed page
    stale, age = repo.staleness(latest) if latest else (None, None)
    return WindowsOut(
        latest_complete=WindowOut.of(latest) if latest else None,
        data_stale=stale, age_minutes=round(age / 60, 1) if age is not None else None,
        windows=[WindowOut.of(w) for w in windows],
    )


@router.get("/edge-weights", response_model=EdgeWeightsOut, summary="read_pollution_weight(bbox, time_window)")
async def read_edge_weights(
    repo: Repo,
    settings: Settings,
    bbox: BBoxParam,
    time_window_start: TimeWindowParam = "latest",
    mode: ModeFilter = None,
    graph_version: GraphVersionParam = ACTIVE,
):
    """EdgeWeight of one complete time window inside bbox. 503 cache_unavailable with
    reason cache_empty when no complete window exists; data_stale = true when the
    window ended more than POLLUTION_STALE_AFTER_S ago."""
    box = _parse_bbox(bbox, settings)
    window = _parse_window(time_window_start)
    version = None if graph_version.strip().lower() == "all" else graph_version.strip()
    read = await repo.read_pollution_weight(box, window, mode=mode, graph_version=version)
    return EdgeWeightsOut.of(read, mode, box.as_list())
