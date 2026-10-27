"""validate_route_request and resolve_mode (Tabel 3.6, UT-BE-02, UT-BE-03).

Errors are raised before any cache is read: 400 invalid_request for schema,
range, mode, pollutant or preference problems, 422 outside_study_area when a
point lies outside the study area.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from osm.config import OsmSettings
from osm.errors import InvalidRequest, OutsideStudyArea
from osm.models import Mode, ModeProfile, Point

from .models import Preference, ValidatedRouteRequest
from .schemas import RouteRequestIn


def resolve_mode(mode: str | Mode, settings: OsmSettings) -> ModeProfile:
    """ModeProfile with the reference speed of the mode (UT-BE-03).

    Raises ValueError for an unknown mode such as "scooter".
    """
    try:
        resolved = Mode(mode)
    except ValueError as exc:
        raise ValueError(f"moda tidak dikenal: {mode!r}; gunakan 'walk' atau 'bike'") from exc
    speed = settings.walk_speed_mps if resolved is Mode.WALK else settings.bike_speed_mps
    return ModeProfile(resolved, speed)


def _first_error(exc: ValidationError) -> str:
    problems = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        problems.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "; ".join(problems)


def validate_route_request(payload: RouteRequestIn | Mapping[str, Any], settings: OsmSettings) -> ValidatedRouteRequest:
    """Check a RouteRequest and turn it into domain values (UT-BE-02)."""
    if not isinstance(payload, RouteRequestIn):
        try:
            payload = RouteRequestIn.model_validate(payload)
        except ValidationError as exc:
            raise InvalidRequest(_first_error(exc)) from exc

    alpha, beta = payload.preference.alpha, payload.preference.beta
    origin = Point(payload.origin.lon, payload.origin.lat)
    destination = Point(payload.destination.lon, payload.destination.lat)
    for label, point in (("Titik asal", origin), ("Titik tujuan", destination)):
        if not settings.study_area.contains(point):
            raise OutsideStudyArea(f"{label} berada di luar wilayah studi (DKI Jakarta).")

    return ValidatedRouteRequest(
        origin=origin,
        destination=destination,
        profile=resolve_mode(payload.mode, settings),
        preference=Preference(alpha, beta),
        pollutant=payload.pollutant,
    )
