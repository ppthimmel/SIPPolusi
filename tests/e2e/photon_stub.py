"""Geocoding provider stand-in for the E2E stack: the Photon API with fixed places.

Answers as the real server does: GET /api?q=&bbox=&limit= and GET /reverse?lat=&lon=.
"monas" is in the study area, "bandung" only outside it, anything else is not found.
Standard library only; runs in python:3.11-slim. Requests are not logged, as a provider's
own logs are outside what E2E-12 checks.
"""
from __future__ import annotations

import http.server
import json
import urllib.parse


def place(name: str, lon: float, lat: float, **props) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"name": name, "country": "Indonesia", "countrycode": "ID", **props}}


MONAS = place("Monumen Nasional", 106.8271692, -6.1754024, street="Jalan Medan Merdeka Utara", district="Gambir",
              city="Jakarta")
BANDUNG = place("Bandung", 107.6098, -6.9147, city="Bandung")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        text = query.get("q", [""])[0].lower()
        if url.path == "/reverse":
            features = [MONAS]
        elif "monas" in text:
            features = [MONAS]
        elif "bandung" in text and "bbox" not in query:
            features = [BANDUNG]
        else:
            features = []
        data = json.dumps({"type": "FeatureCollection", "features": features}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


http.server.ThreadingHTTPServer(("0.0.0.0", 2322), Handler).serve_forever()
