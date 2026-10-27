"""Katalog STAC Microsoft Planetary Computer: pencarian item dan token SAS anonim.

Dipakai konektor Landsat 8/9 dan Sentinel-5P, sumber yang sama dengan data latih
TI-AI-01. Token SAS adalah kredensial jangka pendek: hanya ditempel pada URL
aset, tidak pernah dicatat (``safe_url``).
"""

from __future__ import annotations

import datetime as dt

from .http import FormatError, HttpClient

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
SAS_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token"
#: Kotak wilayah studi data latih (106,6-107,0 BT, 6,4-6,0 LS); memuat ``JAKARTA_BBOX``.
STUDY_BBOX = [106.6, -6.4, 107.0, -6.0]
MAX_PAGES = 10


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def search(client: HttpClient, collection: str, start: dt.datetime, end: dt.datetime,
           query: dict | None = None) -> list[dict]:
    """Seluruh item ``collection`` yang memotong wilayah studi pada [start, end], terlama dulu."""
    body = {"collections": [collection], "bbox": STUDY_BBOX, "datetime": f"{_iso(start)}/{_iso(end)}",
            "limit": 100}
    if query:
        body["query"] = query
    url, method, items = f"{STAC_URL}/search", "POST", []
    for _ in range(MAX_PAGES):
        response = client.get(url) if method == "GET" else client.request("POST", url, json=body)
        try:
            page = response.json()
        except ValueError as exc:
            raise FormatError(f"{client.source}: respons STAC bukan JSON") from exc
        if not isinstance(page, dict) or not isinstance(page.get("features"), list):
            raise FormatError(f"{client.source}: respons STAC tanpa 'features'")
        items.extend(page["features"])
        nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
        if not nxt:
            break
        url, method, body = nxt["href"], nxt.get("method", "GET"), nxt.get("body", body)
    for item in items:
        if "id" not in item or "datetime" not in item.get("properties", {}):
            raise FormatError(f"{client.source}: item STAC tanpa id atau datetime")
    return sorted(items, key=lambda x: x["properties"]["datetime"])


def sas_token(client: HttpClient, collection: str) -> str:
    payload = client.get_json(f"{SAS_URL}/{collection}")
    token = payload.get("token") if isinstance(payload, dict) else None
    if not token:
        raise FormatError(f"{client.source}: respons token SAS tanpa 'token'")
    return token


def signed(href: str, token: str) -> str:
    return href + ("&" if "?" in href else "?") + token
