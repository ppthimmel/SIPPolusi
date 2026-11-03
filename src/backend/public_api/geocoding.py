"""Place search and reverse geocoding for the trip form (TI-SE-12), through a Photon server.

The provider and its usage policy are documented in docs/ti-se-12-geocoding.md. The base
URL comes from API_GEOCODING_URL; without it nothing leaves this service and both
endpoints answer 503 geocoding_unavailable, while the map and typed coordinates keep working.

Search text and coordinates are location data (PNF-06, RI-09), so:
- the public endpoints are POST: the text stays in the body, out of the request line that
  access logs, proxies and error logs record;
- nothing here logs them or keeps them after the response, and errors carry fixed text;
- the provider gets only the query and the study area, never the user's address.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any

import httpx

from osm.errors import OsmApiError
from osm.models import BBox

from .config import ApiSettings

# httpx logs every request URL at INFO, and for this client the URL holds the search text.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

USER_AGENT = "SIPPolusi/1.0 (+https://github.com/ppthimmel/SIPPolusi)"
MAX_RESULTS = 5
# Asked of the provider. Its order favours name similarity, so the place people mean can sit far
# down the list ("monas": three bus stops and five parks before the monument).
SEARCH_LIMIT = 50
# Enough to see whether a place exists outside the area.
ELSEWHERE_LIMIT = 10
# Connections stay open between the keystroke batches of one search, to spare a TLS handshake each.
KEEPALIVE_S = 30.0
# How far a clicked point may be from the place named for it, in kilometres.
REVERSE_RADIUS_KM = 0.5
# Words that say nothing about which place is meant, often abbreviated differently from the map.
GENERIC_WORDS = {"jl", "jln", "jalan"}
# Words that ask for a transit stop, which are otherwise ranked low.
TRANSIT_WORDS = {"halte", "busway", "transjakarta"}
MATCHED_FIELDS = ("name", "street", "housenumber", "locality", "district", "city", "county", "state")
# Stations are often mapped under the bare name: "Manggarai", not "Stasiun Manggarai".
IMPLIED_WORDS = {("railway", "station"): "stasiun", ("building", "train_station"): "stasiun"}

# OpenStreetMap tags: places people head for, and those that crowd out the former under the same
# name. Minor places are ranked low rather than removed, so a stop can still be found by name.
LANDMARK_KEYS = {"historic", "tourism", "aeroway", "place"}
LANDMARK_KINDS = {("railway", "station"), ("building", "train_station"), ("shop", "mall"), ("leisure", "stadium"),
                  ("amenity", "hospital"), ("amenity", "university"), ("amenity", "college")}
MINOR_KEYS = {"public_transport", "waterway", "natural", "boundary", "barrier", "information"}
MINOR_KINDS = {("highway", "bus_stop"), ("highway", "platform"), ("highway", "motorway_junction"),
               ("amenity", "bus_station"), ("amenity", "parking"), ("amenity", "motorcycle_parking"),
               ("building", "transportation"), ("railway", "stop"), ("railway", "platform"), ("railway", "proposed"),
               ("landuse", "construction")}


class GeocodingUnavailable(OsmApiError):
    """503 geocoding_unavailable: the route form still works with the map and coordinates."""

    status_code = 503
    code = "geocoding_unavailable"
    NOT_CONFIGURED = "not_configured"
    TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _place(feature: Any, study_area: BBox) -> dict[str, Any] | None:
    """{name, description, lat, lon} of one GeoJSON feature; None when unnamed or outside the area."""
    if not isinstance(feature, dict):
        return None
    props, geometry = feature.get("properties"), feature.get("geometry")
    if not isinstance(props, dict) or not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) != 2:
        return None
    lon, lat = coordinates
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lon, lat)):
        return None
    if not (study_area.min_lon <= lon <= study_area.max_lon and study_area.min_lat <= lat <= study_area.max_lat):
        return None

    street = " ".join(str(props[k]) for k in ("street", "housenumber") if props.get(k))
    name = props.get("name") or street or props.get("district") or props.get("locality")
    if not name:
        return None
    parts = [street, props.get("district"), props.get("city")]
    description = ", ".join(dict.fromkeys(str(p) for p in parts if p and p != name))
    return {"name": str(name), "description": description, "lat": lat, "lon": lon}


def _matches(feature: Any, query: str) -> bool:
    """Whether every word of the query starts a word of the feature, so a word still being typed
    counts. Photon matches loosely, so its results alone cannot tell the place that was meant from
    one that merely looks similar."""
    props = feature.get("properties") if isinstance(feature, dict) else None
    if not isinstance(props, dict):
        return False
    kind = (props.get("osm_key"), props.get("osm_value"))
    words = _words(" ".join([*(str(props[k]) for k in MATCHED_FIELDS if props.get(k)), IMPLIED_WORDS.get(kind, "")]))
    return all(any(w.startswith(word) for w in words) for word in _words(query) if word not in GENERIC_WORDS)


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower())


def _rank(feature: Any, query: str) -> int:
    """Lower comes first: landmarks, then other places, then minor ones, each with full matches first.
    A landmark without every word stays above other places: the monument is what "monas" means.
    A query naming a street ("jalan sudirman") puts streets above everything."""
    props = feature.get("properties") if isinstance(feature, dict) else None
    if not isinstance(props, dict):
        return 6
    key, words = props.get("osm_key"), set(_words(query))
    kind = (key, props.get("osm_value"))
    if words & GENERIC_WORDS and props.get("type") == "street":
        tier = -1
    elif key in LANDMARK_KEYS or kind in LANDMARK_KINDS:
        tier = 0
    elif (key in MINOR_KEYS or kind in MINOR_KINDS) and not words & TRANSIT_WORDS:
        tier = 2
    else:
        tier = 1
    return tier * 2 + (not _matches(feature, query))


def _in_area(feature: Any, study_area: BBox) -> bool:
    return _place(feature, study_area) is not None


class PhotonGeocoder:
    """The Photon API; the public server and a self-hosted one differ only in the base URL."""

    def __init__(self, base_url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._transport = transport  # tests inject httpx.MockTransport
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        # Made on first use, inside the event loop that serves the requests.
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s, transport=self._transport,
                                             headers={"User-Agent": USER_AGENT},
                                             limits=httpx.Limits(keepalive_expiry=KEEPALIVE_S))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _features(self, path: str, params: dict[str, Any]) -> list[Any]:
        # `from None`: the exceptions of httpx name the URL, which holds the search text.
        try:
            resp = await self._http().get(path, params=params)
        except httpx.TimeoutException:
            raise GeocodingUnavailable("Layanan pencarian lokasi tidak merespons tepat waktu.",
                                       GeocodingUnavailable.TIMEOUT) from None
        except httpx.HTTPError:
            raise GeocodingUnavailable("Layanan pencarian lokasi tidak dapat dihubungi.",
                                       GeocodingUnavailable.PROVIDER_ERROR) from None
        failed = GeocodingUnavailable("Layanan pencarian lokasi sedang bermasalah.", GeocodingUnavailable.PROVIDER_ERROR)
        if resp.status_code != 200:
            raise failed
        try:
            features = resp.json()["features"]
        except (ValueError, KeyError, TypeError):
            raise failed from None
        if not isinstance(features, list):
            raise failed
        return features

    async def search(self, query: str, study_area: BBox) -> dict[str, Any]:
        """Places in the study area, best first (_rank). When none holds every word of the query,
        one more call tells whether the place was meant to be elsewhere: then the similar names
        in the area are not what the user wants, and they get the right message instead."""
        features = await self._features("/api", {
            "q": query, "limit": SEARCH_LIMIT,
            "bbox": f"{study_area.min_lon},{study_area.min_lat},{study_area.max_lon},{study_area.max_lat}",
        })
        full_match = any(_matches(f, query) and _in_area(f, study_area) for f in features)
        if full_match:  # the loose matches of other kinds are noise beside it ("kota tua": a school in Bekasi)
            features = [f for f in features if _matches(f, query) or _rank(f, query) <= 1]
        features.sort(key=lambda f: _rank(f, query))  # stable: otherwise the provider's order
        # One entry per name and area: a station and its platforms, or a road split into segments.
        unique: dict[tuple[Any, ...], dict[str, Any]] = {}
        for feature in features:
            place = _place(feature, study_area)
            if place:
                props = feature["properties"]
                unique.setdefault((place["name"].lower(), props.get("district"), props.get("city")), place)
        results = list(unique.values())[:MAX_RESULTS]
        if full_match:
            return {"results": results, "outside_study_area": False}
        try:
            elsewhere = await self._features("/api", {"q": query, "limit": ELSEWHERE_LIMIT})
        except GeocodingUnavailable:
            elsewhere = []  # the search itself worked, so its results still stand
        if any(_matches(f, query) and not _in_area(f, study_area) for f in elsewhere):
            return {"results": [], "outside_study_area": True}
        return {"results": results, "outside_study_area": False}

    async def reverse(self, lat: float, lon: float, study_area: BBox) -> dict[str, Any] | None:
        features = await self._features("/reverse", {"lat": lat, "lon": lon, "limit": 1, "radius": REVERSE_RADIUS_KM})
        return next(filter(None, (_place(f, study_area) for f in features)), None)


def make_geocoder(settings: ApiSettings) -> PhotonGeocoder | None:
    return PhotonGeocoder(settings.geocoding_url, settings.geocoding_timeout_s) if settings.geocoding_url else None


def require_geocoder(geocoder: PhotonGeocoder | None) -> PhotonGeocoder:
    if geocoder is None:
        raise GeocodingUnavailable("Pencarian lokasi belum dikonfigurasi di server.", GeocodingUnavailable.NOT_CONFIGURED)
    return geocoder
