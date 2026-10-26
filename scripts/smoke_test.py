"""Runtime smoke test for the gateway, backend and Cache API (TI-DO-02).

Standard library only, so it runs anywhere Python 3.10+ does:

    python scripts/smoke_test.py --gateway http://localhost:8081 --backend http://localhost:8000
    python scripts/smoke_test.py --gateway https://<gateway>.up.railway.app

--backend is optional: once the backend is private-only (TI-DO-04) it is
checked through the gateway alone. Prints one line per check and exits 1 if
any check fails. The Data Worker has no HTTP surface; check it with
`python main.py check` (see docs/devops/build-runtime.md).

The proxied checks verify that the right service answered, not that it had
data: a fresh environment without an imported road graph still passes as
long as the backend or Cache API itself replies (e.g. 503 data_unavailable).
A 502/504 from the gateway or a request routed to the wrong service fails.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request

ROUTE_REQUEST = {
    "origin": {"lon": 106.8227, "lat": -6.1754},
    "destination": {"lon": 106.8456, "lat": -6.2088},
    "mode": "walk",
    "alpha": 0.5,
    "beta": 0.5,
}

# HTTP codes the backend's /v1/routes uses (main.route_response); every one of
# its bodies carries a route "status" such as ok, no_route or data_unavailable.
ROUTE_HTTP_CODES = {200, 404, 422, 503}


def answered_by_backend(status: int, headers, payload) -> bool:
    return (status in ROUTE_HTTP_CODES and isinstance(payload, dict) and "status" in payload
            and bool(headers.get("X-Request-ID")))


def answered_by_cache_api(status: int, headers, payload) -> bool:
    """200 with the active graph version, or the Cache API's own 503 when its database is not ready."""
    if not isinstance(payload, dict):
        return False
    if status == 200:
        return "version" in payload
    error = payload.get("error")
    return status == 503 and isinstance(error, dict) and error.get("code") == "cache_unavailable"


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
        ("route request via gateway", "POST", f"{gateway}/v1/routes", ROUTE_REQUEST, answered_by_backend),
        ("cache API via gateway", "GET", f"{gateway}/v1/osm/version", None, answered_by_cache_api),
        ("unknown path not proxied", "GET", f"{gateway}/health", None,
         lambda s, h, p: s == 404 and isinstance(p, dict) and "error" in p),
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
