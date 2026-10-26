"""Route endpoints through the configured solver (ROUTE_SOLVER_PROVIDER: mock, baseline, http).

    POST /v1/routes/solve   RouteRequest -> SolveResult (TI-SE-06, internal shape)
    POST /v1/routes/plan    RouteRequest -> GeoJSON FeatureCollection (TI-SE-07, Kode 3.2)

POST /v1/routes (TI-SE-08) will serve the /plan response under its final path.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from osm.errors import get_request_id
from osm.router import Repo as OsmRepo
from osm.router import Settings as OsmSettingsDep
from planner.router import RouteRequestBody
from pollution.router import Repo as PollutionRepo

from .config import SolverSettings
from .contract import RouteSolver
from .deadline import RECEIVED_AT_HEADER, parse_received_at
from .geojson import build_geojson_response
from .orchestrator import solve_route_request
from .schemas import SolveResultOut

router = APIRouter(prefix="/v1/routes", tags=["routes"])


def get_solver(request: Request) -> RouteSolver:
    return request.app.state.route_solver


def get_solver_settings(request: Request) -> SolverSettings:
    return request.app.state.solver_settings


Solver = Annotated[RouteSolver, Depends(get_solver)]
SolverConfig = Annotated[SolverSettings, Depends(get_solver_settings)]


@router.post("/solve", response_model=SolveResultOut, summary="RouteRequest → SolveResult (TI-SE-06)")
async def solve(request: Request, osm: OsmRepo, pollution: PollutionRepo, osm_settings: OsmSettingsDep,
                solver: Solver, solver_settings: SolverConfig, body: RouteRequestBody):
    """Plan the subgraph (TI-SE-05), call the configured solver within the latency budget,
    check both routes against the subgraph and return geometry, metrics and route source.

    503 route_service_unavailable (reason solver_timeout, solver_unreachable, solver_error,
    invalid_solver_response, deadline_exceeded) and 422 no_route_found (reason same_node,
    solver_no_route) are structured errors."""
    result = await _solve(request, osm, pollution, osm_settings, solver, solver_settings, body)
    return SolveResultOut.of(result)


GEOJSON = "application/geo+json"


@router.post(
    "/plan",
    response_class=JSONResponse,
    summary="RouteRequest → GeoJSON FeatureCollection (TI-SE-07, Kode 3.2)",
    responses={200: {"content": {GEOJSON: {}},
                     "description": "Dua Feature (recommended, comparison) dan metadata dengan route_source"}},
)
async def plan(request: Request, osm: OsmRepo, pollution: PollutionRepo, osm_settings: OsmSettingsDep,
               solver: Solver, solver_settings: SolverConfig, body: RouteRequestBody):
    """Same planning and solving as /solve, answered as GeoJSON. When the configured solver
    fails and ROUTE_SOLVER_FALLBACK=baseline, the fastest route is returned with
    route_source "baseline", preference_applied false and metadata.fallback_reason
    "model_timeout", "model_unavailable", "model_error", "model_invalid_response" or
    "model_no_route"."""
    result = await _solve(request, osm, pollution, osm_settings, solver, solver_settings, body)
    return JSONResponse(build_geojson_response(result), media_type=GEOJSON)


async def _solve(request, osm, pollution, osm_settings, solver, solver_settings, body):
    received_at = parse_received_at(request.headers.get(RECEIVED_AT_HEADER)) or datetime.now(timezone.utc)
    return await solve_route_request(
        body, osm=osm, pollution=pollution, osm_settings=osm_settings, solver=solver,
        solver_settings=solver_settings, request_id=get_request_id(request), received_at=received_at)
