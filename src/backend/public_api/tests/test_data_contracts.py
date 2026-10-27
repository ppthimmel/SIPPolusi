"""Executable TI-SE-01 schemas, examples and serializer compatibility."""
from __future__ import annotations

import json
import copy
from datetime import datetime, timezone

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from osm.schemas import RoadGraphOut
from osm.models import BBox, Mode, RoadEdge, RoadNode
from pollution.models import EdgeWeight
from pollution.schemas import EdgeWeightOut

from .test_data_contracts_validation import graph_payload


def test_versioned_bundle_is_current_and_examples_obey_the_contract():
    from public_api import data_contracts as contracts

    schema = contracts.build()
    Draft202012Validator.check_schema(schema)
    assert json.loads(contracts.SCHEMA_PATH.read_text(encoding="utf-8")) == schema
    corpus = json.loads(contracts.EXAMPLES_PATH.read_text(encoding="utf-8"))
    for name, payload in corpus["valid"].items():
        validator = Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"}, format_checker=FormatChecker())
        validator.validate(payload)
        model = contracts.MODELS[name].model_validate(payload)
        validator.validate(model.model_dump(mode="json"))
    for case in corpus["invalid"]:
        with pytest.raises(ValidationError):
            contracts.MODELS[case["type"]].model_validate(case["payload"])
        if case["validation"] == "schema":
            validator = Draft202012Validator({**schema, "$ref": f"#/$defs/{case['type']}"}, format_checker=FormatChecker())
            assert list(validator.iter_errors(case["payload"])), case["reason"]


def test_real_graph_and_weight_serializers_obey_versioned_schema():
    from public_api import data_contracts as contracts

    schema = contracts.build()
    # These are the same serializers used by /v1/osm/graph and /v1/pollution/edge-weights.
    source = graph_payload()
    graph = RoadGraphOut.build("g1", Mode.WALK, BBox(*source["bbox"]),
                              {n["node_id"]: RoadNode(**n) for n in source["nodes"]},
                              [RoadEdge(**e) for e in source["edges"]])
    Draft202012Validator({**schema, "$ref": "#/$defs/RoadGraph"}).validate(graph.model_dump(mode="json"))
    weight = EdgeWeight(10, datetime(2026, 10, 27, 5, tzinfo=timezone.utc), 0., None, 0., 0.,
                        None, "idw", "g1", "idw-v1")
    payload = EdgeWeightOut.of(weight).model_dump(mode="json")
    Draft202012Validator({**schema, "$ref": "#/$defs/EdgeWeight"}).validate(payload)
    assert payload["pm25_ugm3"] == 0. and payload["no2_ugm3"] is None


@pytest.mark.parametrize("name", ["route_policy.geojson", "route_fallback.geojson"])
def test_real_route_examples_obey_versioned_schema(name):
    from public_api import data_contracts as contracts

    payload = json.loads((contracts.EXAMPLES_PATH.parent / name).read_text(encoding="utf-8"))
    Draft202012Validator({**contracts.build(), "$ref": "#/$defs/RouteOutput"},
                        format_checker=FormatChecker()).validate(payload)


@pytest.mark.parametrize("field,value", [("travel_time_min", -1), ("confidence_score", 1.1),
                                         ("concentration_coverage", -0.1), ("distance_m", float("inf"))])
def test_route_output_rejects_invalid_metrics(field, value):
    from public_api.schemas import RouteFeatureCollection
    from public_api.openapi_examples import ROUTE_EXAMPLE

    payload = copy.deepcopy(ROUTE_EXAMPLE)
    payload["features"][0]["properties"][field] = value
    with pytest.raises(ValidationError):
        RouteFeatureCollection.model_validate(payload)


@pytest.mark.parametrize("case", ["invalid_time", "invalid_offset", "not_hourly", "role_order"])
def test_route_output_enforces_time_and_route_roles(case):
    from public_api.schemas import RouteFeatureCollection
    from public_api.openapi_examples import ROUTE_EXAMPLE

    payload = copy.deepcopy(ROUTE_EXAMPLE)
    if case == "invalid_time":
        payload["metadata"]["time_window_start"] = "not-a-time"
    elif case == "invalid_offset":
        payload["metadata"]["time_window_start"] = "2026-10-27T05:00:00+00:60"
    elif case == "not_hourly":
        payload["metadata"]["time_window_start"] = "2026-10-27T05:30:00Z"
    else:
        payload["features"][1]["properties"]["role"] = "recommended"
    with pytest.raises(ValidationError):
        RouteFeatureCollection.model_validate(payload)
