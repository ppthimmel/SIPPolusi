"""build_solver_input: RouteRequest -> SolverInput (Gambar 3.9, R5 to R11).

Order follows the design: validate and resolve the mode before touching any
cache (R5); read the road graph and the pollution weights in parallel (R6 to R9);
assemble the subgraph with feasible snapping (R10); attach the weights (R11).
Both reads use the same active graph_version, so weights estimated on another
graph are never paired with this graph's edge ids.

Errors about the trip itself (422 point_not_on_network, no_route_found) are
reported before a failed pollution read (503), because the user can act on them.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from starlette.concurrency import run_in_threadpool

from osm.config import OsmSettings
from osm.graph import assemble_request_subgraph, compute_request_bbox
from osm.repository import OsmRepository
from pollution.repository import PollutionRepository

from .models import SnappedPoint, SolverInput, SourceMetadata
from .schemas import RouteRequestIn
from .validation import validate_route_request
from .weights import attach_edge_weights, count_sources


async def build_solver_input(
    payload: RouteRequestIn | Mapping[str, Any],
    *,
    osm: OsmRepository,
    pollution: PollutionRepository,
    settings: OsmSettings,
    request_id: str,
) -> SolverInput:
    request = validate_route_request(payload, settings)                       # R5
    mode = request.mode
    bbox = compute_request_bbox(request.origin, request.destination)
    active = await osm.get_active_version()
    graph, read = await asyncio.gather(                                       # R6 to R9, in parallel
        osm.read_road_graph(bbox, mode, include_geometry=False),
        pollution.read_pollution_weight(bbox, "latest", mode=mode, graph_version=active.version),
        return_exceptions=True,
    )
    if isinstance(graph, BaseException):
        raise graph
    subgraph = await run_in_threadpool(                                        # R10
        assemble_request_subgraph, graph, request.origin, request.destination, settings.snap_max_distance_m)
    if isinstance(read, BaseException):
        raise read
    edges, status = attach_edge_weights(subgraph, read, request.profile, request.pollutant)  # R11
    estimation, background = count_sources(edges, read.weights_by_edge_id())

    return SolverInput(
        request_id=request_id,
        mode=mode,
        speed_mps=request.profile.speed_mps,
        preference=request.preference,
        pollutant=request.pollutant,
        bbox=bbox,
        origin=SnappedPoint(request.origin, subgraph.origin_node, subgraph.origin_snap_distance_m),
        destination=SnappedPoint(request.destination, subgraph.destination_node, subgraph.destination_snap_distance_m),
        weights=status,
        source=SourceMetadata(
            graph_version=subgraph.graph_version,
            osm_timestamp=active.osm_timestamp,
            pollution_model_version=read.window.model_version,
            pollution_coverage_ratio=read.window.coverage_ratio,
            estimation_sources=estimation,
            background_sources=background,
            rejected_graph_rows=dict(subgraph.rejected),
        ),
        nodes={node_id: node.point for node_id, node in subgraph.nodes.items()},
        edges=edges,
    )
