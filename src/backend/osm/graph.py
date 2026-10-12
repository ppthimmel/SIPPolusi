"""In-memory graph helpers of the Backend's Perakit Subgraf Permintaan (Tabel 3.6).

These functions work on a RoadGraph already read from the OSM Cache DB and do
no I/O, so they can be unit tested with synthetic graphs (Tabel 5.5).
"""
from __future__ import annotations

from collections import deque

from .errors import NoRouteFound, PointNotOnNetwork
from .models import BBox, Mode, Point, RoadEdge, RoadGraph, SnapResult, Subgraph, haversine_m

MIN_BUFFER_M = 500.0
BUFFER_RATIO = 0.25
DEFAULT_SNAP_DISTANCE_M = 200.0
# Absorbs float rounding so a node exactly at max_distance_m is accepted (UT-BE-05d).
_DISTANCE_TOLERANCE_M = 1e-6


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
    margin = request_buffer_m(haversine_m(origin, destination), buffer_m)
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


def assemble_request_subgraph(
    road_graph: RoadGraph,
    origin: Point,
    destination: Point,
    max_distance_m: float = DEFAULT_SNAP_DISTANCE_M,
) -> Subgraph:
    """Subgraph reachable from the origin node (Tabel 3.6, UT-BE-06).

    Walking treats every edge as two-way, so this is the connected component of
    the origin. Cycling follows oneway, so it is the set reachable from the
    origin. Self-loops are dropped. Raises NoRouteFound when the destination
    node is not in that set.
    """
    mode = road_graph.mode
    origin_snap = snap_to_graph(origin, road_graph, max_distance_m, mode)
    destination_snap = snap_to_graph(destination, road_graph, max_distance_m, mode)

    usable = [e for e in road_graph.edges if mode.allows(e) and e.u in road_graph.nodes and e.v in road_graph.nodes]
    reached = _reachable_from(origin_snap.node_id, _adjacency(usable, mode))
    if destination_snap.node_id not in reached:
        raise NoRouteFound("Simpul asal dan simpul tujuan tidak terhubung pada subgraf permintaan.")

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
    )
