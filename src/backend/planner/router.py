"""POST /v1/routes/solver-input: the solver input built for a RouteRequest.

Inspection endpoint for TI-SE-05. The public POST /v1/routes (public_api, TI-SE-08)
runs the same planning and then the solver (TI-SE-06).
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Query, Request

from osm.errors import get_request_id
from osm.router import Repo as OsmRepo
from osm.router import Settings, _ex
from pollution.router import Repo as PollutionRepo

from .assembler import build_solver_input
from .models import SolverInput
from .schemas import (
    PreferenceOut,
    RouteContextOut,
    RouteRequestIn,
    SnappedPointOut,
    SolverEdgeOut,
    SolverInputOut,
    SolverNodeOut,
    SourceOut,
    WeightStatusOut,
)

router = APIRouter(prefix="/v1/routes", tags=["routes"])

_MONAS = {"lat": -6.1754, "lon": 106.8272}
_BUNDARAN_HI = {"lat": -6.1950, "lon": 106.8230}
_BALANCED = {"alpha": 0.5, "beta": 0.5}

RouteRequestBody = Annotated[RouteRequestIn, Body(openapi_examples={
    "walk": _ex("Jalan kaki Monas → Bundaran HI, preferensi seimbang", {
        "origin": _MONAS, "destination": _BUNDARAN_HI, "mode": "walk", "pollutant": "pm25",
        "preference": _BALANCED}),
    "bike_no2": _ex("Sepeda, polutan acuan NO₂, utamakan paparan", {
        "origin": _MONAS, "destination": _BUNDARAN_HI, "mode": "bike", "pollutant": "no2",
        "preference": {"alpha": 0.2, "beta": 0.8}}),
    "preferensi_salah": _ex("α + β ≠ 1 → 400 invalid_request", {
        "origin": _MONAS, "destination": _BUNDARAN_HI, "mode": "walk", "preference": {"alpha": 0.6, "beta": 0.5}}),
    "moda_salah": _ex("mode \"car\" → 400 invalid_request", {
        "origin": _MONAS, "destination": _BUNDARAN_HI, "mode": "car", "preference": _BALANCED}),
    "luar_wilayah": _ex("Tujuan Kepulauan Seribu → 422 outside_study_area", {
        "origin": _MONAS, "destination": {"lat": -5.75, "lon": 106.55}, "mode": "walk", "preference": _BALANCED}),
    "tidak_dapat_snap": _ex("Pantai Ancol → laut → 422 point_not_on_network (titik tujuan)", {
        "origin": {"lat": -6.1225, "lon": 106.8420}, "destination": {"lat": -6.1000, "lon": 106.8420},
        "mode": "walk", "preference": _BALANCED}),
})]


def to_response(si: SolverInput, summary: bool) -> SolverInputOut:
    return SolverInputOut(
        request_id=si.request_id,
        context=RouteContextOut(
            mode=si.mode, speed_mps=si.speed_mps,
            preference=PreferenceOut(alpha=si.preference.alpha, beta=si.preference.beta),
            pollutant=si.pollutant, bbox=si.bbox.as_list(),
            origin=SnappedPointOut(lat=si.origin.point.lat, lon=si.origin.point.lon, node_id=si.origin.node_id,
                                   snap_distance_m=round(si.origin.snap_distance_m, 2)),
            destination=SnappedPointOut(lat=si.destination.point.lat, lon=si.destination.point.lon,
                                        node_id=si.destination.node_id,
                                        snap_distance_m=round(si.destination.snap_distance_m, 2)),
            same_node=si.same_node,
        ),
        weights=WeightStatusOut(**{f: getattr(si.weights, f) for f in WeightStatusOut.model_fields}),
        source=SourceOut(**{f: getattr(si.source, f) for f in SourceOut.model_fields}),
        origin_node=si.origin.node_id,
        destination_node=si.destination.node_id,
        node_count=len(si.nodes),
        edge_count=len(si.edges),
        nodes=None if summary else [SolverNodeOut(id=n, lon=p.lon, lat=p.lat) for n, p in si.nodes.items()],
        edges=None if summary else [
            SolverEdgeOut(id=e.edge_id, **{f: getattr(e, f) for f in SolverEdgeOut.model_fields if f != "id"})
            for e in si.edges
        ],
    )


@router.post("/solver-input", response_model=SolverInputOut, summary="RouteRequest → solver input (TI-SE-05)")
async def solver_input(
    request: Request,
    osm: OsmRepo,
    pollution: PollutionRepo,
    settings: Settings,
    body: RouteRequestBody,
    summary: Annotated[bool, Query(description="true = tanpa daftar nodes dan edges")] = False,
):
    """Validate the RouteRequest, read the road graph and the latest complete pollution
    window in parallel, snap both points to feasible nodes, assemble the connected
    subgraph and attach travel time and exposure to every edge."""
    si = await build_solver_input(body, osm=osm, pollution=pollution, settings=settings,
                                  request_id=get_request_id(request))
    return to_response(si, summary)
