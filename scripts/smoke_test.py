"""Runtime smoke test for the gateway, backend and Cache API (TI-DO-02, TI-DO-05).

Standard library only, so it runs anywhere Python 3.10+ does:

    python scripts/smoke_test.py --gateway http://localhost:8081 --backend http://localhost:8000
    python scripts/smoke_test.py --gateway https://<gateway>.up.railway.app

--backend is optional: once the backend is private-only (TI-DO-04) it is
checked through the gateway alone. Prints one line per check and exits 1 if
any check fails. The Data Worker has no HTTP surface; check it with
`python main.py check` (see docs/devops/build-runtime.md).

The gateway exposes only the public API (POST /v1/routes, GET
/v1/exposure-surface, both on the Cache API); internal paths must get the
gateway's own 404. The proxied checks verify that the Cache API answered, not
that it had data: a fresh environment without an imported road graph still
passes as long as the Cache API itself replies (e.g. 503 cache_unavailable).
An error the gateway produces itself (404, 429, 502, 504) on a public path
fails. Four requests reach the public API, well under its rate limits.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request

# RouteRequest (design Kode 3.1, src/backend/public_api/README.md): Monas to Bundaran HI.
ROUTE_REQUEST = {
    "origin": {"lat": -6.1754, "lon": 106.8272},
    "destination": {"lat": -6.1950, "lon": 106.8230},
    "mode": "walk",
    "pollutant": "pm25",
    "preference": {"alpha": 0.5, "beta": 0.5},
}
SURFACE_QUERY = "bbox=106.822,-6.180,106.832,-6.170&zoom=15"

# Error codes the gateway produces itself (src/gateway/README.md); on a public
# path they mean the request never got a real answer from the Cache API.
GATEWAY_ERRORS = {"not_found", "rate_limited", "upstream_unavailable", "upstream_timeout"}


def answered_by_cache_api(status: int, headers, payload) -> bool:
    """A GeoJSON FeatureCollection, or an error in the uniform schema that the Cache API produced."""
    if not isinstance(payload, dict) or not headers.get("X-Request-ID"):
        return False
    if status == 200:
        return payload.get("type") == "FeatureCollection"
    error = payload.get("error")
    return isinstance(error, dict) and "code" in error and error["code"] not in GATEWAY_ERRORS


def closed_by_gateway(status: int, headers, payload) -> bool:
    error = payload.get("error") if isinstance(payload, dict) else None
    return status == 404 and isinstance(error, dict) and error.get("code") == "not_found"


def request(method: str, url: str, body: dict | None = None, timeout: float = 15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, headers, raw = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        status, headers, raw = exc.code, exc.headers, exc.read()
    elapsed_ms = (time.monotonic() - started) * 1000
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = raw.decode(errors="replace")[:200]
    return status, headers, payload, elapsed_ms


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gateway", required=True, help="gateway base URL, e.g. http://localhost:8081")
    parser.add_argument("--backend", help="backend base URL, if it is reachable directly")
    args = parser.parse_args(argv)
    gateway = args.gateway.rstrip("/")
    backend = args.backend.rstrip("/") if args.backend else None

    checks = [
        ("gateway liveness", "GET", f"{gateway}/healthz", None,
         lambda s, h, p: s == 200 and isinstance(p, dict) and p.get("status") == "ok"),
        ("route request via gateway", "POST", f"{gateway}/v1/routes", ROUTE_REQUEST, answered_by_cache_api),
        ("heatmap via gateway", "GET", f"{gateway}/v1/exposure-surface?{SURFACE_QUERY}", None,
         answered_by_cache_api),
        ("Cache API internals closed", "GET", f"{gateway}/v1/osm/version", None, closed_by_gateway),
        ("route inspection closed", "POST", f"{gateway}/v1/routes/solve", ROUTE_REQUEST, closed_by_gateway),
        ("health endpoints closed", "GET", f"{gateway}/health", None, closed_by_gateway),
    ]
    if backend:
        checks += [
            ("backend liveness", "GET", f"{backend}/health", None,
             lambda s, h, p: s == 200 and isinstance(p, dict) and p.get("status") == "ok"),
            ("backend readiness (database)", "GET", f"{backend}/health/ready", None,
             lambda s, h, p: s == 200 and isinstance(p, dict) and p.get("status") == "ready"),
        ]

    print(f"smoke test {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}")
    print(f"  gateway: {gateway}")
    print(f"  backend: {backend or '(not checked directly)'}")
    failed = 0
    for name, method, url, body, ok in checks:
        try:
            status, headers, payload, elapsed_ms = request(method, url, body)
            passed = bool(ok(status, headers, payload))
            detail = f"HTTP {status} {elapsed_ms:.0f} ms {json.dumps(payload)[:160]}"
            if headers.get("X-Request-ID"):
                detail += f" request_id={headers['X-Request-ID']}"
        except Exception as exc:  # noqa: BLE001 - reported as a failed check
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        failed += not passed
        print(f"{'PASS' if passed else 'FAIL'}  {name:<30} {method} {url}\n      {detail}")
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
