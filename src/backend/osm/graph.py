"""In-memory graph helpers of the Backend's Perakit Subgraf Permintaan (Tabel 3.6).

These functions work on a RoadGraph already read from the OSM Cache DB and do
no I/O, so they can be unit tested with synthetic graphs (Tabel 5.5).
"""
from __future__ import annotations

import math
from collections import Counter, deque
from collections.abc import Iterable, Mapping
from typing import Any

from .errors import NoRouteFound, PointNotOnNetwork
from .models import BBox, Mode, Point, RoadEdge, RoadGraph, RoadNode, SnapResult, Subgraph, haversine_m

MIN_BUFFER_M = 500.0
# Floor for an explicit buffer_m, so the bbox never collapses to zero width when the
# points share a longitude or latitude (or are the same point) and buffer_m is 0.
MIN_MARGIN_M = 1.0
BUFFER_RATIO = 0.25
DEFAULT_SNAP_DISTANCE_M = 200.0
# Absorbs float rounding so a node exactly at max_distance_m is accepted (UT-BE-05d).
_DISTANCE_TOLERANCE_M = 1e-6
# Allowed gap between an edge's first/last vertex and its u/v node. Coordinates
# come from ST_AsGeoJSON(geom, 6), so they are rounded to 1e-6 degrees (~0.1 m).
_ENDPOINT_TOLERANCE_DEG = 1e-5

# Reasons an edge row is left out of a RoadGraph (RoadGraph.rejected keys).
REJECT_NOT_ALLOWED = "not_allowed_for_mode"
REJECT_DUPLICATE_ID = "duplicate_edge_id"
REJECT_MISSING_ENDPOINT = "missing_endpoint_node"
REJECT_MISSING_GEOMETRY = "missing_geometry"
REJECT_INVALID_LENGTH = "invalid_length"
REJECT_ENDPOINT_MISMATCH = "geometry_endpoint_mismatch"
REJECT_NODE_WITHOUT_GEOMETRY = "node_without_geometry"


def _valid_length(value: Any) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _near(vertex: list[float], node: RoadNode) -> bool:
    return (abs(vertex[0] - node.lon) <= _ENDPOINT_TOLERANCE_DEG
            and abs(vertex[1] - node.lat) <= _ENDPOINT_TOLERANCE_DEG)


def assemble_road_graph(
    graph_version: str,
    mode: Mode,
    bbox: BBox,
    node_rows: Iterable[Mapping[str, Any]],
    edge_rows: Iterable[Mapping[str, Any]],
) -> RoadGraph:
    """Build a validated RoadGraph from osm.road_node and osm.road_edge rows.

    An edge is kept only when the mode may use it, its edge_id is not repeated,
    both endpoint nodes exist with a location, it has a geometry of at least two
    vertices, length_m is a positive number and, when coordinates were fetched,
    the geometry starts at u and ends at v. Every other row is counted in
    RoadGraph.rejected by reason, so callers can always rely on geometry and
    length being usable for travel time, exposure and EdgeWeight mapping.

    Edge rows carry `has_geometry`; `coordinates` is optional ([[lon, lat], ...]).
    """
    rejected: Counter[str] = Counter()

    nodes: dict[int, RoadNode] = {}
    for r in node_rows:
        if r["lon"] is None or r["lat"] is None:
            rejected[REJECT_NODE_WITHOUT_GEOMETRY] += 1
            continue
        nodes[r["node_id"]] = RoadNode(r["node_id"], r["lon"], r["lat"], r.get("tags") or {})

    edges: list[RoadEdge] = []
    seen: set[int] = set()
    for r in edge_rows:
        edge = RoadEdge(
            edge_id=r["edge_id"], u=r["u"], v=r["v"], length_m=r["length_m"],
            highway=r["highway"], name=r.get("name"), oneway=bool(r["oneway"]),
            walk_allowed=bool(r["walk_allowed"]), bike_allowed=bool(r["bike_allowed"]),
            osm_way_id=r.get("osm_way_id"), coordinates=r.get("coordinates"),
        )
        coordinates = edge.coordinates
        if not mode.allows(edge):
            reason = REJECT_NOT_ALLOWED
        elif edge.edge_id in seen:
            reason = REJECT_DUPLICATE_ID
        elif edge.u not in nodes or edge.v not in nodes:
            reason = REJECT_MISSING_ENDPOINT
        elif not r["has_geometry"] or (coordinates is not None and len(coordinates) < 2):
            reason = REJECT_MISSING_GEOMETRY
        elif not _valid_length(edge.length_m):
            reason = REJECT_INVALID_LENGTH
        elif coordinates is not None and not (
            _near(coordinates[0], nodes[edge.u]) and _near(coordinates[-1], nodes[edge.v])
        ):
            reason = REJECT_ENDPOINT_MISMATCH
        else:
            seen.add(edge.edge_id)
            edges.append(edge)
            continue
        rejected[reason] += 1

    # Keep only nodes that a kept edge touches.
    used = {e.u for e in edges} | {e.v for e in edges}
    nodes = {node_id: node for node_id, node in nodes.items() if node_id in used}
    return RoadGraph(graph_version=graph_version, mode=mode, bbox=bbox,
                     nodes=nodes, edges=edges, rejected=dict(rejected))


def request_buffer_m(straight_line_m: float, buffer_m: float | None = None) -> float:
    """Margin around the origin-destination line: buffer_m, or max(500 m, 25% of the distance)."""
    if buffer_m is not None:
        if buffer_m < 0:
            raise ValueError("buffer_m must not be negative")
        return buffer_m
    return max(MIN_BUFFER_M, BUFFER_RATIO * straight_line_m)


def compute_request_bbox(origin: Point, destination: Point, buffer_m: float | None = None) -> BBox:
    """BBox of the request subgraph (Tabel 3.6, UT-BE-04).

    The margin leaves room for a low-exposure detour while keeping the
    subgraph proportional to the trip length rather than to the city.
    """
    margin = max(request_buffer_m(haversine_m(origin, destination), buffer_m), MIN_MARGIN_M)
    return BBox.around([origin, destination], margin)


def _usable_node_ids(road_graph: RoadGraph, mode: Mode) -> set[int]:
    ids: set[int] = set()
    for edge in road_graph.edges:
        if mode.allows(edge):
            ids.add(edge.u)
            ids.add(edge.v)
    return ids


def snap_to_graph(
    point: Point,
    road_graph: RoadGraph,
    max_distance_m: float = DEFAULT_SNAP_DISTANCE_M,
    mode: Mode | None = None,
) -> SnapResult:
    """Nearest node the mode can use, within max_distance_m (Tabel 3.6, UT-BE-05).

    A node only counts when at least one incident edge allows the mode, so a
    node reachable only by bicycle is skipped for walking.
    """
    mode = mode or road_graph.mode
    best_id: int | None = None
    best_distance = float("inf")
    for node_id in _usable_node_ids(road_graph, mode):
        node = road_graph.nodes.get(node_id)
        if node is None:
            continue
        distance = haversine_m(point, node.point)
        if distance < best_distance:
            best_id, best_distance = node_id, distance
    if best_id is None or best_distance > max_distance_m + _DISTANCE_TOLERANCE_M:
        raise PointNotOnNetwork(
            f"Tidak ada simpul yang dapat dilalui moda {mode.value} dalam radius {max_distance_m:g} m dari titik."
        )
    return SnapResult(best_id, best_distance)


def _adjacency(edges: list[RoadEdge], mode: Mode) -> dict[int, list[int]]:
    adjacency: dict[int, list[int]] = {}
    for edge in edges:
        if edge.is_self_loop:
            continue
        for start, end in mode.directions(edge):
            adjacency.setdefault(start, []).append(end)
    return adjacency


def _reachable_from(start: int, adjacency: dict[int, list[int]]) -> set[int]:
    seen = {start}
    queue = deque([start])
    while queue:
        for nxt in adjacency.get(queue.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _candidates(point: Point, road_graph: RoadGraph, node_ids: set[int], max_distance_m: float) -> list[SnapResult]:
    """Nodes in node_ids within max_distance_m of point, nearest first (ties by node id)."""
    found = []
    for node_id in node_ids:
        node = road_graph.nodes.get(node_id)
        if node is None:
            continue
        distance = haversine_m(point, node.point)
        if distance <= max_distance_m + _DISTANCE_TOLERANCE_M:
            found.append(SnapResult(node_id, distance))
    return sorted(found, key=lambda s: (s.distance_m, s.node_id))


def assemble_request_subgraph(
    road_graph: RoadGraph,
    origin: Point,
    destination: Point,
    max_distance_m: float = DEFAULT_SNAP_DISTANCE_M,
) -> Subgraph:
    """Snap origin and destination to feasible nodes and keep the part of the graph
    reachable from the origin (Tabel 3.6, UT-BE-05, UT-BE-06).

    Feasible snapping: the origin node must have an edge the mode may leave by and
    the destination node an edge the mode may arrive by (oneway matters for bikes).
    The nearest feasible origin is chosen first, then the nearest destination
    candidate reachable from it; when none is reachable, the next origin candidate
    is tried. Walking treats every edge as two-way, so the subgraph is the origin's
    connected component; cycling follows oneway, so it is the set reachable from
    the origin. Self-loops are dropped.

    Raises PointNotOnNetwork when an endpoint has no feasible node within
    max_distance_m, and NoRouteFound when no origin candidate reaches any
    destination candidate.
    """
    mode = road_graph.mode
    usable = [e for e in road_graph.edges if mode.allows(e) and e.u in road_graph.nodes and e.v in road_graph.nodes]
    adjacency = _adjacency(usable, mode)
    leaving = set(adjacency)
    arriving = {end for ends in adjacency.values() for end in ends}

    origins = _candidates(origin, road_graph, leaving, max_distance_m)
    if not origins:
        raise PointNotOnNetwork(
            f"Titik asal: tidak ada simpul yang dapat dilalui moda {mode.value} dalam radius {max_distance_m:g} m."
        )
    destinations = _candidates(destination, road_graph, arriving, max_distance_m)
    if not destinations:
        raise PointNotOnNetwork(
            f"Titik tujuan: tidak ada simpul yang dapat dilalui moda {mode.value} dalam radius {max_distance_m:g} m."
        )

    explored: list[set[int]] = []
    for origin_snap in origins:
        # Anything reachable from a node inside an earlier reached set is a subset of it,
        # so an origin candidate there cannot reach a destination that set missed.
        if any(origin_snap.node_id in seen for seen in explored):
            continue
        reached = _reachable_from(origin_snap.node_id, adjacency)
        destination_snap = next((d for d in destinations if d.node_id in reached), None)
        if destination_snap is not None:
            return Subgraph(
                graph_version=road_graph.graph_version,
                mode=mode,
                bbox=road_graph.bbox,
                origin_node=origin_snap.node_id,
                destination_node=destination_snap.node_id,
                origin_snap_distance_m=origin_snap.distance_m,
                destination_snap_distance_m=destination_snap.distance_m,
                nodes={n: road_graph.nodes[n] for n in reached},
                edges=[e for e in usable if not e.is_self_loop and e.u in reached and e.v in reached],
                rejected=dict(road_graph.rejected),
            )
        explored.append(reached)
    raise NoRouteFound("Simpul asal dan simpul tujuan tidak terhubung pada subgraf permintaan.")
