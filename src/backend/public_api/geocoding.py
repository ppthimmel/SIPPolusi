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
# Asked of the provider: duplicates and unnamed places are dropped afterwards.
PROVIDER_LIMIT = 10
# How far a clicked point may be from the place named for it, in kilometres.
REVERSE_RADIUS_KM = 0.5
# Words that say nothing about which place is meant, often abbreviated differently from the map.
GENERIC_WORDS = {"jl", "jln", "jalan"}
MATCHED_FIELDS = ("name", "street", "housenumber", "locality", "district", "city", "county", "state")


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
    """Whether the feature holds every word of the query. Photon matches loosely, so its results
    alone cannot tell the place that was meant from one that merely looks similar."""
    props = feature.get("properties") if isinstance(feature, dict) else None
    if not isinstance(props, dict):
        return False
    text = " ".join(str(props[k]) for k in MATCHED_FIELDS if props.get(k)).lower()
    return all(word in text for word in re.findall(r"[^\W_]+", query.lower()) if word not in GENERIC_WORDS)


def _in_area(feature: Any, study_area: BBox) -> bool:
    return _place(feature, study_area) is not None


class PhotonGeocoder:
    """The Photon API; the public server and a self-hosted one differ only in the base URL."""

    def __init__(self, base_url: str, timeout_s: float, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._transport = transport  # tests inject httpx.MockTransport

    async def _features(self, path: str, params: dict[str, Any]) -> list[Any]:
        # `from None`: the exceptions of httpx name the URL, which holds the search text.
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s, transport=self._transport,
                                         headers={"User-Agent": USER_AGENT}) as client:
                resp = await client.get(path, params=params)
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
        """Places in the study area, those holding every word of the query first. When none does,
        one more call tells whether the place was meant to be elsewhere: then the similar names
        in the area are not what the user wants, and they get the right message instead."""
        features = await self._features("/api", {
            "q": query, "limit": PROVIDER_LIMIT,
            "bbox": f"{study_area.min_lon},{study_area.min_lat},{study_area.max_lon},{study_area.max_lat}",
        })
        features.sort(key=lambda f: not _matches(f, query))  # stable: otherwise the provider's order
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for feature in features:
            place = _place(feature, study_area)
            if place:
                unique.setdefault((place["name"], place["description"]), place)
        results = list(unique.values())[:MAX_RESULTS]
        if any(_matches(f, query) and _in_area(f, study_area) for f in features):
            return {"results": results, "outside_study_area": False}
        try:
            elsewhere = await self._features("/api", {"q": query, "limit": PROVIDER_LIMIT})
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
