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
    "sippolusi_http_request_duration_seconds": ("histogram", "HTTP request latency by route template."),
    "sippolusi_route_requests_total": ("counter", "Route requests by outcome (ok, fallback, failed)."),
    "sippolusi_route_fallback_total": ("counter", "Routes answered by the baseline fallback, by reason."),
    "sippolusi_route_inference_failures_total": ("counter", "Route policy inference failures, by reason."),
    "sippolusi_route_solver_requests_total": (
        "counter", "Cache API route solves by solver, outcome (ok, fallback, failed) and route source."),
    "sippolusi_route_solver_duration_seconds": ("histogram", "Cache API route solver time per attempt."),
}

# Upper bounds (seconds) for latency histograms, so Prometheus can compute p95 (histogram_quantile). They bracket
# the targets that matter: the 8 s integration-test budget and the 60 s p95 target (EDO-02).
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 8.0, 10.0, 15.0, 20.0, 30.0, 45.0, 60.0)


def _labels_key(labels: dict[str, object]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((k, str(v)) for k, v in labels.items()))


def _sort_key(item: tuple[tuple[str, tuple[tuple[str, str], ...]], float]):
    """Group series by name and labels, with histogram buckets in increasing ``le`` order."""
    (name, labels), _ = item
    le = dict(labels).get("le")
    return name, tuple(pair for pair in labels if pair[0] != "le"), float(le) if le is not None else 0.0


class Metrics:
    """Thread-safe counters and histograms in the Prometheus text format (no client library)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)

    def inc(self, name: str, amount: float = 1.0, **labels: object) -> None:
        key = (name, _labels_key(labels))
        with self._lock:
            self._values[key] += amount

    def observe(self, name: str, seconds: float, buckets: tuple[float, ...] = LATENCY_BUCKETS,
                **labels: object) -> None:
        """Add one observation to histogram ``name`` (cumulative ``_bucket``, ``_sum`` and ``_count``)."""
        with self._lock:
            for bound in buckets:
                if seconds <= bound:
                    self._values[(f"{name}_bucket", _labels_key({**labels, "le": f"{bound:g}"}))] += 1
            self._values[(f"{name}_bucket", _labels_key({**labels, "le": "+Inf"}))] += 1
            self._values[(f"{name}_sum", _labels_key(labels))] += seconds
            self._values[(f"{name}_count", _labels_key(labels))] += 1

    def observe_request(self, service: str, method: str, route: str, status: int, seconds: float) -> None:
        self.inc("sippolusi_http_requests_total", service=service, method=method, route=route, status=status)
        self.observe("sippolusi_http_request_duration_seconds", seconds, service=service, method=method, route=route)

    def value(self, name: str, **labels: object) -> float:
        with self._lock:
            return self._values.get((name, _labels_key(labels)), 0.0)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()

    def render(self) -> str:
        with self._lock:
            items = sorted(self._values.items(), key=_sort_key)
        lines: list[str] = []
        described: set[str] = set()
        for (name, labels), value in items:
            family = name if name in _HELP else name.removesuffix("_bucket").removesuffix("_sum").removesuffix("_count")
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
