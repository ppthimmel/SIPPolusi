"""Route solving orchestration (Gambar 3.9 R12 to R18, Tabel 3.6 compute_deadline, call_route_solve).

    planner.build_solver_input           R5 to R11 (TI-SE-05)
    compute_deadline                     budget - time spent - response margin
    call_route_solve                     IRouteSolve within deadline_ms + grace
    check routes against the subgraph    edges exist, connect origin -> destination, allowed direction
    geometry + metrics                   T(R), E(R), distance, confidence, LineString

Every failure leaves as a structured error (Tabel 3.16):
    solver timeout / unreachable / error / invalid reply / no time left
        -> 503 route_service_unavailable with reason
    no route (solver reply, or origin and destination on the same node)
        -> 422 no_route_found with reason
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from osm.config import OsmSettings
from osm.errors import NoRouteFound
from osm.repository import OsmRepository
from planner.assembler import build_solver_input
from planner.models import IMPUTED_MEDIAN, PlannedEdge, SolverInput
from pollution.repository import PollutionRepository

from .config import SolverSettings
from .contract import (
    DEADLINE_EXCEEDED,
    INVALID_SOLVER_RESPONSE,
    SOLVER_ERROR,
    SOLVER_TIMEOUT,
    RouteMetrics,
    RouteResult,
    RouteServiceUnavailable,
    RouteSolver,
    SolveContext,
    SolveRequest,
    SolveResult,
    SolverError,
    SolverReply,
)
from .deadline import compute_deadline

logger = logging.getLogger("solver")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _no_route(message: str, reason: str) -> NoRouteFound:
    exc = NoRouteFound(message)
    exc.reason = reason  # error_response adds it to the error body
    return exc


async def call_route_solve(solver: RouteSolver, request: SolveRequest, grace_ms: int = 300) -> tuple[SolverReply, float]:
    """IRouteSolve client (Tabel 3.6). Returns (reply, elapsed ms) or raises RouteServiceUnavailable."""
    started = time.perf_counter()
    try:
        reply = await asyncio.wait_for(solver.solve(request), timeout=(request.deadline_ms + grace_ms) / 1000)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        raise RouteServiceUnavailable(
            f"Route Model tidak merespons dalam {request.deadline_ms + grace_ms} ms.", SOLVER_TIMEOUT) from exc
    except SolverError as exc:
        raise RouteServiceUnavailable(exc.message, exc.reason) from exc
    except Exception as exc:  # noqa: BLE001 - any solver crash is a solver error, not a Backend 500
        logger.exception("request_id=%s solver %s failed", request.request_id, solver.name)
        raise RouteServiceUnavailable(f"Route Model gagal ({type(exc).__name__}).", SOLVER_ERROR) from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not isinstance(reply, SolverReply):
        raise RouteServiceUnavailable("Jawaban solver tidak sesuai kontrak IRouteSolve.", INVALID_SOLVER_RESPONSE)
    return reply, elapsed_ms


def trace_route(edge_ids: tuple[int, ...], si: SolverInput) -> list[tuple[PlannedEdge, bool]]:
    """(edge, forward) along the route; ValueError when it is not a valid path of the subgraph."""
    if not edge_ids:
        raise ValueError("rute kosong")
    by_id = {e.edge_id: e for e in si.edges}
    current = si.origin.node_id
    path = []
    for edge_id in edge_ids:
        edge = by_id.get(edge_id)
        if edge is None:
            raise ValueError(f"ruas {edge_id} bukan bagian dari subgraf")
        if edge.u == current:
            path.append((edge, True))
            current = edge.v
        elif edge.v == current and edge.bidirectional:
            path.append((edge, False))
            current = edge.u
        else:
            raise ValueError(f"ruas {edge_id} tidak bersambung dari simpul {current} atau arahnya tidak diizinkan")
    if current != si.destination.node_id:
        raise ValueError(f"rute berakhir di simpul {current}, bukan di simpul tujuan {si.destination.node_id}")
    return path


def route_metrics(path: list[tuple[PlannedEdge, bool]]) -> RouteMetrics:
    """compute_route_metrics (Tabel 3.7)."""
    edges = [e for e, _ in path]
    minutes = [e.travel_time_s / 60 for e in edges]
    exposure = [e.exposure_index * m for e, m in zip(edges, minutes)]
    known = [(e.concentration_ugm3, m) for e, m in zip(edges, minutes) if e.concentration_ugm3 is not None]
    total_exposure = sum(exposure)
    if total_exposure > 0:
        confidence = sum(w * e.confidence_score for w, e in zip(exposure, edges)) / total_exposure
    else:
        confidence = sum(e.confidence_score for e in edges) / len(edges)
    return RouteMetrics(
        travel_time_min=round(sum(minutes), 3),
        distance_m=round(sum(e.length_m for e in edges), 2),
        exposure_index_min=round(total_exposure, 4),
        exposure_ug_min_m3=round(sum(c * m for c, m in known), 3) if len(known) == len(edges) else None,
        concentration_coverage=round(len(known) / len(edges), 4),
        confidence_score=round(confidence, 4),
        edge_count=len(edges),
        imputed_edge_count=sum(1 for e in edges if e.weight_source == IMPUTED_MEDIAN),
    )


def route_line(path: list[tuple[PlannedEdge, bool]], geometries: Mapping[int, list[list[float]]],
               si: SolverInput) -> list[list[float]]:
    """One LineString from origin node to destination node, each edge drawn in travel direction."""
    line: list[list[float]] = []
    for edge, forward in path:
        coords = geometries.get(edge.edge_id)
        if not coords:  # should not happen; fall back to the straight segment between the nodes
            a, b = si.nodes[edge.u], si.nodes[edge.v]
            coords = [[a.lon, a.lat], [b.lon, b.lat]]
        coords = coords if forward else coords[::-1]
        line.extend(coords[1:] if line and line[-1] == coords[0] else coords)
    return line


async def solve_route_request(
    payload: Any,
    *,
    osm: OsmRepository,
    pollution: PollutionRepository,
    osm_settings: OsmSettings,
    solver: RouteSolver,
    solver_settings: SolverSettings,
    request_id: str,
    received_at: datetime,
    clock: Callable[[], datetime] = _utcnow,
) -> SolveResult:
    si = await build_solver_input(payload, osm=osm, pollution=pollution, settings=osm_settings,
                                  request_id=request_id)
    if si.same_node:
        raise _no_route("Titik asal dan tujuan ter-snap ke simpul yang sama; tidak ada perjalanan.", "same_node")

    deadline_ms = compute_deadline(received_at, clock(), solver_settings.budget_ms, solver_settings.response_margin_ms)
    if deadline_ms <= 0:
        raise RouteServiceUnavailable("Batas latensi habis sebelum Route Model dipanggil.", DEADLINE_EXCEEDED)

    reply, elapsed_ms = await call_route_solve(solver, SolveRequest(si, deadline_ms), solver_settings.grace_ms)
    if reply.status == "no_route":
        raise _no_route("Route Model tidak menemukan rute antara simpul asal dan simpul tujuan.", "solver_no_route")

    try:
        recommended = trace_route(reply.recommended, si)
        comparison = trace_route(reply.comparison, si)
    except ValueError as exc:
        raise RouteServiceUnavailable(f"Rute dari solver tidak valid: {exc}.", INVALID_SOLVER_RESPONSE) from exc

    ids = sorted({e.edge_id for e, _ in recommended} | {e.edge_id for e, _ in comparison})
    geometries = await osm.read_edge_geometries(ids, si.source.graph_version)

    rec_metrics, cmp_metrics = route_metrics(recommended), route_metrics(comparison)
    notes = []
    if not reply.preference_applied:
        notes.append("Preferensi waktu-paparan tidak diterapkan; rute rekomendasi berasal dari baseline.")
    if si.weights.data_stale:
        notes.append("Estimasi polusi berasal dari time window yang lebih lama dari batas kebaruan.")
    if rec_metrics.imputed_edge_count:
        notes.append(f"{rec_metrics.imputed_edge_count} ruas pada rute rekomendasi tanpa estimasi; diisi median wilayah.")

    return SolveResult(
        solver=solver.name,
        route_source=reply.route_source,
        preference_applied=reply.preference_applied,
        fallback_reason=reply.fallback_reason,
        model_version=reply.model_version,
        recommended=RouteResult("recommended", reply.route_source, reply.preference_applied,
                                tuple(reply.recommended), route_line(recommended, geometries, si), rec_metrics),
        comparison=RouteResult("comparison", "baseline", False,
                               tuple(reply.comparison), route_line(comparison, geometries, si), cmp_metrics),
        delta_travel_time_min=round(rec_metrics.travel_time_min - cmp_metrics.travel_time_min, 3),
        delta_exposure_pct=(round((rec_metrics.exposure_index_min - cmp_metrics.exposure_index_min)
                                  / cmp_metrics.exposure_index_min * 100, 2)
                            if cmp_metrics.exposure_index_min > 0 else None),
        deadline_ms=deadline_ms,
        solver_elapsed_ms=round(elapsed_ms, 1),
        context=SolveContext(
            request_id=si.request_id, mode=si.mode, preference=si.preference, pollutant=si.pollutant,
            origin_node=si.origin.node_id, destination_node=si.destination.node_id,
            origin_snap_distance_m=round(si.origin.snap_distance_m, 2),
            destination_snap_distance_m=round(si.destination.snap_distance_m, 2),
            graph_version=si.source.graph_version, time_window_start=si.weights.time_window_start,
            data_stale=si.weights.data_stale, missing_edge_count=si.weights.missing_edge_count,
            pollution_model_version=si.source.pollution_model_version,
        ),
        notes=notes,
    )
