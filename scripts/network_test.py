"""Private-networking test: which services answer, and from where (TI-DO-04).

Standard library only. Each target is expected to answer (--open), to be
unreachable for any reason (--closed), or to resolve and then be refused
(--refused) from the place the script runs:

    # From outside (laptop, CI): only the gateway may answer.
    python scripts/network_test.py \
        --open https://<gateway>.up.railway.app/healthz \
        --refused https://<old-backend-domain>.up.railway.app/health \
        --closed tcp://backend.railway.internal:8000

    # From inside the private network, e.g. in the backend container
    # (the script is read from stdin, so it need not be in the image):
    docker compose exec -T backend python - --open tcp://postgres:5432 < scripts/network_test.py

Targets are tcp://HOST:PORT (DNS + TCP connect), postgres://HOST:PORT (DNS +
PostgreSQL SSLRequest, no credentials; use it for Railway TCP proxies) or
http(s)://HOST[:PORT]/PATH (DNS + HTTP request). Every check prints the resolved addresses, so the
output also shows that internal names (*.railway.internal, compose service
names) resolve inside the network and not outside it.

A target counts as unreachable when its name does not resolve, the
connection is refused or times out, or Railway's edge answers for a domain
that no longer routes to a service (404 with X-Railway-Fallback). Any other
HTTP response, whatever its status, means the service is reachable.

--closed also passes when the name does not resolve, so a typo in a host name
passes too. Use --refused for names that exist (public domains, TCP proxy
hosts, localhost ports): it passes only when DNS answers and the connection
is then refused, reset, timed out or answered by Railway's fallback. Keep
--closed for names that must not resolve, such as *.railway.internal from
outside. HTTP(S)_PROXY and similar variables are ignored, so every check
connects directly.

Exits 1 if any check fails.
"""
from __future__ import annotations

import argparse
import datetime as dt
import socket
import struct
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


class Unreachable(Exception):
    """The target could not be reached; the message says at which step.

    resolved is False when DNS failed, True when the name resolved and the
    connection or the service refused.
    """

    def __init__(self, message: str, resolved: bool = True):
        super().__init__(message)
        self.resolved = resolved


# Direct connections only: a proxy from the environment would answer instead
# of the target and make closed paths look open (or the reverse).
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def resolve(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise Unreachable(f"DNS: {host} not resolvable ({exc.strerror})", resolved=False) from None
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


def probe_postgres(host: str, port: int, timeout: float) -> str:
    """TCP connect plus a PostgreSQL SSLRequest (no credentials sent).

    Railway's TCP proxy edge accepts connections even after the proxy is
    removed, so a bare TCP check cannot tell; only an answering server can.
    """
    addresses = resolve(host, port)
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(struct.pack("!ii", 8, 80877103))
            answer = sock.recv(1)
    except OSError as exc:
        raise Unreachable(f"DNS {', '.join(addresses)}; PostgreSQL: {exc.strerror or type(exc).__name__}") from None
    if answer not in (b"S", b"N"):
        raise Unreachable(f"DNS {', '.join(addresses)}; TCP accepted but no PostgreSQL answered ({answer!r})")
    return f"DNS {', '.join(addresses)}; PostgreSQL answered SSLRequest with {answer.decode()}"


def probe_http(url: str, timeout: float) -> str:
    parts = urlsplit(url)
    addresses = resolve(parts.hostname, parts.port or DEFAULT_PORTS[parts.scheme])
    started = time.monotonic()
    try:
        with _OPENER.open(url, timeout=timeout) as resp:
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
    if parts.scheme == "postgres" and parts.hostname:
        return probe_postgres(parts.hostname, parts.port or 5432, timeout)
    if parts.scheme in DEFAULT_PORTS and parts.hostname:
        return probe_http(target, timeout)
    raise ValueError(f"unsupported target {target!r}; use tcp://HOST:PORT, postgres://HOST:PORT "
                     "or http(s)://HOST[:PORT]/PATH")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--open", action="append", default=[], metavar="TARGET", help="must be reachable")
    parser.add_argument("--closed", action="append", default=[], metavar="TARGET",
                        help="must be unreachable, including a name that does not resolve")
    parser.add_argument("--refused", action="append", default=[], metavar="TARGET",
                        help="must resolve, then be refused (catches typos that --closed would pass)")
    parser.add_argument("--timeout", type=float, default=5, help="seconds per connection (default 5)")
    args = parser.parse_args(argv)
    if not (args.open or args.closed or args.refused):
        parser.error("give at least one --open, --closed or --refused target")

    print(f"network test {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} from {socket.gethostname()}")
    checks = ([(target, "reachable") for target in args.open]
              + [(target, "unreachable") for target in args.closed]
              + [(target, "refused") for target in args.refused])
    failed = 0
    for target, expected in checks:
        try:
            detail, outcome = probe(target, args.timeout), "reachable"
        except Unreachable as exc:
            detail, outcome = str(exc), "refused" if exc.resolved else "unresolved"
        except ValueError as exc:
            detail, outcome = str(exc), None
        if expected == "unreachable":
            passed = outcome in ("refused", "unresolved")
        else:
            passed = outcome == expected
        failed += not passed
        print(f"{'PASS' if passed else 'FAIL'}  {expected:<11} {target}\n      {detail}")
    print(f"{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
