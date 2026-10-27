"""Route solving orchestration (Gambar 3.9 R12 to R18, Tabel 3.6 compute_deadline, call_route_solve).

    planner.build_solver_input           R5 to R11 (TI-SE-05)
    compute_deadline                     budget - time spent - response margin
    call_route_solve                     IRouteSolve within deadline_ms + grace
    check routes against the subgraph    edges exist, connect origin -> destination, allowed direction
    fallback (TI-SE-07)                  baseline when the configured solver fails, explicitly marked
    geometry + metrics                   T(R), E(R), distance, confidence, LineString

Route provenance is checked, not trusted (TI-SE-07 QA): a reply labelled policy
must apply the preference, one labelled baseline must not and must say why, and
every route shown as baseline (the comparison, or a baseline recommendation) must be
a fastest route of the subgraph. The Backend computes the fastest travel time itself,
concurrently with the solver call, so the check adds no latency in the usual case.

Errors the user sees are structured and generic (Tabel 3.16); the internal cause
(exception, HTTP status, URL) only goes to the log:
    solver timeout / unreachable / error / invalid reply / no route / no time left
        -> 503 route_service_unavailable with reason (when no fallback is possible)
    origin and destination on the same node
        -> 422 no_route_found, reason same_node
The planner only hands over subgraphs where the destination is reachable, so a
solver answering "no route" is a solver failure (503 solver_no_route), not a trip
without a route.

One log line per request (logger "solver", event route_inference) records the
solver, outcome, route_source, model version, preference, deadline, inference
time and failure reason. Coordinates are never logged (PNF-06).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from osm.config import OsmSettings
from osm.errors import NoRouteFound
from osm.repository import OsmRepository
from planner.assembler import build_solver_input
from planner.models import IMPUTED_MEDIAN, PlannedEdge, SolverInput
from pollution.repository import PollutionRepository

from .config import SolverSettings
from .cancel import run_cancellable
from .contract import (
    DEADLINE_EXCEEDED,
    INVALID_SOLVER_RESPONSE,
    MODEL_FALLBACK_REASONS,
    SOLVER_ERROR,
    SOLVER_NO_ROUTE,
    SOLVER_TIMEOUT,
    FallbackInfo,
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
from .paths import fastest_time_s

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
            SOLVER_TIMEOUT, f"no reply within {request.deadline_ms + grace_ms} ms") from exc
    except SolverError as exc:
        raise RouteServiceUnavailable(exc.reason, exc.message) from exc
    except Exception as exc:  # noqa: BLE001 - any solver crash is a solver error, not a Backend 500
        logger.exception("request_id=%s solver=%s raised", request.request_id, solver.name)
        raise RouteServiceUnavailable(SOLVER_ERROR, f"{type(exc).__name__}: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000
    if not isinstance(reply, SolverReply):
        raise RouteServiceUnavailable(INVALID_SOLVER_RESPONSE, f"reply of type {type(reply).__name__}")
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


@dataclass(slots=True)
class _Attempt:
    solver: str
    reply: SolverReply
    recommended: list[tuple[PlannedEdge, bool]]
    comparison: list[tuple[PlannedEdge, bool]]
    elapsed_ms: float


# Relative tolerance when comparing a route's travel time with the fastest one.
_FASTEST_TOLERANCE = 1e-9


def check_provenance(reply: SolverReply) -> None:
    """route_source, preference_applied and fallback_reason must agree; ValueError otherwise.

    policy:   the preference was applied and there is no fallback reason;
    baseline: the preference was not applied and fallback_reason says why."""
    if reply.route_source == "policy" and (not reply.preference_applied or reply.fallback_reason):
        raise ValueError(f"route_source policy dengan preference_applied={reply.preference_applied}, "
                         f"fallback_reason={reply.fallback_reason!r}")
    if reply.route_source == "baseline" and (reply.preference_applied or not reply.fallback_reason):
        raise ValueError(f"route_source baseline dengan preference_applied={reply.preference_applied}, "
                         f"fallback_reason={reply.fallback_reason!r}")


def check_fastest(label: str, path: list[tuple[PlannedEdge, bool]], fastest_s: float | None) -> None:
    """ValueError unless path is a fastest route (ties allowed)."""
    seconds = sum(e.travel_time_s for e, _ in path)
    if fastest_s is None or seconds > fastest_s * (1 + _FASTEST_TOLERANCE) + 1e-9:
        raise ValueError(f"rute {label} berlabel baseline {seconds:.3f} s, rute tercepat {fastest_s} s")


async def _attempt(solver: RouteSolver, request: SolveRequest, grace_ms: int) -> _Attempt:
    """Call one solver and check its routes; raises RouteServiceUnavailable.

    For any solver other than the in-process baseline (whose routes are the
    fastest by construction), the fastest travel time is computed concurrently
    and the routes labelled baseline are checked against it."""
    si = request.solver_input
    limit_s = (request.deadline_ms + grace_ms) / 1000
    started = time.perf_counter()
    fastest = None if solver.name == "baseline" else asyncio.create_task(run_cancellable(fastest_time_s, si))
    try:
        reply, elapsed_ms = await call_route_solve(solver, request, grace_ms)
        if reply.status == "no_route":
            raise RouteServiceUnavailable(SOLVER_NO_ROUTE, "solver reported no route on a reachable subgraph")
        try:
            check_provenance(reply)
            recommended = trace_route(reply.recommended, si)
            comparison = trace_route(reply.comparison, si)
        except ValueError as exc:
            raise RouteServiceUnavailable(INVALID_SOLVER_RESPONSE, str(exc)) from exc
        if fastest is not None:
            remaining_s = max(limit_s - (time.perf_counter() - started), 0.0)
            try:
                fastest_s = await asyncio.wait_for(fastest, timeout=remaining_s)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise RouteServiceUnavailable(
                    SOLVER_TIMEOUT, f"fastest-route check did not finish within {limit_s * 1000:.0f} ms") from exc
            try:
                check_fastest("pembanding", comparison, fastest_s)
                if reply.route_source == "baseline":
                    check_fastest("rekomendasi", recommended, fastest_s)
            except ValueError as exc:
                raise RouteServiceUnavailable(INVALID_SOLVER_RESPONSE, str(exc)) from exc
    finally:
        if fastest is not None:
            # When the solver failed first: cancel, and wait until the token is set so the
            # search thread stops now (run_cancellable does not wait for the thread itself).
            fastest.cancel()
            await asyncio.gather(fastest, return_exceptions=True)
    return _Attempt(solver.name, reply, recommended, comparison, elapsed_ms)


def _failure_reason(exc: Exception) -> str | None:
    """Solver failure reason that allows a fallback, or None."""
    if isinstance(exc, RouteServiceUnavailable) and exc.reason in MODEL_FALLBACK_REASONS:
        return exc.reason
    return None


def _log(outcome: str, *, si: SolverInput | None, solver: str, deadline_ms: int | None, attempt: _Attempt | None = None,
         reason: str | None = None, detail: str = "", fallback: FallbackInfo | None = None, request_id: str = "") -> None:
    reply = attempt.reply if attempt else None
    fields = {
        "request_id": si.request_id if si else request_id,
        "solver": solver,
        "outcome": outcome,                                  # ok | fallback | failed
        "route_source": reply.route_source if reply else None,
        "model_version": reply.model_version if reply else None,
        "fallback_reason": (MODEL_FALLBACK_REASONS.get(fallback.failure_reason) if fallback
                            else reply.fallback_reason if reply else None),
        "mode": si.mode.value if si else None,
        "alpha": si.preference.alpha if si else None,
        "beta": si.preference.beta if si else None,
        "deadline_ms": deadline_ms,
        "inference_ms": round(attempt.elapsed_ms, 1) if attempt else None,
        "primary_ms": round(fallback.primary_elapsed_ms, 1) if fallback else None,
        "reason": reason or (fallback.failure_reason if fallback else None),
    }
    line = "route_inference " + " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    if detail:
        line += f" detail={detail!r}"
    (logger.warning if outcome != "ok" else logger.info)(line)


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
    fallback_solver: RouteSolver | None = None,
) -> SolveResult:
    si = await build_solver_input(payload, osm=osm, pollution=pollution, settings=osm_settings,
                                  request_id=request_id)
    if si.same_node:
        raise _no_route("Titik asal dan tujuan ter-snap ke simpul yang sama; tidak ada perjalanan.", "same_node")

    deadline_ms = compute_deadline(received_at, clock(), solver_settings.budget_ms, solver_settings.response_margin_ms)
    if deadline_ms <= 0:
        _log("failed", si=si, solver=solver.name, deadline_ms=deadline_ms, reason=DEADLINE_EXCEEDED)
        raise RouteServiceUnavailable(DEADLINE_EXCEEDED, f"deadline {deadline_ms} ms")

    fallback: FallbackInfo | None = None
    failure_detail = ""
    primary_started = time.perf_counter()
    try:
        attempt = await _attempt(solver, SolveRequest(si, deadline_ms), solver_settings.grace_ms)
    except RouteServiceUnavailable as exc:
        reason = _failure_reason(exc)
        detail = getattr(exc, "detail", "") or exc.message
        if reason is None or solver_settings.fallback != "baseline" or solver.name == "baseline":
            _log("failed", si=si, solver=solver.name, deadline_ms=deadline_ms,
                 reason=getattr(exc, "reason", None), detail=detail)
            raise
        # Remaining time, so the whole request stays within the API Gateway timeout.
        spent_ms = (clock() - received_at).total_seconds() * 1000
        budget_ms = int(min(solver_settings.fallback_timeout_ms,
                            solver_settings.gateway_timeout_ms - spent_ms - solver_settings.response_margin_ms))
        if budget_ms <= 0:
            _log("failed", si=si, solver=solver.name, deadline_ms=deadline_ms, reason=reason,
                 detail=f"{detail}; no time left for the fallback")
            raise
        fallback = FallbackInfo(solver.name, reason, (time.perf_counter() - primary_started) * 1000)
        failure_detail = detail
        if fallback_solver is None:
            from .baseline import BaselineSolver
            fallback_solver = BaselineSolver()
        try:
            attempt = await _attempt(fallback_solver, SolveRequest(si, budget_ms), 0)
        except RouteServiceUnavailable as fallback_exc:
            _log("failed", si=si, solver=solver.name, deadline_ms=deadline_ms, reason=reason,
                 detail=f"{detail}; fallback failed: {getattr(fallback_exc, 'detail', '') or fallback_exc.message}")
            raise exc from fallback_exc

    reply = attempt.reply
    route_source = "baseline" if fallback else reply.route_source
    preference_applied = False if fallback else reply.preference_applied
    fallback_reason = MODEL_FALLBACK_REASONS[fallback.failure_reason] if fallback else reply.fallback_reason

    ids = sorted({e.edge_id for e, _ in attempt.recommended} | {e.edge_id for e, _ in attempt.comparison})
    geometries = await osm.read_edge_geometries(ids, si.source.graph_version)
    rec_metrics, cmp_metrics = route_metrics(attempt.recommended), route_metrics(attempt.comparison)

    notes = []
    if fallback:
        notes.append("Layanan model rute tidak dapat dipakai untuk permintaan ini; rute rekomendasi adalah "
                     "rute tercepat (baseline) dan preferensi waktu-paparan tidak diterapkan.")
    elif not preference_applied:
        notes.append("Preferensi waktu-paparan tidak diterapkan; rute rekomendasi berasal dari baseline.")
    if si.weights.data_stale:
        notes.append("Estimasi polusi berasal dari time window yang lebih lama dari batas kebaruan.")
    if rec_metrics.imputed_edge_count:
        notes.append(f"{rec_metrics.imputed_edge_count} ruas pada rute rekomendasi tanpa estimasi; diisi median wilayah.")

    _log("fallback" if fallback else "ok", si=si, solver=solver.name, deadline_ms=deadline_ms,
         attempt=attempt, fallback=fallback, detail=failure_detail)
    return SolveResult(
        solver=attempt.solver,
        route_source=route_source,
        preference_applied=preference_applied,
        fallback_reason=fallback_reason,
        model_version=reply.model_version,
        recommended=RouteResult("recommended", route_source, preference_applied, tuple(reply.recommended),
                                route_line(attempt.recommended, geometries, si), rec_metrics),
        # Labelled baseline: checked to be a fastest route in _attempt (or the baseline solver's own).
        comparison=RouteResult("comparison", "baseline", False, tuple(reply.comparison),
                               route_line(attempt.comparison, geometries, si), cmp_metrics),
        delta_travel_time_min=round(rec_metrics.travel_time_min - cmp_metrics.travel_time_min, 3),
        delta_exposure_pct=(round((rec_metrics.exposure_index_min - cmp_metrics.exposure_index_min)
                                  / cmp_metrics.exposure_index_min * 100, 2)
                            if cmp_metrics.exposure_index_min > 0 else None),
        deadline_ms=deadline_ms,
        solver_elapsed_ms=round(attempt.elapsed_ms, 1),
        context=SolveContext(
            request_id=si.request_id, mode=si.mode, preference=si.preference, pollutant=si.pollutant,
            origin_node=si.origin.node_id, destination_node=si.destination.node_id,
            origin_snap_distance_m=round(si.origin.snap_distance_m, 2),
            destination_snap_distance_m=round(si.destination.snap_distance_m, 2),
            graph_version=si.source.graph_version, time_window_start=si.weights.time_window_start,
            data_stale=si.weights.data_stale, missing_edge_count=si.weights.missing_edge_count,
            pollution_model_version=si.source.pollution_model_version,
            estimation_sources=dict(si.source.estimation_sources),
            downscaling_fallback=si.source.downscaling_fallback,
        ),
        notes=notes,
        fallback=fallback,
    )
