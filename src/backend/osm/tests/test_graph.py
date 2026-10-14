"""Graph helpers against the synthetic grid (UT-BE-04, UT-BE-05, UT-BE-06)."""
from __future__ import annotations

import pytest

from osm.errors import NoRouteFound, PointNotOnNetwork
from osm.graph import assemble_request_subgraph, compute_request_bbox, request_buffer_m, snap_to_graph
from osm.models import BBox, Mode, Point, RoadGraph, RoadNode, haversine_m

from .conftest import ORIGIN_LAT, ORIGIN_LON, VERSION, edge, grid_graph, node_id, offset_north

ORIGIN = Point(ORIGIN_LON, ORIGIN_LAT)


# UT-BE-04 compute_request_bbox ------------------------------------------------

def test_buffer_is_500_m_for_1_km_trip():
    assert request_buffer_m(1000.0) == 500.0


def test_buffer_is_25_percent_for_4_km_trip():
    assert request_buffer_m(4000.0) == 1000.0


def test_explicit_buffer_wins():
    assert request_buffer_m(4000.0, buffer_m=300.0) == 300.0


def test_negative_buffer_rejected():
    with pytest.raises(ValueError):
        request_buffer_m(1000.0, buffer_m=-1)


@pytest.mark.parametrize("trip_m, margin_m", [(1000, 500), (4000, 1000)])
def test_bbox_holds_both_points_with_margin(trip_m, margin_m):
    destination = offset_north(ORIGIN, trip_m)
    bbox = compute_request_bbox(ORIGIN, destination)
    assert bbox.contains(ORIGIN) and bbox.contains(destination)
    south = Point(ORIGIN.lon, bbox.min_lat)
    north = Point(ORIGIN.lon, bbox.max_lat)
    # Margin is a lower bound on each side, at most ~1% larger.
    assert margin_m <= haversine_m(south, ORIGIN) <= margin_m * 1.01
    assert margin_m <= haversine_m(destination, north) <= margin_m * 1.01
    west = Point(bbox.min_lon, ORIGIN.lat)
    assert margin_m <= haversine_m(west, ORIGIN) <= margin_m * 1.01


# UT-BE-05 snap_to_graph -------------------------------------------------------

def test_snap_returns_node_50_m_away(grid):
    point = offset_north(grid.nodes[node_id(2, 2)].point, 50)
    snap = snap_to_graph(point, grid)
    assert snap.node_id == node_id(2, 2)
    assert snap.distance_m == pytest.approx(50, abs=0.01)


def test_snap_skips_node_only_bikes_can_use():
    # Node 1 is nearest but only touches a bike-only edge; walking must pick node 3.
    nodes = {
        1: RoadNode(1, ORIGIN_LON, ORIGIN_LAT),
        2: RoadNode(2, ORIGIN_LON + 0.001, ORIGIN_LAT),
        3: RoadNode(3, ORIGIN_LON, ORIGIN_LAT + 0.0005),
        4: RoadNode(4, ORIGIN_LON + 0.001, ORIGIN_LAT + 0.0005),
    }
    edges = [edge(1, 1, 2, walk=False, bike=True), edge(2, 3, 4)]
    g = RoadGraph(VERSION, Mode.WALK, BBox(106.7, -6.3, 106.9, -6.1), nodes, edges)
    point = Point(ORIGIN_LON, ORIGIN_LAT - 0.0001)
    assert snap_to_graph(point, g).node_id == 3
    assert snap_to_graph(point, g, mode=Mode.BIKE).node_id == 1


def test_snap_fails_when_nothing_within_200_m(grid):
    far = offset_north(grid.nodes[node_id(4, 2)].point, 250)
    with pytest.raises(PointNotOnNetwork) as exc:
        snap_to_graph(far, grid)
    assert exc.value.code == "point_not_on_network"
    assert exc.value.status_code == 422


def test_snap_accepts_node_exactly_200_m_away(grid):
    exactly = offset_north(grid.nodes[node_id(4, 2)].point, 200)
    assert snap_to_graph(exactly, grid, max_distance_m=200).node_id == node_id(4, 2)


# UT-BE-06 assemble_request_subgraph -------------------------------------------

def test_subgraph_keeps_only_origin_component(grid):
    origin = grid.nodes[node_id(0, 0)].point
    destination = grid.nodes[node_id(4, 4)].point
    sub = assemble_request_subgraph(grid, origin, destination)
    assert sub.origin_node == node_id(0, 0)
    assert sub.destination_node == node_id(4, 4)
    assert len(sub.nodes) == 25
    assert len(sub.edges) == 40
    assert 900 not in sub.nodes and 901 not in sub.nodes
    assert all(e.edge_id != 999 for e in sub.edges)


def test_subgraph_fails_when_destination_is_disconnected(grid):
    origin = grid.nodes[node_id(0, 0)].point
    island = grid.nodes[900].point
    with pytest.raises(NoRouteFound) as exc:
        assemble_request_subgraph(grid, origin, island)
    assert exc.value.code == "no_route_found"


def test_bike_follows_oneway_but_walk_does_not():
    nodes = {1: RoadNode(1, ORIGIN_LON, ORIGIN_LAT), 2: RoadNode(2, ORIGIN_LON + 0.001, ORIGIN_LAT)}
    edges = [edge(1, 1, 2, oneway=True)]
    a, b = nodes[1].point, nodes[2].point
    bbox = BBox(106.7, -6.3, 106.9, -6.1)

    bike = RoadGraph(VERSION, Mode.BIKE, bbox, nodes, edges)
    assert assemble_request_subgraph(bike, a, b).destination_node == 2
    with pytest.raises(NoRouteFound):
        assemble_request_subgraph(bike, b, a)

    walk = RoadGraph(VERSION, Mode.WALK, bbox, nodes, edges)
    assert assemble_request_subgraph(walk, b, a).destination_node == 1


def test_self_loops_are_dropped(grid):
    grid.edges.append(edge(1000, node_id(0, 0), node_id(0, 0)))
    sub = assemble_request_subgraph(grid, grid.nodes[node_id(0, 0)].point, grid.nodes[node_id(1, 1)].point)
    assert all(e.u != e.v for e in sub.edges)


def test_edges_not_allowed_for_mode_split_the_graph():
    g = grid_graph(Mode.WALK, with_island=False)
    # Close every walkable link between column 1 and column 2.
    g.edges = [
        edge(e.edge_id, e.u, e.v, walk=False) if {e.u % 5, e.v % 5} == {1, 2} and e.u // 5 == e.v // 5 else e
        for e in g.edges
    ]
    with pytest.raises(NoRouteFound):
        assemble_request_subgraph(g, g.nodes[node_id(0, 0)].point, g.nodes[node_id(0, 4)].point)
