import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from database import dispose_engine, init_engine, is_configured, ping
from log_config import configure_logging
from route_model.inference import solve_route, solve_baseline_route
from settings import load_settings

# serve.py configures logging before importing the app; this covers
# `uvicorn main:app` during local development.
if not logging.getLogger().handlers:
    configure_logging()
logger = logging.getLogger(__name__)

# Readiness must answer well within Railway's health check timeout.
DATABASE_CHECK_TIMEOUT_S = 3

app = FastAPI(
    title="SIPPolusi Backend API",
    description="Backend API for routing and spatial downscaling models.",
    version="1.0.0"
)

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
    """Readiness: dependencies needed to serve requests are reachable (503 if not)."""
    checks = {"database": await _check_database()}
    ready = all(result == "ok" for result in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "service": "backend", "checks": checks},
    )


def route_response(result):
    code = {"invalid_input": 422, "snap_failed": 404, "no_route": 404,
            "same_node": 422, "data_unavailable": 503, "invalid_graph": 503}.get(result["status"], 200)
    return JSONResponse(result, status_code=code)

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

    try:
        result = solve_route(origin, destination, mode, alpha, beta)
    except Exception:
        logger.exception("Route policy inference failed, falling back to baseline")
        result = solve_baseline_route(origin, destination, mode, fallback_reason="policy_error")

    return route_response(result)

@app.get("/metrics")
async def metrics():
    """Endpoint for Prometheus metrics (IMetricsSink)."""
    return {"metrics": "Not implemented yet"}
