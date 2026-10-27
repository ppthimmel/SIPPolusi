"""The OpenAPI document (public_api/openapi.json) is current and documents the contract:
request schema, success response, error responses, units and CRS."""
from __future__ import annotations

import json

import pytest

from public_api import openapi
from public_api.openapi_examples import (
    CELL_SURFACE_EXAMPLE,
    EDGE_SURFACE_EXAMPLE,
    FALLBACK_ROUTE_EXAMPLE,
    ROUTE_EXAMPLE,
)
from public_api.schemas import ErrorResponse, RouteFeatureCollection, SurfaceFeatureCollection

GEOJSON = "application/geo+json"


@pytest.fixture(scope="module")
def spec() -> dict:
    return openapi.build()


def _schema(spec, ref: str) -> dict:
    return spec["components"]["schemas"][ref.rsplit("/", 1)[1]]


def test_committed_spec_is_current(spec):
    committed = openapi.SPEC_PATH.read_text(encoding="utf-8")
    written_with = json.loads(committed)["info"]["x-generator"]
    if written_with != spec["info"]["x-generator"]:
        pytest.skip(f"openapi.json ditulis dengan {written_with}, sekarang {spec['info']['x-generator']}")
    assert committed == openapi.render(spec), "openapi.json usang: jalankan `python -m public_api.openapi --write`"


def test_only_gateway_routed_paths(spec):
    assert set(spec["paths"]) == {"/v1/routes", "/v1/exposure-surface"}
    assert all(openapi.is_public(p) for p in spec["paths"])
    assert not openapi.is_public("/v1/osm/version") and not openapi.is_public("/v1/pollution/windows")
    assert not {"SolveResultOut", "SolverInputOut", "HTTPValidationError"} & set(spec["components"]["schemas"])


def test_route_request_schema(spec):
    op = spec["paths"]["/v1/routes"]["post"]
    body = op["requestBody"]["content"]["application/json"]
    request = _schema(spec, body["schema"]["$ref"])
    assert set(request["required"]) == {"origin", "destination", "mode", "preference"}
    assert {"walk", "bike_no2", "preferensi_salah", "luar_wilayah"} <= set(body["examples"])


@pytest.mark.parametrize("path, method, model, codes", [
    ("/v1/routes", "post", "RouteFeatureCollection", {"400", "413", "422", "429", "500", "502", "503", "504"}),
    ("/v1/exposure-surface", "get", "SurfaceFeatureCollection", {"400", "429", "500", "502", "503", "504"}),
])
def test_success_and_error_responses(spec, path, method, model, codes):
    responses = spec["paths"][path][method]["responses"]
    ok = responses["200"]["content"]
    assert list(ok) == [GEOJSON] and ok[GEOJSON]["schema"]["$ref"].endswith("/" + model)
    assert {"Cache-Control", "X-Request-ID"} <= set(responses["200"]["headers"])
    assert codes <= set(responses)
    for code in codes | {"default"}:
        content = responses[code]["content"]["application/json"]
        assert content["schema"]["$ref"].endswith("/ErrorResponse")
        for example in content.get("examples", {}).values():
            assert ErrorResponse.model_validate(example["value"]).error.code
    for example in ok[GEOJSON]["examples"].values():
        {"RouteFeatureCollection": RouteFeatureCollection,
         "SurfaceFeatureCollection": SurfaceFeatureCollection}[model].model_validate(example["value"])


def test_every_operation_documents_the_uniform_error(spec):
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op["responses"]["default"]["content"]["application/json"]["schema"]["$ref"].endswith(
                "/ErrorResponse"), (method, path)
            assert "422" not in op["responses"] or "ErrorResponse" in json.dumps(op["responses"]["422"])


def test_error_codes_of_table_3_16_appear(spec):
    text = json.dumps(spec["paths"]["/v1/routes"])
    for code in ("invalid_request", "payload_too_large", "outside_study_area", "point_not_on_network",
                 "no_route_found", "rate_limited", "cache_unavailable", "route_service_unavailable",
                 "upstream_timeout"):
        assert f'"code": "{code}"' in text, code


@pytest.mark.parametrize("schema, field, unit", [
    ("RouteProperties", "travel_time_min", "menit"),
    ("RouteProperties", "distance_m", "meter"),
    ("RouteProperties", "exposure_index_min", "indeks·menit"),
    ("RouteProperties", "exposure_ug_min_m3", "µg·min/m³"),
    ("EdgeSurfaceProperties", "pm25_ugm3", "µg/m³"),
    ("CellSurfaceProperties", "no2_ugm3", "µg/m³"),
    ("RouteMetadata", "crs", "WGS 84"),
])
def test_units_and_crs_are_documented(spec, schema, field, unit):
    assert unit in spec["components"]["schemas"][schema]["properties"][field]["description"]


def test_surface_parameters(spec):
    params = {p["name"]: p for p in spec["paths"]["/v1/exposure-surface"]["get"]["parameters"]}
    assert params["bbox"]["required"] and params["zoom"]["required"] and not params["pollutant"]["required"]
    assert (params["zoom"]["schema"]["minimum"], params["zoom"]["schema"]["maximum"]) == (0, 22)
    assert params["pollutant"]["schema"]["enum"] == ["combined", "pm25", "no2"]


@pytest.mark.parametrize("example, model", [
    (ROUTE_EXAMPLE, RouteFeatureCollection),
    (FALLBACK_ROUTE_EXAMPLE, RouteFeatureCollection),
    (EDGE_SURFACE_EXAMPLE, SurfaceFeatureCollection),
    (CELL_SURFACE_EXAMPLE, SurfaceFeatureCollection),
])
def test_openapi_examples_match_the_schemas(example, model):
    model.model_validate(example)


def test_fallback_example_is_marked():
    meta, rec = FALLBACK_ROUTE_EXAMPLE["metadata"], FALLBACK_ROUTE_EXAMPLE["features"][0]["properties"]
    assert (meta["route_source"], meta["preference_applied"], meta["fallback_reason"]) == \
        ("baseline", False, "model_timeout")
    assert (rec["role"], rec["route_source"], rec["preference_applied"]) == ("recommended", "baseline", False)
    assert ROUTE_EXAMPLE["features"][0]["properties"]["route_source"] == "policy"  # original untouched
