"""Private-networking test: which services answer, and from where (TI-DO-04).

Standard library only. Each target is expected either to answer (--open) or
to be unreachable (--closed) from the place the script runs:

    # From outside (laptop, CI): only the gateway may answer.
    python scripts/network_test.py \
        --open https://<gateway>.up.railway.app/healthz \
        --closed https://<old-backend-domain>.up.railway.app/health \
        --closed tcp://backend.railway.internal:8000

    # From inside the private network, e.g. in the backend container
    # (the script is read from stdin, so it need not be in the image):
    docker compose exec -T backend python - --open tcp://postgres:5432 < scripts/network_test.py

Targets are tcp://HOST:PORT (DNS + TCP connect) or http(s)://HOST[:PORT]/PATH
(DNS + HTTP request). Every check prints the resolved addresses, so the
output also shows that internal names (*.railway.internal, compose service
names) resolve inside the network and not outside it.

A target counts as unreachable when its name does not resolve, the
connection is refused or times out, or Railway's edge answers for a domain
that no longer routes to a service (404 with X-Railway-Fallback). Any other
HTTP response, whatever its status, means the service is reachable.
Exits 1 if any check fails.
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


class Unreachable(Exception):
    """The target could not be reached; the message says at which step."""


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Unreachable(f"DNS: {host} not resolvable ({exc.strerror})") from None
    return sorted({info[4][0] for info in infos})


def probe_tcp(host: str, port: int, timeout: float) -> str:
    addresses = resolve(host, port)
    errors = []
    for address in addresses:
        try:
            with socket.create_connection((address, port), timeout=timeout):
                return f"DNS {', '.join(addresses)}; TCP connect to [{address}]:{port} ok"
        except OSError as exc:
            errors.append(f"[{address}]:{port} {exc.strerror or type(exc).__name__}")
    raise Unreachable(f"DNS {', '.join(addresses)}; TCP: {'; '.join(errors)}")


def probe_http(url: str, timeout: float) -> str:
    parts = urlsplit(url)
    addresses = resolve(parts.hostname, parts.port or DEFAULT_PORTS[parts.scheme])
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status, headers, body = resp.status, resp.headers, resp.read(200)
    except urllib.error.HTTPError as exc:
        status, headers, body = exc.code, exc.headers, exc.read(200)
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise Unreachable(f"DNS {', '.join(addresses)}; HTTP: {reason}") from None
    elapsed_ms = (time.monotonic() - started) * 1000
    text = body.decode(errors="replace").replace("\n", " ")[:120]
    if status == 404 and headers.get("X-Railway-Fallback", "").lower() == "true":
        raise Unreachable(f"DNS {', '.join(addresses)}; Railway edge: domain not routed to a service (404 {text})")
    return f"DNS {', '.join(addresses)}; HTTP {status} {elapsed_ms:.0f} ms {text}"


def probe(target: str, timeout: float) -> str:
    parts = urlsplit(target)
    if parts.scheme == "tcp" and parts.hostname and parts.port:
        return probe_tcp(parts.hostname, parts.port, timeout)
    if parts.scheme in DEFAULT_PORTS and parts.hostname:
        return probe_http(target, timeout)
    raise ValueError(f"unsupported target {target!r}; use tcp://HOST:PORT or http(s)://HOST[:PORT]/PATH")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--open", action="append", default=[], metavar="TARGET", help="must be reachable")
    parser.add_argument("--closed", action="append", default=[], metavar="TARGET", help="must be unreachable")
    parser.add_argument("--timeout", type=float, default=5, help="seconds per connection (default 5)")
    args = parser.parse_args(argv)
    if not args.open and not args.closed:
        parser.error("give at least one --open or --closed target")

    print(f"network test {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} from {socket.gethostname()}")
    checks = [(target, True) for target in args.open] + [(target, False) for target in args.closed]
    failed = 0
    for target, want_open in checks:
        try:
            detail, is_open = probe(target, args.timeout), True
        except Unreachable as exc:
            detail, is_open = str(exc), False
        except ValueError as exc:
            detail, is_open = str(exc), None
        passed = is_open is want_open
        failed += not passed
        expected = "reachable" if want_open else "unreachable"
        print(f"{'PASS' if passed else 'FAIL'}  {expected:<11} {target}\n      {detail}")
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
