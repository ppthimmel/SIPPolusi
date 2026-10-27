import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from database import dispose_engine, init_engine, is_configured, ping
from log_config import configure_logging, log_event
from route_model.astar_osm import DEFAULT_GRAPH
from route_model.inference import solve_route, solve_baseline_route
from settings import load_settings
from telemetry import METRICS, install_request_observability

SERVICE = "backend"
# Files the baseline router reads; without them every route answers data_unavailable.
GRAPH_FILES = ("graph_version.csv", "road_node.csv.gz", "road_edge.csv.gz")

# serve.py configures logging before importing the app; this covers
# `uvicorn main:app` during local development.
if not logging.getLogger().handlers:
    configure_logging(service=SERVICE)
logger = logging.getLogger(__name__)

# Readiness must answer well within Railway's health check timeout.
DATABASE_CHECK_TIMEOUT_S = 3

app = FastAPI(
    title="SIPPolusi Backend API",
    description="Backend API for routing and spatial downscaling models.",
    version="1.0.0"
)
install_request_observability(app, SERVICE)

async def _check_database() -> str:
    """'ok', 'not_configured' or 'unavailable'. Details go to the log, not the response."""
    if not is_configured():
        return "not_configured"
    try:
        await ping(DATABASE_CHECK_TIMEOUT_S)
        return "ok"
    except Exception as exc:  # noqa: BLE001 - reported as "unavailable"
        logger.error("Database check failed: %s: %s", type(exc).__name__, exc)
        return "unavailable"


def _check_route_graph() -> str:
    """'ok' or 'unavailable': the road graph files the route search loads are present."""
    folder = Path(os.environ.get("OSM_GRAPH_DIR") or DEFAULT_GRAPH)
    if all((folder / name).is_file() for name in GRAPH_FILES):
        return "ok"
    logger.warning("Route graph unavailable: missing files in %s (set OSM_GRAPH_DIR)", folder)
    return "unavailable"


@app.on_event("startup")
async def startup():
    # Raises ConfigError with an actionable message; uvicorn then exits.
    settings = load_settings()
    app.state.settings = settings
    if settings.database_url is None:
        logger.warning("DATABASE_URL is not set. Database features will not work.")
        return

    init_engine(settings.database_url)
    # A database outage must not keep the API down: start anyway and let
    # /health/ready report it until the database is reachable.
    if await _check_database() == "ok":
        logger.info("Successfully connected to the database.")


@app.on_event("shutdown")
async def shutdown():
    await dispose_engine()
    logger.info("Database engine disposed.")

@app.get("/health")
async def health_check():
    """Liveness: the process is up and serving. Used by Railway's deploy health check."""
    return {"status": "ok", "service": "backend"}


@app.get("/health/ready")
async def readiness_check():
    """Readiness: dependencies needed to serve requests are reachable (503 if not).

    Only the database gates readiness. route_graph is reported but does not: public
    POST /v1/routes is served by the Cache API (whose readiness checks the active graph),
    and this image does not ship the graph files used by the local /v1/routes baseline.
    """
    checks = {"database": await _check_database(), "route_graph": _check_route_graph()}
    ready = checks["database"] == "ok"
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "service": "backend", "checks": checks},
    )


def route_response(result):
    code = {"invalid_input": 422, "snap_failed": 404, "no_route": 404,
            "same_node": 422, "data_unavailable": 503, "invalid_graph": 503}.get(result["status"], 200)
    return JSONResponse(result, status_code=code)


def _record_route(result: dict, status_code: int, started: float) -> None:
    """Log and count one route request, so a fallback is visible beyond its 200 response."""
    fallback_reason = result.get("fallback_reason")
    outcome = "failed" if status_code >= 400 else "fallback" if fallback_reason else "ok"
    METRICS.inc("sippolusi_route_requests_total", outcome=outcome)
    if fallback_reason:
        METRICS.inc("sippolusi_route_fallback_total", reason=fallback_reason)
    log_event(logger, "route_inference", logging.INFO if outcome == "ok" else logging.WARNING,
              outcome=outcome, status=result.get("status"), route_source=result.get("route_source"),
              fallback_reason=fallback_reason, http_status=status_code,
              duration_ms=round((time.perf_counter() - started) * 1000, 1))

@app.post("/v1/routes")
def plan_route(request: dict):
    """Validated route request; label unavailable/failed policy fallback explicitly.

    Runs in FastAPI's worker thread pool because graph search is synchronous.
    Policy timeout/cancellation belongs to the future model adapter.
    """
    origin = request.get("origin")
    destination = request.get("destination")
    mode = request.get("mode")
    alpha = request.get("alpha", 0.5)
    beta = request.get("beta", 0.5)

    started = time.perf_counter()
    try:
        result = solve_route(origin, destination, mode, alpha, beta)
    except Exception:
        logger.exception("Route policy inference failed, falling back to baseline")
        METRICS.inc("sippolusi_route_inference_failures_total", reason="policy_error")
        result = solve_baseline_route(origin, destination, mode, fallback_reason="policy_error")

    response = route_response(result)
    _record_route(result, response.status_code, started)
    return response
