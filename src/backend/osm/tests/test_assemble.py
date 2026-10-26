"""assemble_road_graph: building a validated RoadGraph from osm rows.

Covers the acceptance criteria of the RoadGraph issue on the backend side:
edges without geometry, invalid lengths, mode access differences, oneway
edges and isolated origin/destination after validation.
"""
from __future__ import annotations

import math

import pytest

from osm.errors import NoRouteFound, PointNotOnNetwork
from osm.graph import (
    REJECT_DUPLICATE_ID,
    REJECT_ENDPOINT_MISMATCH,
    REJECT_INVALID_LENGTH,
    REJECT_MISSING_ENDPOINT,
    REJECT_MISSING_GEOMETRY,
    REJECT_NODE_WITHOUT_GEOMETRY,
    REJECT_NOT_ALLOWED,
    assemble_request_subgraph,
    assemble_road_graph,
)
from osm.models import BBox, Mode

VERSION = "jakarta-20261005"
BBOX = BBox(106.79, -6.21, 106.82, -6.19)
# Three nodes on a line, ~110 m apart.
N1, N2, N3 = (1, 106.800, -6.200), (2, 106.801, -6.200), (3, 106.802, -6.200)


def node_row(node, *, located=True, tags=None):
    node_id, lon, lat = node
    return {"node_id": node_id, "lon": lon if located else None, "lat": lat if located else None,
            "tags": tags or {}}


def edge_row(edge_id, a, b, *, walk=True, bike=True, oneway=False, length_m=110.6,
             has_geometry=True, coordinates="auto"):
    row = {"edge_id": edge_id, "u": a[0], "v": b[0], "length_m": length_m, "highway": "residential",
           "name": None, "oneway": oneway, "walk_allowed": walk, "bike_allowed": bike,
           "osm_way_id": 77, "has_geometry": has_geometry}
    if coordinates == "auto":
        row["coordinates"] = [[a[1], a[2]], [b[1], b[2]]] if has_geometry else None
    elif coordinates is not None:
        row["coordinates"] = coordinates
    return row


NODES = [node_row(N1), node_row(N2), node_row(N3)]


def build(edges, mode=Mode.WALK, nodes=NODES):
    return assemble_road_graph(VERSION, mode, BBOX, nodes, edges)


def test_valid_rows_build_graph_with_version():
    g = build([edge_row(10, N1, N2), edge_row(11, N2, N3)])
    assert g.graph_version == VERSION
    assert [e.edge_id for e in g.edges] == [10, 11]
    assert set(g.nodes) == {1, 2, 3}
    assert g.rejected == {}


def test_edge_without_geometry_is_rejected_and_its_lone_node_dropped():
    g = build([edge_row(10, N1, N2), edge_row(11, N2, N3, has_geometry=False)])
    assert [e.edge_id for e in g.edges] == [10]
    assert g.rejected == {REJECT_MISSING_GEOMETRY: 1}
    assert 3 not in g.nodes


def test_single_vertex_geometry_is_rejected():
    g = build([edge_row(10, N1, N2, coordinates=[[N1[1], N1[2]]])])
    assert g.edges == [] and g.rejected == {REJECT_MISSING_GEOMETRY: 1}


def test_rows_without_coordinates_are_kept_when_geometry_exists():
    # include_geometry=False: SQL sends has_geometry but no coordinates.
    g = build([edge_row(10, N1, N2, coordinates=None)])
    assert len(g.edges) == 1 and g.edges[0].coordinates is None


@pytest.mark.parametrize("length", [None, 0.0, -5.0, math.nan, math.inf])
def test_invalid_length_is_rejected(length):
    g = build([edge_row(10, N1, N2, length_m=length)])
    assert g.edges == [] and g.rejected == {REJECT_INVALID_LENGTH: 1}


def test_geometry_must_run_from_u_to_v():
    reversed_line = [[N2[1], N2[2]], [N1[1], N1[2]]]
    g = build([edge_row(10, N1, N2, coordinates=reversed_line)])
    assert g.edges == [] and g.rejected == {REJECT_ENDPOINT_MISMATCH: 1}


def test_missing_or_unlocated_endpoint_node_rejects_edge():
    nodes = [node_row(N1), node_row(N2, located=False)]
    g = build([edge_row(10, N1, N2), edge_row(11, N1, N3)], nodes=nodes)
    assert g.edges == []
    assert g.rejected == {REJECT_NODE_WITHOUT_GEOMETRY: 1, REJECT_MISSING_ENDPOINT: 2}


def test_duplicate_edge_id_is_rejected():
    g = build([edge_row(10, N1, N2), edge_row(10, N2, N3)])
    assert [e.edge_id for e in g.edges] == [10]
    assert g.rejected == {REJECT_DUPLICATE_ID: 1}


def test_access_differs_between_modes():
    rows = [edge_row(10, N1, N2), edge_row(11, N2, N3, walk=False), edge_row(12, N1, N3, bike=False)]
    walk = build(rows, Mode.WALK)
    bike = build(rows, Mode.BIKE)
    assert {e.edge_id for e in walk.edges} == {10, 12}
    assert {e.edge_id for e in bike.edges} == {10, 11}
    assert walk.rejected == bike.rejected == {REJECT_NOT_ALLOWED: 1}


def test_oneway_survives_assembly_and_limits_bike_direction():
    # ~550 m apart, so each point has only its own node within the 200 m snap radius.
    far = (2, 106.805, -6.200)
    g = build([edge_row(10, N1, far, oneway=True, length_m=550.0)], Mode.BIKE,
              nodes=[node_row(N1), node_row(far)])
    assert g.edges[0].oneway is True
    a, b = g.nodes[1].point, g.nodes[2].point
    assert assemble_request_subgraph(g, a, b).destination_node == 2
    with pytest.raises(PointNotOnNetwork, match="Titik asal"):
        assemble_request_subgraph(g, b, a)


def test_destination_isolated_after_rejecting_its_only_edge():
    g = build([edge_row(10, N1, N2), edge_row(11, N2, N3, has_geometry=False),
               edge_row(12, (4, 106.805, -6.200), (5, 106.806, -6.200))],
              nodes=NODES + [node_row((4, 106.805, -6.200)), node_row((5, 106.806, -6.200))])
    origin = g.nodes[1].point
    island = g.nodes[4].point
    with pytest.raises(NoRouteFound):
        assemble_request_subgraph(g, origin, island)
    sub = assemble_request_subgraph(g, origin, g.nodes[2].point)
    assert sub.rejected == {REJECT_MISSING_GEOMETRY: 1}


def test_edge_weights_map_by_edge_id():
    g = build([edge_row(10, N1, N2), edge_row(11, N2, N3)])
    weights = {10: 1.4, 11: 0.8, 999: 2.0}  # EdgeWeight.exposure_index by edge_id
    index = g.edges_by_id()
    mapped = {edge_id: w for edge_id, w in weights.items() if edge_id in index}
    assert mapped == {10: 1.4, 11: 0.8}
    assert set(index) == {e.edge_id for e in g.edges}
