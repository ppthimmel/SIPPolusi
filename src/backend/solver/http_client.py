"""Client for the Multi-Objective Route Model service (IRouteSolve over HTTP).

POST {ROUTE_SOLVER_URL}/internal/v1/solve with the solver input as JSON,
gzip-compressed (Tabel 3.14, 3.15, Kode 3.3). Failures are classified:

    timeout                  -> SolverError(solver_timeout)
    connection refused/DNS   -> SolverError(solver_unreachable)
    5xx or unexpected 4xx    -> SolverError(solver_error)
    422 no_route_found       -> SolverReply(status="no_route")
    body breaks the contract -> SolverError(invalid_solver_response)
"""
from __future__ import annotations

import gzip
import json
from typing import Literal

import httpx
from pydantic import BaseModel, ValidationError

from planner.models import SolverInput

from .contract import (
    INVALID_SOLVER_RESPONSE,
    SOLVER_ERROR,
    SOLVER_TIMEOUT,
    SOLVER_UNREACHABLE,
    SolveRequest,
    SolverError,
    SolverReply,
)

SOLVE_PATH = "/internal/v1/solve"


def request_body(request: SolveRequest) -> dict:
    """IRouteSolve request (Kode 3.3) plus the attributes the planner prepared per edge."""
    si: SolverInput = request.solver_input
    return {
        "request_id": si.request_id,
        "mode": si.mode.value,
        "deadline_ms": request.deadline_ms,
        "preference": {"alpha": si.preference.alpha, "beta": si.preference.beta},
        "pollutant": si.pollutant,
        "graph_version": si.source.graph_version,
        "time_window_start": si.weights.time_window_start.isoformat().replace("+00:00", "Z"),
        "origin_node": si.origin.node_id,
        "destination_node": si.destination.node_id,
        "nodes": [{"id": n, "lon": p.lon, "lat": p.lat} for n, p in si.nodes.items()],
        "edges": [{
            "id": e.edge_id, "u": e.u, "v": e.v, "length_m": e.length_m, "bidirectional": e.bidirectional,
            "travel_time_s": e.travel_time_s, "exposure_index": e.exposure_index,
            "concentration_ugm3": e.concentration_ugm3, "confidence_score": e.confidence_score,
            "weight_source": e.weight_source,
        } for e in si.edges],
    }


class _RouteIn(BaseModel):
    edge_ids: list[int]


class _ReplyIn(BaseModel):
    route_source: Literal["policy", "baseline"]
    fallback_reason: str | None = None
    model_version: str | None = None
    preference_applied: bool | None = None
    recommended: _RouteIn
    comparison: _RouteIn


class HttpSolver:
    name = "http"

    def __init__(self, base_url: str, grace_ms: int = 300, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.grace_ms = grace_ms
        self._transport = transport  # tests inject httpx.MockTransport

    async def solve(self, request: SolveRequest) -> SolverReply:
        payload = gzip.compress(json.dumps(request_body(request)).encode())
        timeout = (request.deadline_ms + self.grace_ms) / 1000
        headers = {"Content-Type": "application/json", "Content-Encoding": "gzip",
                   "X-Request-ID": request.request_id}
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport) as client:
                resp = await client.post(SOLVE_PATH, content=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise SolverError(SOLVER_TIMEOUT, "Route Model tidak merespons dalam batas waktu.") from exc
        except httpx.TransportError as exc:
            raise SolverError(SOLVER_UNREACHABLE, f"Route Model tidak dapat dihubungi ({type(exc).__name__}).") from exc

        if resp.status_code == 422 and _error_code(resp) == "no_route_found":
            return SolverReply("no_route", "policy", False, None, None)
        if resp.status_code != 200:
            raise SolverError(SOLVER_ERROR, f"Route Model menjawab HTTP {resp.status_code}.")
        try:
            body = _ReplyIn.model_validate_json(resp.content)
        except ValidationError as exc:
            raise SolverError(INVALID_SOLVER_RESPONSE, "Jawaban Route Model tidak sesuai kontrak IRouteSolve.") from exc
        # Taken as reported, or derived when absent. The orchestrator rejects replies whose
        # labels contradict each other or whose baseline routes are not fastest (check_provenance,
        # check_fastest), so a label from the model is never shown unchecked.
        applied = body.route_source == "policy" if body.preference_applied is None else body.preference_applied
        return SolverReply("ok", body.route_source, applied, body.fallback_reason, body.model_version,
                           tuple(body.recommended.edge_ids), tuple(body.comparison.edge_ids))


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return resp.json().get("error", {}).get("code")
    except (ValueError, AttributeError):
        return None
