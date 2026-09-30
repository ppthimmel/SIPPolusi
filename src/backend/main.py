from fastapi import FastAPI, HTTPException, status

import os
import logging

from sqlalchemy import text
from database import dispose_engine, get_session, init_engine
from route_model.inference import solve_route, solve_baseline_route

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SIPPolusi Backend API",
    description="Backend API for routing and spatial downscaling models.",
    version="1.0.0"
)

@app.on_event("startup")
async def startup():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.warning("DATABASE_URL environment variable is not set. Database features will not work.")
        return

    try:
        init_engine(database_url)
        async for session in get_session():
            await session.execute(text("SELECT 1"))
        logger.info("Successfully connected to the database.")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")

@app.on_event("shutdown")
async def shutdown():
    await dispose_engine()
    logger.info("Database engine disposed.")

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway and NGINX."""
    return {"status": "ok", "service": "backend"}

@app.post("/v1/routes")
async def plan_route(request: dict):
    """Real-time critical path: validate request, run route policy inference within
    the latency budget, fall back to the baseline route on failure or timeout (PF-09)."""
    origin = request.get("origin")
    destination = request.get("destination")
    mode = request.get("mode")
    alpha = request.get("alpha", 0.5)
    beta = request.get("beta", 0.5)

    try:
        result = solve_route(origin, destination, mode, alpha, beta)
    except Exception as e:
        logger.error(f"Route policy inference failed, falling back to baseline: {e}")
        result = solve_baseline_route(origin, destination, mode)

    return result

@app.get("/metrics")
async def metrics():
    """Endpoint for Prometheus metrics (IMetricsSink)."""
    return {"metrics": "Not implemented yet"}
