"""TI-SE-01: reject malformed exchanges before they cross a component boundary."""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from osm.schemas import RoadGraphOut
from planner.schemas import RouteRequestIn
from pollution.schemas import EdgeWeightOut, EdgeWeightsOut, TimeWindowOut

from .conftest import request_body


@pytest.mark.parametrize("change", [
    {"origin": {"lon": True, "lat": -6.2}},
    {"origin": {"lon": "106.8", "lat": -6.2}},
    {"preference": {"alpha": True, "beta": 0}},
    {"preference": {"alpha": 0.6, "beta": 0.5}},
])
def test_request_schema_itself_rejects_invalid_coordinates_and_preferences(change):
    with pytest.raises(ValidationError):
        RouteRequestIn.model_validate({**request_body(), **change})


def graph_payload():
    return dict(graph_version="g1", mode="walk", bbox=[106.7, -6.3, 106.9, -6.1],
                node_count=2, edge_count=1, rejected={},
                nodes=[dict(node_id=1, lon=106.8, lat=-6.2, tags={}),
                       dict(node_id=2, lon=106.801, lat=-6.2, tags={})],
                edges=[dict(edge_id=10, u=1, v=2, length_m=110., highway=None, name=None,
                            oneway=False, walk_allowed=True, bike_allowed=True, osm_way_id=None,
                            coordinates=[[106.8, -6.2], [106.801, -6.2]])])


@pytest.mark.parametrize("case", ["missing_node", "duplicate_node", "duplicate_edge", "count", "bbox",
                                      "length", "coordinate", "vertex_shape", "mode"])
def test_graph_exchange_rejects_broken_topology_and_geometry(case):
    p = copy.deepcopy(graph_payload())
    if case == "missing_node":
        p["edges"][0]["v"] = 99
    elif case == "duplicate_node":
        p["nodes"][1]["node_id"] = 1
    elif case == "duplicate_edge":
        p["edges"].append(p["edges"][0])
        p["edge_count"] = 2
    elif case == "count":
        p["node_count"] = 5
    elif case == "bbox":
        p["bbox"] = [106.9, -6.3, 106.7, -6.1]
    elif case == "length":
        p["edges"][0]["length_m"] = -1
    elif case == "coordinate":
        p["edges"][0]["coordinates"][0][1] = 91
    elif case == "vertex_shape":
        p["edges"][0]["coordinates"][0] = [106.8]
    elif case == "mode":
        p["edges"][0]["walk_allowed"] = False
    with pytest.raises(ValidationError):
        RoadGraphOut.model_validate(p)


def edge_payload():
    return dict(edge_id=10, pm25_ugm3=0., no2_ugm3=None, exposure_index=0., confidence_score=0.,
                background_source=None, estimation_source="idw", model_metadata={}, trace_run_id=None,
                graph_version="g1", model_version="idw-v1")


@pytest.mark.parametrize("field,value", [("pm25_ugm3", -1), ("no2_ugm3", float("inf")),
                                         ("exposure_index", float("nan")), ("confidence_score", 1.1),
                                         ("background_source", "unknown"), ("estimation_source", "unknown")])
def test_weight_exchange_rejects_invalid_numeric_values(field, value):
    with pytest.raises(ValidationError):
        EdgeWeightOut.model_validate({**edge_payload(), field: value})


@pytest.mark.parametrize("end", ["2026-10-27T06:30:00Z", "2026-10-27T05:00:00Z", "2026-10-27T07:00:00Z"])
def test_weight_batch_rejects_invalid_window(end):
    with pytest.raises(ValidationError):
        EdgeWeightsOut.model_validate(dict(
            time_window_start="2026-10-27T05:00:00Z", time_window_end=end,
            model_version="idw-v1", coverage_ratio=1., graph_version="g1", mode="walk",
            bbox=[106.7, -6.3, 106.9, -6.1], data_stale=False, age_minutes=0., count=1,
            excluded={}, weights=[edge_payload()]))


@pytest.mark.parametrize("start", [1793077200, True, "1793077200", "20261027T05+00:00"])
def test_window_exchange_requires_iso_time_instead_of_epoch(start):
    with pytest.raises(ValidationError):
        TimeWindowOut(time_window_start=start, time_window_end="2026-10-27T06:00:00Z")


def test_window_exchange_normalizes_offset_to_utc():
    w = TimeWindowOut(time_window_start="2026-10-27T12:00:00+07:00", time_window_end="2026-10-27T13:00:00+07:00")
    assert w.model_dump(mode="json") == {"time_window_start": "2026-10-27T05:00:00Z", "time_window_end": "2026-10-27T06:00:00Z"}


def test_window_exchange_rejects_out_of_range_timezone_offset():
    with pytest.raises(ValidationError):
        TimeWindowOut(time_window_start="2026-10-27T05:00:00+00:60",
                      time_window_end="2026-10-27T06:00:00+00:60")
