"""GET /v1/exposure-surface without a database: zoom levels, bbox limits, grid, cache header."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from osm.errors import CacheUnavailable, InvalidRequest
from osm.models import BBox
from public_api.config import ApiSettings
from public_api.schemas import SurfaceFeatureCollection
from public_api.surface import Grid, cell_size_m, etag_matches, parse_surface_bbox

from .conftest import EMPTY, FakeSurfaceRepository, error

MONAS = "106.822,-6.180,106.832,-6.170"           # about 1.2 km²
JAKARTA = "106.68,-6.38,106.98,-6.08"             # the study area, about 1 100 km²
EDGE_ROW = {"edge_id": 7, "exposure_index": 1.8467, "pm25_ugm3": 31.2, "no2_ugm3": 45.1, "confidence_score": 0.74,
            "estimation_source": "idw", "coordinates": [[106.8268, -6.1745], [106.8271, -6.1762]]}
CELL_ROW = {"ix": 23636, "iy": -1366, "edge_count": 41, "exposure_index": 1.6213, "exposure_index_max": 2.4102,
            "pm25_ugm3": 27.9, "no2_ugm3": None, "confidence_score": 0.7}


# ------------------------------------------------------------------ grid and limits

@pytest.mark.parametrize("zoom, size", [(22, None), (14, None), (13, 250), (12, 500), (11, 1000), (0, 1000)])
def test_cell_size_by_zoom(zoom, size):
    assert cell_size_m(zoom) == size


def test_grid_covers_the_bbox_with_aligned_cells():
    bbox = BBox.parse(MONAS)
    grid = Grid.covering(bbox, 250)
    env = grid.envelope()
    assert env.min_lon <= bbox.min_lon and env.min_lat <= bbox.min_lat
    assert env.max_lon >= bbox.max_lon and env.max_lat >= bbox.max_lat
    # Cells are about 250 m on each side.
    assert grid.dlon * 110_670 == pytest.approx(250, rel=0.005) and grid.dlat * 110_590 == pytest.approx(250, rel=0.005)


def test_cell_ids_stay_put_when_the_map_pans():
    a = Grid.covering(BBox.parse(MONAS), 500)
    b = Grid.covering(BBox.parse("106.8231,-6.1789,106.8331,-6.1689"), 500)  # shifted by ~100 m
    assert a.polygon(a.ix0 + 1, a.iy0 + 1) == b.polygon(a.ix0 + 1, a.iy0 + 1)


def test_cell_polygon_is_a_closed_counter_clockwise_ring():
    grid = Grid.covering(BBox.parse(MONAS), 1000)
    (ring,) = grid.polygon(grid.ix0, grid.iy0)
    assert len(ring) == 5 and ring[0] == ring[-1]
    area2 = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:]))
    assert area2 > 0  # positive signed area = counter-clockwise (RFC 7946 section 3.1.6)


@pytest.mark.parametrize("bbox, zoom, ok", [
    (MONAS, 15, True),
    ("106.80,-6.20,106.845,-6.155", 14, True),     # about 24.8 km²
    ("106.80,-6.20,106.85,-6.15", 14, False),      # about 30.6 km² > 25 km² per edge
    (JAKARTA, 15, False),
    (JAKARTA, 13, False),                          # about 17 600 cells of 250 m
    (JAKARTA, 12, True),                           # about 4 400 cells of 500 m
    (JAKARTA, 11, True),
    ("100,-10,115,0", 5, False),                   # Java at 1 km: far over 10 000 cells
])
def test_bbox_limit_depends_on_zoom(bbox, zoom, ok):
    settings = ApiSettings(_env_file=None)
    if ok:
        parse_surface_bbox(bbox, zoom, settings)
    else:
        with pytest.raises(InvalidRequest, match="terlalu luas"):
            parse_surface_bbox(bbox, zoom, settings)


@pytest.mark.parametrize("header, match", [
    (None, False), ("", False), ('W/"abc"', True), ('"abc"', True), ('W/"x", W/"abc"', True), ("*", True),
    ('W/"abcd"', False),
])
def test_if_none_match_uses_weak_comparison(header, match):
    assert etag_matches(header, 'W/"abc"') is match


# ------------------------------------------------------------------ endpoint

def test_zoom_14_and_up_returns_edges(make_client):
    repo = FakeSurfaceRepository(edge_rows=[EDGE_ROW])
    with make_client(pollution=repo) as c:
        resp = c.get("/v1/exposure-surface", params={"bbox": MONAS, "zoom": 15}, headers={"X-Request-ID": "s-1"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/geo+json"
    assert resp.headers["cache-control"] == "public, no-cache"
    assert resp.headers["etag"].startswith('W/"') and resp.headers["x-request-id"] == "s-1"
    fc = SurfaceFeatureCollection.model_validate(resp.json())
    m = fc.metadata
    assert (m.kind, m.zoom, m.layer, m.cell_size_m, m.feature_count) == ("edge", 15, "combined", None, 1)
    assert "request_id" not in resp.json()["metadata"]  # the body may be reused for other requests
    assert m.units["pm25_ugm3"] == "µg/m³" and m.exposure_index_classes == [0.5, 1.0, 2.0, 3.0]
    assert m.time_window_start == "2026-10-13T05:00:00Z" and m.time_window_end == "2026-10-13T06:00:00Z"
    assert repo.surface_calls[0][:2] == ("edges", "jakarta-20261005")
    props = resp.json()["features"][0]["properties"]
    assert set(props) >= {"exposure_index", "pm25_ugm3", "no2_ugm3", "confidence_score"}  # all layers at once


def test_zoom_below_14_returns_grid_cells(make_client):
    repo = FakeSurfaceRepository(cell_rows=[CELL_ROW])
    with make_client(pollution=repo) as c:
        resp = c.get("/v1/exposure-surface", params={"bbox": JAKARTA, "zoom": 12, "pollutant": "no2"})
    assert resp.status_code == 200, resp.text
    fc = SurfaceFeatureCollection.model_validate(resp.json())
    assert (fc.metadata.kind, fc.metadata.cell_size_m, fc.metadata.layer) == ("grid", 500, "no2")
    feature = resp.json()["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"]["cell_id"] == "500m:23636:-1366" and feature["properties"]["no2_ugm3"] is None
    kind, _, envelope, cell_deg, (ix0, ix1, iy0, iy1) = repo.surface_calls[0]
    assert kind == "cells" and (ix1 - ix0) * (iy1 - iy0) <= 10_000
    assert envelope.min_lon == pytest.approx(ix0 * cell_deg[0]) and envelope.max_lat == pytest.approx(iy1 * cell_deg[1])


def test_empty_area_is_200_with_no_features(make_client):
    with make_client(pollution=FakeSurfaceRepository()) as c:
        body = c.get("/v1/exposure-surface", params={"bbox": "106.85,-6.09,106.86,-6.08", "zoom": 16}).json()
    assert body["features"] == [] and body["metadata"]["feature_count"] == 0


@pytest.mark.parametrize("params", [
    {"bbox": JAKARTA, "zoom": 15},
    {"bbox": "106.82,-6.18,106.83", "zoom": 15},
    {"bbox": "106.83,-6.17,106.82,-6.18", "zoom": 15},
    {"bbox": "a,b,c,d", "zoom": 15},
    {"bbox": "106.82,-6.18,106.83,nan", "zoom": 15},
    {"bbox": MONAS, "zoom": 23},
    {"bbox": MONAS, "zoom": -1},
    {"bbox": MONAS, "zoom": "x"},
    {"bbox": MONAS},
    {"zoom": 15},
    {"bbox": MONAS, "zoom": 15, "pollutant": "co"},
])
def test_invalid_query_is_400_without_reading_the_cache(make_client, params):
    repo = FakeSurfaceRepository()
    with make_client(pollution=repo) as c:
        error(c.get("/v1/exposure-surface", params=params), 400, "invalid_request")
    assert repo.surface_calls == []


@pytest.mark.parametrize("failure, reason", [
    (EMPTY, "cache_empty"),
    (CacheUnavailable("Basis data cache tidak dapat dihubungi.", CacheUnavailable.DATABASE_UNAVAILABLE),
     "database_unavailable"),
])
def test_cache_failure_is_503(make_client, failure, reason):
    with make_client(pollution=FakeSurfaceRepository(fail=failure)) as c:
        resp = c.get("/v1/exposure-surface", params={"bbox": MONAS, "zoom": 15})
    assert error(resp, 503, "cache_unavailable")["reason"] == reason
    assert "cache-control" not in resp.headers and "etag" not in resp.headers  # errors are not cached


# ------------------------------------------------------------------ caching (TI-SE-08 QA)

def _get(c, etag=None, **params):
    headers = {"If-None-Match": etag} if etag else {}
    return c.get("/v1/exposure-surface", params={"bbox": MONAS, "zoom": 15, **params}, headers=headers)


def test_same_data_revalidates_with_304_without_the_surface_query(make_client):
    repo = FakeSurfaceRepository(edge_rows=[EDGE_ROW])
    with make_client(pollution=repo) as c:
        first = _get(c)
        again = _get(c, first.headers["etag"])
    assert again.status_code == 304 and again.content == b""
    assert again.headers["etag"] == first.headers["etag"] and again.headers["cache-control"] == "public, no-cache"
    assert again.headers["x-request-id"] != first.headers["x-request-id"]  # each request keeps its own ID
    assert len(repo.surface_calls) == 1  # 304 answered from the window and graph version only


def test_newer_complete_window_is_served_at_once(make_client):
    repo = FakeSurfaceRepository(edge_rows=[EDGE_ROW])
    with make_client(pollution=repo) as c:
        first = _get(c)
        old = repo.window
        repo.window = replace(old, time_window_start=old.time_window_start + timedelta(hours=1),
                              completed_at=old.completed_at + timedelta(hours=1))
        newer = _get(c, first.headers["etag"])
    assert newer.status_code == 200 and newer.headers["etag"] != first.headers["etag"]
    assert newer.json()["metadata"]["time_window_start"] == "2026-10-13T06:00:00Z"


QUERY_CHANGES = {"bbox": {"bbox": "106.822,-6.180,106.833,-6.170"}, "zoom": {"zoom": 16}, "layer": {"pollutant": "no2"}}


@pytest.mark.parametrize("change", ["rewritten_window", "now_stale", *QUERY_CHANGES])
def test_etag_changes_with_everything_the_body_depends_on(make_client, change):
    repo = FakeSurfaceRepository(edge_rows=[EDGE_ROW])
    with make_client(pollution=repo) as c:
        first = _get(c).headers["etag"]
        if change == "rewritten_window":  # same window written again by the Data Worker
            repo.window = replace(repo.window, completed_at=repo.window.completed_at + timedelta(minutes=5))
        elif change == "now_stale":
            repo.stale = True
        second = _get(c, first, **QUERY_CHANGES.get(change, {}))
    assert second.status_code == 200 and second.headers["etag"] != first
