"""POST /v1/geocode/* with a fake Photon server: results, the study area, failures and the logs (TI-SE-12)."""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from public_api.config import ApiSettings
from public_api.geocoding import USER_AGENT, PhotonGeocoder, make_geocoder
from public_api.schemas import GeocodeReverseResponse, GeocodeSearchResponse

from .conftest import error

MARKER = "Zqxmarker Monas"
LAT, LON = -6.17719, 106.83113
SEARCH = "/v1/geocode/search"
REVERSE = "/v1/geocode/reverse"


def feature(name=None, lon=106.8271692, lat=-6.1754024, **props):
    # The shape of a real Photon answer, see docs/ti-se-12-geocoding.md.
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"osm_type": "W", "osm_id": 1, "type": "house", "country": "Indonesia", "countrycode": "ID",
                           **({"name": name} if name else {}), **props}}


MONAS = feature("Monumen Nasional", street="Jalan Medan Merdeka Utara", district="Gambir", city="Jakarta")
BANDUNG = feature("Bandung", lon=107.6098, lat=-6.9147, city="Bandung")


def collection(*features):
    return httpx.Response(200, json={"type": "FeatureCollection", "features": list(features)})


class Photon:
    """Answers in turn with `replies`, each a Response or an exception (the last one repeats),
    and records the requests."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def client_for(make_client):
    opened = []

    def make(*replies):
        client = make_client()
        opened.append(client.__enter__())  # runs the lifespan, which puts the settings on app.state
        photon = Photon(*replies)
        client.app.state.geocoder = PhotonGeocoder("http://photon.test/", 3.0, transport=httpx.MockTransport(photon))
        return client, photon
    yield make
    for client in opened:
        client.__exit__(None, None, None)


def test_search_returns_named_places_in_the_study_area(client_for):
    client, photon = client_for(collection(MONAS, feature(street="Jalan Gambir", housenumber="12", district="Menteng")))
    resp = client.post(SEARCH, json={"query": "  gambir "})
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    body = GeocodeSearchResponse.model_validate(resp.json())
    assert not body.outside_study_area
    assert [(p.name, p.description) for p in body.results] == [
        ("Monumen Nasional", "Jalan Medan Merdeka Utara, Gambir, Jakarta"), ("Jalan Gambir 12", "Menteng")]
    assert (body.results[0].lat, body.results[0].lon) == (-6.1754024, 106.8271692)

    sent = photon.requests[0]
    assert len(photon.requests) == 1  # a full match needs no second call
    assert sent.url.path == "/api" and sent.url.params["q"] == "gambir"
    assert sent.url.params["bbox"] == "106.68,-6.38,106.98,-6.08"
    assert sent.headers["user-agent"] == USER_AGENT


def test_search_drops_duplicates_unusable_features_and_anything_outside_the_area(client_for):
    client, _ = client_for(collection(MONAS, MONAS, BANDUNG, feature(), {"type": "Feature"}, "x",
                                      feature("Tanpa koordinat", lon="106"), feature("Ragunan", lon=106.82, lat=-6.3)))
    assert [p["name"] for p in client.post(SEARCH, json={"query": "nasional"}).json()["results"]] == ["Monumen Nasional", "Ragunan"]


def test_at_most_five_results(client_for):
    client, _ = client_for(collection(*[feature(f"Tempat {i}") for i in range(10)]))
    assert len(client.post(SEARCH, json={"query": "tempat"}).json()["results"]) == 5


def test_a_place_found_only_elsewhere_is_told_apart_from_a_place_not_found(client_for):
    client, photon = client_for(collection(), collection(BANDUNG), collection(), collection())
    assert client.post(SEARCH, json={"query": "bandung"}).json() == {"results": [], "outside_study_area": True}
    assert "bbox" not in photon.requests[1].url.params
    assert client.post(SEARCH, json={"query": "tidak ada"}).json() == {"results": [], "outside_study_area": False}


def test_a_failing_second_call_still_answers_with_what_the_search_found(client_for):
    client, _ = client_for(collection(), httpx.ConnectError("down"))
    assert client.post(SEARCH, json={"query": "bandung"}).json() == {"results": [], "outside_study_area": False}
    client, _ = client_for(collection(MONAS), httpx.ConnectError("down"))
    assert [p["name"] for p in client.post(SEARCH, json={"query": "monas"}).json()["results"]] == ["Monumen Nasional"]


def test_a_loose_match_elsewhere_is_not_a_place_outside_the_area(client_for):
    # As the real provider answers random text with whatever looks closest.
    client, _ = client_for(collection(), collection(feature("Ada Mart", lon=107.6, lat=-6.9)))
    assert client.post(SEARCH, json={"query": "qqzzxx tidak ada"}).json() == {"results": [], "outside_study_area": False}


def test_a_place_meant_elsewhere_replaces_similar_names_in_the_area(client_for):
    sate = feature("Gedung Sate", lon=107.6186, lat=-6.9025, city="Bandung")
    similar = feature("Gedung Dinar", district="Senen", city="Jakarta")
    client, _ = client_for(collection(similar), collection(sate))
    assert client.post(SEARCH, json={"query": "gedung sate bandung"}).json() == {"results": [], "outside_study_area": True}


def test_similar_names_are_kept_when_no_exact_place_exists_anywhere(client_for):
    # A typo: still useful suggestions.
    client, _ = client_for(collection(MONAS), collection(feature("Monaco", lon=7.4, lat=43.7)))
    body = client.post(SEARCH, json={"query": "monumen nasionl"}).json()
    assert [p["name"] for p in body["results"]] == ["Monumen Nasional"] and not body["outside_study_area"]


def test_full_matches_come_first_and_generic_street_words_are_ignored(client_for):
    client, _ = client_for(collection(feature("Hotel Sudirman Baru", lon=106.82, lat=-6.21), feature("Taman Sari"),
                                      feature("Toko", street="Jalan Jenderal Sudirman", lon=106.82, lat=-6.22)),)
    names = [p["name"] for p in client.post(SEARCH, json={"query": "jl sudirman"}).json()["results"]]
    assert names == ["Hotel Sudirman Baru", "Toko", "Taman Sari"]


def test_reverse_names_the_nearest_place(client_for):
    client, photon = client_for(collection(MONAS))
    resp = client.post(REVERSE, json={"lat": LAT, "lon": LON})
    assert resp.status_code == 200 and resp.headers["cache-control"] == "no-store"
    assert GeocodeReverseResponse.model_validate(resp.json()).place.name == "Monumen Nasional"
    params = photon.requests[0].url.params
    assert photon.requests[0].url.path == "/reverse" and (params["lat"], params["lon"]) == (str(LAT), str(LON))


@pytest.mark.parametrize("reply", [collection(), collection(feature())])
def test_reverse_without_a_named_place_answers_null(client_for, reply):
    client, _ = client_for(reply)
    assert client.post(REVERSE, json={"lat": LAT, "lon": LON}).json() == {"place": None}


def test_a_point_outside_the_area_never_reaches_the_provider(client_for):
    client, photon = client_for()
    error(client.post(REVERSE, json={"lat": -6.9147, "lon": 107.6098}), 422, "outside_study_area")
    assert not photon.requests


@pytest.mark.parametrize("path, body", [
    (SEARCH, {"query": "x"}), (SEARCH, {"query": "   "}), (SEARCH, {"query": "x" * 201}), (SEARCH, {}),
    (SEARCH, {"query": "monas", "extra": 1}), (REVERSE, {"lat": 91, "lon": 106.8}), (REVERSE, {"lat": -6.2}),
])
def test_invalid_bodies_are_refused_without_echoing_or_calling_the_provider(client_for, path, body):
    client, photon = client_for()
    assert "xxx" not in error(client.post(path, json=body), 400, "invalid_request")["message"]
    assert not photon.requests


@pytest.mark.parametrize("reply, reason", [
    (httpx.ReadTimeout("slow"), "provider_timeout"),
    (httpx.ConnectError("down"), "provider_error"),
    (httpx.Response(500, text="boom"), "provider_error"),
    (httpx.Response(429, text="throttled"), "provider_error"),
    (httpx.Response(200, text="<html>"), "provider_error"),
    (httpx.Response(200, json={"features": "no"}), "provider_error"),
    (httpx.Response(200, json=[]), "provider_error"),
])
@pytest.mark.parametrize("path, body", [(SEARCH, {"query": "monas"}), (REVERSE, {"lat": LAT, "lon": LON})])
def test_provider_failures_answer_503(client_for, path, body, reply, reason):
    client, _ = client_for(reply)
    assert error(client.post(path, json=body), 503, "geocoding_unavailable")["reason"] == reason


def test_without_a_url_nothing_is_sent_anywhere(make_client):
    with make_client() as client:
        assert client.app.state.geocoder is None
        for path, body in ((SEARCH, {"query": "monas"}), (REVERSE, {"lat": LAT, "lon": LON})):
            assert error(client.post(path, json=body), 503, "geocoding_unavailable")["reason"] == "not_configured"


def test_the_url_setting_makes_the_geocoder():
    geocoder = make_geocoder(ApiSettings(_env_file=None, geocoding_url="https://photon.example/", geocoding_timeout_s=2))
    assert (geocoder.base_url, geocoder.timeout_s) == ("https://photon.example", 2)


@pytest.mark.parametrize("replies", [
    (collection(MONAS), collection(MONAS)),
    (collection(), collection(), collection()),
    (httpx.ReadTimeout("slow"), httpx.ConnectError("down")),
    (httpx.Response(500, text="boom"), httpx.Response(500, text="boom")),
])
def test_search_text_and_coordinates_never_reach_the_log(client_for, caplog, replies):
    caplog.set_level(logging.DEBUG)
    client, _ = client_for(*replies)
    client.post(SEARCH, json={"query": MARKER})
    client.post(REVERSE, json={"lat": LAT, "lon": LON})
    client.post(SEARCH, json={"query": MARKER + "x" * 200})  # refused, and not echoed either
    text = "\n".join(f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert "http_request" in text  # the request log itself ran
    for secret in ("Zqxmarker", "zqxmarker", str(LAT), str(LON), "-6.177", "106.831", "photon.test"):
        assert secret not in text, secret


def test_httpx_does_not_log_the_url(client_for, caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = client_for(collection(MONAS))
    client.post(SEARCH, json={"query": MARKER})
    assert not [r for r in caplog.records if r.name.startswith(("httpx", "httpcore")) and r.levelno < logging.WARNING]
    assert json.dumps([r.getMessage() for r in caplog.records]).find("Zqxmarker") == -1
