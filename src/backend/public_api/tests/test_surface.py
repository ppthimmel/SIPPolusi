"""GET /v1/exposure-surface without a database: zoom levels, bbox limits, grid, cache header."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from osm.errors import CacheUnavailable, InvalidRequest
from osm.models import BBox
from public_api.config import ApiSettings
from public_api.schemas import SurfaceFeatureCollection
from public_api.surface import Grid, cell_size_m, max_age_s, parse_surface_bbox

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


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 10, 26, 5, 0, 0, tzinfo=timezone.utc), 3600),
    (datetime(2026, 10, 26, 5, 59, 30, tzinfo=timezone.utc), 30),
    (datetime(2026, 10, 26, 5, 59, 59, 900_000, tzinfo=timezone.utc), 1),
])
def test_max_age_runs_to_the_end_of_the_time_window(now, expected):
    assert max_age_s(now) == expected


# ------------------------------------------------------------------ endpoint

def test_zoom_14_and_up_returns_edges(make_client):
    repo = FakeSurfaceRepository(edge_rows=[EDGE_ROW])
    with make_client(pollution=repo) as c:
        resp = c.get("/v1/exposure-surface", params={"bbox": MONAS, "zoom": 15}, headers={"X-Request-ID": "s-1"})
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/geo+json"
    assert resp.headers["cache-control"].startswith("public, max-age=")
    assert 1 <= int(resp.headers["cache-control"].split("=")[1]) <= 3600
    fc = SurfaceFeatureCollection.model_validate(resp.json())
    m = fc.metadata
    assert (m.kind, m.zoom, m.layer, m.cell_size_m, m.feature_count, m.request_id) == ("edge", 15, "combined", None, 1, "s-1")
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
    assert "max-age" not in resp.headers.get("cache-control", "")  # errors are not cached
