"""Test collection for the public API, run against a deployed Cache API or API Gateway.

    python -m public_api.smoke --base-url http://localhost:8001         # Cache API (docker run -p 8001:8000)
    python -m public_api.smoke --base-url https://<gateway-domain>      # through the API Gateway
    python -m public_api.smoke --export-http                            # rewrite public_api/requests.http

Each case lists the statuses it accepts. Requests that read the pollution cache
also accept 503 cache_unavailable/cache_empty, because the Railway cache has no
complete time window yet; validation and study-area errors come before the cache
and must match exactly. 200 bodies are checked against the response schemas,
errors against the uniform error schema, and X-Request-ID must be echoed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

HTTP_FILE = pathlib.Path(__file__).with_name("requests.http")

MONAS = {"lat": -6.1754, "lon": 106.8272}
BUNDARAN_HI = {"lat": -6.1950, "lon": 106.8230}
CACHE_EMPTY = (503, "cache_unavailable")


def _route(**overrides) -> dict[str, Any]:
    return {"origin": MONAS, "destination": BUNDARAN_HI, "mode": "walk", "pollutant": "pm25",
            "preference": {"alpha": 0.5, "beta": 0.5}, **overrides}


@dataclass
class Case:
    name: str
    method: str
    path: str
    expect: list[tuple[int, str | None]]  # (status, error code or None for 200)
    body: Any = None
    raw: str | None = None                # sent as-is instead of body
    params: dict[str, Any] = field(default_factory=dict)
    in_http_file: bool = True


CASES = [
    Case("Rute jalan kaki Monas → Bundaran HI", "POST", "/v1/routes", [(200, None), CACHE_EMPTY], _route()),
    Case("Rute sepeda, polutan acuan NO2, utamakan paparan", "POST", "/v1/routes", [(200, None), CACHE_EMPTY],
         _route(mode="bike", pollutant="no2", preference={"alpha": 0.2, "beta": 0.8})),
    Case("α + β ≠ 1", "POST", "/v1/routes", [(400, "invalid_request")],
         _route(preference={"alpha": 0.6, "beta": 0.5})),
    Case("Moda tidak dikenal", "POST", "/v1/routes", [(400, "invalid_request")], _route(mode="car")),
    Case("Lintang di luar rentang", "POST", "/v1/routes", [(400, "invalid_request")],
         _route(origin={"lat": 106.8, "lon": -6.2})),
    Case("JSON rusak", "POST", "/v1/routes", [(400, "invalid_request")], raw='{"origin": '),
    Case("Body > 16 KiB", "POST", "/v1/routes", [(413, "payload_too_large")],
         _route(padding="x" * 20_000), in_http_file=False),
    Case("Tujuan di Kepulauan Seribu (luar wilayah studi)", "POST", "/v1/routes", [(422, "outside_study_area")],
         _route(destination={"lat": -5.75, "lon": 106.55})),
    Case("Pantai Ancol → laut (tidak dekat jalan)", "POST", "/v1/routes", [(422, "point_not_on_network")],
         _route(origin={"lat": -6.1225, "lon": 106.8420}, destination={"lat": -6.1000, "lon": 106.8420})),
    Case("Asal = tujuan", "POST", "/v1/routes", [(422, "no_route_found"), CACHE_EMPTY], _route(destination=MONAS)),
    Case("GET pada /v1/routes", "GET", "/v1/routes", [(405, "method_not_allowed")]),
    Case("Heatmap per ruas, sekitar Monas, zoom 15", "GET", "/v1/exposure-surface", [(200, None), CACHE_EMPTY],
         params={"bbox": "106.822,-6.180,106.832,-6.170", "zoom": 15}),
    Case("Heatmap grid 500 m, Jakarta Pusat, zoom 12", "GET", "/v1/exposure-surface", [(200, None), CACHE_EMPTY],
         params={"bbox": "106.79,-6.22,106.87,-6.15", "zoom": 12, "pollutant": "pm25"}),
    Case("Heatmap grid 1 km, seluruh Jakarta, zoom 11", "GET", "/v1/exposure-surface", [(200, None), CACHE_EMPTY],
         params={"bbox": "106.68,-6.38,106.98,-6.08", "zoom": 11}),
    Case("Seluruh Jakarta pada zoom 15 (terlalu luas)", "GET", "/v1/exposure-surface", [(400, "invalid_request")],
         params={"bbox": "106.68,-6.38,106.98,-6.08", "zoom": 15}),
    Case("Zoom di luar 0–22", "GET", "/v1/exposure-surface", [(400, "invalid_request")],
         params={"bbox": "106.822,-6.180,106.832,-6.170", "zoom": 23}),
    Case("bbox hanya 3 angka", "GET", "/v1/exposure-surface", [(400, "invalid_request")],
         params={"bbox": "106.82,-6.18,106.83", "zoom": 15}),
]


def check(case: Case, resp) -> str | None:
    """None when the response satisfies the case, else what is wrong."""
    from public_api.schemas import ErrorResponse, RouteFeatureCollection, SurfaceFeatureCollection

    accepted = {status for status, _ in case.expect}
    if resp.status_code not in accepted:
        return f"HTTP {resp.status_code}, harap {sorted(accepted)}: {resp.text[:200]}"
    # The Cache API echoes the X-Request-ID it receives; the API Gateway replaces it
    # with its own. Either way the response carries one ID, the same as in the body.
    ids = resp.headers.get_list("x-request-id")
    if len(ids) != 1:
        return f"harap tepat satu header X-Request-ID, ada {len(ids)}"
    body_id = (resp.json().get("metadata", {}) if resp.status_code == 200 else resp.json().get("error", {})).get("request_id")
    if body_id != ids[0]:
        return f"request_id body {body_id!r} ≠ header X-Request-ID {ids[0]!r}"
    if resp.status_code == 200:
        if resp.headers.get("content-type") != "application/geo+json":
            return f"content-type {resp.headers.get('content-type')}"
        model = RouteFeatureCollection if case.path == "/v1/routes" else SurfaceFeatureCollection
        try:
            model.model_validate(resp.json())
        except ValueError as exc:
            return f"respons tidak sesuai skema: {str(exc)[:300]}"
        return None
    try:
        error = ErrorResponse.model_validate(resp.json()).error
    except ValueError as exc:
        return f"galat tidak sesuai skema seragam: {str(exc)[:300]}"
    codes = {code for status, code in case.expect if status == resp.status_code}
    if error.code not in codes:
        return f"kode {error.code}, harap {sorted(codes)}"
    if (resp.status_code, error.code) == CACHE_EMPTY and error.reason != "cache_empty":
        return f"503 dengan reason {error.reason}, hanya cache_empty yang diterima"
    return None


def send(client, case: Case):
    headers = {"X-Request-ID": f"smoke-{uuid.uuid4().hex[:12]}"}
    if case.raw is not None:
        headers["Content-Type"] = "application/json"
    return client.request(case.method, case.path, params=case.params or None, headers=headers,
                          json=case.body if case.raw is None else None,
                          content=case.raw.encode() if case.raw is not None else None)


def run(client, cases: list[Case] = CASES) -> int:
    """Send every case with client (an httpx.Client or a FastAPI TestClient); returns the failures."""
    failures = 0
    for case in cases:
        resp = send(client, case)
        problem = check(case, resp)
        if problem is None:
            outcome = "200" if resp.status_code == 200 else f"{resp.status_code} {resp.json()['error']['code']}"
            print(f"PASS  {case.name} → {outcome} ({resp.elapsed.total_seconds():.2f} s)")
        else:
            failures += 1
            print(f"FAIL  {case.name}: {problem}")
    print(f"{len(cases) - failures}/{len(cases)} lulus")
    return failures


def export_http() -> str:
    """The cases as a .http file (VS Code REST Client, JetBrains HTTP Client)."""
    lines = ["# Koleksi uji API publik SIPPolusi (TI-SE-08). Dibuat dari public_api/smoke.py:",
             "#   python -m public_api.smoke --export-http",
             "# Ganti @host dengan Cache API lokal (docker run -p 8001:8000) atau domain API Gateway.",
             "@host = http://localhost:8001", ""]
    for case in CASES:
        if not case.in_http_file:
            continue
        expect = ", ".join(f"{s} {c}" if c else str(s) for s, c in case.expect)
        query = "&".join(f"{k}={v}" for k, v in case.params.items())
        lines += [f"### {case.name}", f"# Harapan: {expect}",
                  f"{case.method} {{{{host}}}}{case.path}{'?' + query if query else ''}"]
        if case.body is not None or case.raw is not None:
            lines += ["Content-Type: application/json", "",
                      case.raw if case.raw is not None else json.dumps(case.body, ensure_ascii=False, indent=2)]
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", help="mis. http://localhost:8001")
    parser.add_argument("--export-http", action="store_true", help=f"tulis {HTTP_FILE.name}")
    args = parser.parse_args()
    if args.export_http:
        HTTP_FILE.write_text(export_http(), encoding="utf-8")
        print(f"{HTTP_FILE} diperbarui")
        return
    if not args.base_url:
        parser.error("--base-url atau --export-http wajib")
    import httpx

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=15.0) as client:
        sys.exit(1 if run(client) else 0)


if __name__ == "__main__":
    main()
