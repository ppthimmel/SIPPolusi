"""Request correlation, request log and in-process metrics for the HTTP services (TI-DO-08).

``install_request_observability(app, service)`` adds one middleware that

- adopts the API Gateway's X-Request-ID (or creates a UUID when it is absent or
  not a safe ID), stores it on ``request.state.request_id`` and returns it in the
  response header, so osm.errors and the solver client reuse the same ID;
- runs the request inside ``log_config.correlation`` so every record logged while
  handling it carries ``correlation_id``;
- logs one ``http_request`` event with the route template (never the query
  string or path parameters, which may hold coordinates), status and latency;
- counts the request in ``METRICS``, served as Prometheus text by ``/metrics``.

Health probes and metric scrapes are logged at DEBUG so they do not drown real
traffic; they are still counted.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from log_config import correlation, log_event, new_correlation_id, safe_correlation_id

REQUEST_ID_HEADER = "X-Request-ID"
QUIET_PREFIXES = ("/health", "/metrics")
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

logger = logging.getLogger("request")

_HELP = {
    "sippolusi_http_requests_total": ("counter", "HTTP requests by route template and status."),
    "sippolusi_http_request_duration_seconds": ("summary", "HTTP request latency by route template."),
    "sippolusi_route_requests_total": ("counter", "Route requests by outcome (ok, fallback, failed)."),
    "sippolusi_route_fallback_total": ("counter", "Routes answered by the baseline fallback, by reason."),
    "sippolusi_route_inference_failures_total": ("counter", "Route policy inference failures, by reason."),
}


class Metrics:
    """Thread-safe counters and latency sums in the Prometheus text format (no client library)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)

    def inc(self, name: str, amount: float = 1.0, **labels: object) -> None:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            self._values[key] += amount

    def observe_request(self, service: str, method: str, route: str, status: int, seconds: float) -> None:
        self.inc("sippolusi_http_requests_total", service=service, method=method, route=route, status=status)
        self.inc("sippolusi_http_request_duration_seconds_sum", seconds, service=service, method=method, route=route)
        self.inc("sippolusi_http_request_duration_seconds_count", service=service, method=method, route=route)

    def value(self, name: str, **labels: object) -> float:
        with self._lock:
            return self._values.get((name, tuple(sorted((k, str(v)) for k, v in labels.items()))), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def render(self) -> str:
        with self._lock:
            items = sorted(self._values.items())
        lines: list[str] = []
        described: set[str] = set()
        for (name, labels), value in items:
            family = name.removesuffix("_sum").removesuffix("_count") if name not in _HELP else name
            if family in _HELP and family not in described:
                kind, text = _HELP[family]
                lines += [f"# HELP {family} {text}", f"# TYPE {family} {kind}"]
                described.add(family)
            rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
            lines.append(f"{name}{{{rendered}}} {value:g}" if rendered else f"{name} {value:g}")
        return "\n".join(lines) + "\n"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


METRICS = Metrics()


def _route_template(request: Request) -> str:
    """The matched route's path template; unmatched paths share one label to keep cardinality bounded."""
    route = request.scope.get("route")
    return getattr(route, "path", None) or "unmatched"


def install_request_observability(app: FastAPI, service: str) -> None:
    """Add the correlation/logging/metrics middleware and GET /metrics to ``app``.

    Install it after any other middleware so it is the outermost one and sees
    every request, including those that end in an unhandled exception.
    """

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        request_id = safe_correlation_id(request.headers.get(REQUEST_ID_HEADER)) or new_correlation_id()
        request.state.request_id = request_id
        started = time.perf_counter()
        status = 500
        with correlation(request_id):
            try:
                response = await call_next(request)
                status = response.status_code
            finally:
                elapsed = time.perf_counter() - started
                route = _route_template(request)
                METRICS.observe_request(service, request.method, route, status, elapsed)
                if request.url.path.startswith(QUIET_PREFIXES):
                    level = logging.DEBUG
                else:
                    level = logging.ERROR if status >= 500 else logging.INFO
                log_event(logger, "http_request", level, method=request.method, route=route, status=status,
                          duration_ms=round(elapsed * 1000, 1))
        response.headers.setdefault(REQUEST_ID_HEADER, request_id)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def _metrics():
        """Prometheus scrape target (IMetricsSink)."""
        return PlainTextResponse(METRICS.render(), media_type=PROMETHEUS_CONTENT_TYPE)
