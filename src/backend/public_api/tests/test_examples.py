"""Example responses in public_api/examples/ (TI-SE-08 evidence).

Every file is a real response of the public API on data written by the Data Worker
to a local PostGIS (the 4 x 4 grid of the cache-flow tests). Regenerate with:

    UPDATE_EXAMPLES=1 TEST_DATABASE_URL=postgresql://test:test@localhost:55432/test \\
        pytest tests/test_examples.py

Without UPDATE_EXAMPLES the cache-flow test checks that the committed files still
equal what the API answers (apart from the time window, which follows the clock).
"""
from __future__ import annotations

import copy
import json
import os
import pathlib

import pytest

from osm.config import get_settings
from planner.tests.test_flow import body as route_body
from pollution.config import get_pollution_settings
from pollution.tests.conftest import admin_dsn, cache_dsn, writer  # noqa: F401  (fixtures)
from pollution.tests.test_cache_flow import write_graph
from public_api.config import get_api_settings
from public_api.schemas import ErrorResponse, RouteFeatureCollection, SurfaceFeatureCollection
from solver.config import get_solver_settings

from .test_surface_db import AROUND_GRID, _write_varied

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"
MODELS = {
    "route_policy.geojson": RouteFeatureCollection,
    "route_fallback.geojson": RouteFeatureCollection,
    "surface_edges_z15.geojson": SurfaceFeatureCollection,
    "surface_grid_z13.geojson": SurfaceFeatureCollection,
}
_CACHED = (get_settings, get_pollution_settings, get_solver_settings, get_api_settings)


def _load(name: str):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", MODELS)
def test_committed_example_matches_the_schema(name):
    MODELS[name].model_validate(_load(name))


def test_committed_error_examples_match_the_schema():
    errors = _load("errors.json")
    assert {"400", "413", "422", "503"} <= {key.split("_")[0] for key in errors}
    for key, example in errors.items():
        assert example["status"] == int(key.split("_")[0])
        assert ErrorResponse.model_validate(example["body"]).error.code in key


# ------------------------------------------------------------------ regenerate on the local database

def _normalise(doc):
    doc = copy.deepcopy(doc)
    for key in ("time_window_start", "time_window_end"):
        if key in doc.get("metadata", {}):
            doc["metadata"][key] = "<time window>"
    return doc


@pytest.fixture
def client_with(cache_dsn, monkeypatch):
    from fastapi.testclient import TestClient

    from osm.app import create_app

    opened = []

    def make(**env):
        monkeypatch.setenv("DATABASE_URL", cache_dsn)
        for key in ("ROUTE_SOLVER_PROVIDER", "ROUTE_SOLVER_FAULT", "ROUTE_SOLVER_FALLBACK"):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ROUTE_SOLVER_PROVIDER", "mock")
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        for cached in _CACHED:
            cached.cache_clear()
        client = TestClient(create_app())
        client.__enter__()
        opened.append(client)
        return client

    yield make
    for client in opened:
        client.__exit__(None, None, None)
    for cached in _CACHED:
        cached.cache_clear()


@pytest.mark.cacheflow
def test_examples_are_what_the_api_answers(writer, client_with):
    def get(c, path, rid, **kw):
        return c.get(path, headers={"X-Request-ID": rid}, **kw)

    def post(c, rid, body, **kw):
        return c.post("/v1/routes", json=body, headers={"X-Request-ID": rid}, **kw)

    write_graph(writer, "v1")
    client = client_with()
    errors = {"503_cache_unavailable": post(client, "contoh-cache-empty", route_body())}

    _write_varied(writer)
    responses = {
        "route_policy.geojson": post(client, "contoh-route-policy", route_body()),
        "surface_edges_z15.geojson": get(client, "/v1/exposure-surface", "contoh-surface-z15",
                                         params={"bbox": AROUND_GRID, "zoom": 15}),
        "surface_grid_z13.geojson": get(client, "/v1/exposure-surface", "contoh-surface-z13",
                                        params={"bbox": AROUND_GRID, "zoom": 13}),
    }
    origin = route_body()["origin"]
    errors |= {
        "400_invalid_request": post(client, "contoh-400", {**route_body(), "preference": {"alpha": 0.6, "beta": 0.5}}),
        "413_payload_too_large": post(client, "contoh-413", {**route_body(), "padding": "x" * 20_000}),
        "422_outside_study_area": post(client, "contoh-422-luar", {**route_body(),
                                                                    "destination": {"lat": -5.75, "lon": 106.55}}),
        "422_point_not_on_network": post(client, "contoh-422-snap", {**route_body(),
                                                                      "destination": {"lat": -6.18, "lon": 106.80}}),
        "422_no_route_found": post(client, "contoh-422-same", {**route_body(), "destination": origin}),
    }
    fallback = client_with(ROUTE_SOLVER_FAULT="error")
    responses["route_fallback.geojson"] = post(fallback, "contoh-route-fallback", route_body())
    no_fallback = client_with(ROUTE_SOLVER_FAULT="error", ROUTE_SOLVER_FALLBACK="none")
    errors["503_route_service_unavailable"] = post(no_fallback, "contoh-503-model", route_body())

    generated = {}
    for name, resp in responses.items():
        assert resp.status_code == 200, (name, resp.text)
        assert resp.headers["content-type"] == "application/geo+json"
        generated[name] = MODELS[name].model_validate(resp.json()) and resp.json()
    error_doc = {}
    for key, resp in errors.items():
        assert resp.status_code == int(key.split("_")[0]), (key, resp.text)
        body = ErrorResponse.model_validate(resp.json())
        assert body.error.code in key
        error_doc[key] = {"status": resp.status_code, "body": resp.json()}

    assert generated["route_policy.geojson"]["metadata"]["route_source"] == "policy"
    assert generated["route_fallback.geojson"]["metadata"]["fallback_reason"] == "model_error"
    assert generated["surface_grid_z13.geojson"]["metadata"]["kind"] == "grid"

    if os.environ.get("UPDATE_EXAMPLES"):
        EXAMPLES.mkdir(exist_ok=True)
        for name, doc in generated.items():
            (EXAMPLES / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (EXAMPLES / "errors.json").write_text(json.dumps(error_doc, indent=2, ensure_ascii=False) + "\n",
                                              encoding="utf-8")
        return
    for name, doc in generated.items():
        assert _normalise(doc) == _normalise(_load(name)), f"{name} berbeda; jalankan dengan UPDATE_EXAMPLES=1"
    assert error_doc == _load("errors.json")
