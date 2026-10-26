"""validate_route_request and resolve_mode (UT-BE-02, UT-BE-03)."""
from __future__ import annotations

import pytest

from osm.errors import InvalidRequest, OutsideStudyArea
from osm.models import Mode
from planner.validation import resolve_mode, validate_route_request

from .conftest import request_body


def test_ut_be_02a_valid_request(settings):
    req = validate_route_request(request_body(alpha=0.6, beta=0.4), settings)
    assert req.preference.alpha == 0.6 and req.preference.beta == 0.4
    assert req.mode is Mode.WALK and req.profile.speed_mps == 1.3 and req.pollutant == "pm25"


def test_preference_tolerance(settings):
    validate_route_request(request_body(alpha=0.3, beta=0.7 + 5e-7), settings)
    with pytest.raises(InvalidRequest):
        validate_route_request(request_body(alpha=0.3, beta=0.7 + 2e-6), settings)


@pytest.mark.parametrize("change", [
    {"preference": {"alpha": 0.6, "beta": 0.5}},
    {"preference": {"alpha": -0.1, "beta": 1.1}},
    {"mode": "car"},
    {"origin": {"lat": 106.0, "lon": 106.8}},
    {"pollutant": "co"},
    {"preference": None},
])
def test_ut_be_02b_invalid_request(settings, change):
    body = {**request_body(), **change}
    body = {k: v for k, v in body.items() if v is not None}
    with pytest.raises(InvalidRequest):
        validate_route_request(body, settings)


@pytest.mark.parametrize("which, label", [("origin", "asal"), ("destination", "tujuan")])
def test_ut_be_02c_outside_study_area(settings, which, label):
    body = request_body()
    body[which] = {"lat": -5.75, "lon": 106.55}
    with pytest.raises(OutsideStudyArea, match=label):
        validate_route_request(body, settings)


def test_ut_be_03a_mode_profiles(settings):
    walk, bike = resolve_mode("walk", settings), resolve_mode(Mode.BIKE, settings)
    assert (walk.mode, walk.speed_mps, walk.access_column) == (Mode.WALK, 1.3, "walk_allowed")
    assert (bike.mode, bike.speed_mps, bike.access_column) == (Mode.BIKE, 4.2, "bike_allowed")


def test_ut_be_03b_unknown_mode(settings):
    with pytest.raises(ValueError):
        resolve_mode("scooter", settings)
