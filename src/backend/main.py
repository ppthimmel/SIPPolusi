from fastapi import FastAPI, HTTPException, status
import asyncpg
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="SIPPolusi Backend API",
    description="Backend API for routing and spatial downscaling models.",
    version="1.0.0"
)

# Database connection pool
db_pool = None

@app.on_event("startup")
async def startup():
    global db_pool
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.warning("DATABASE_URL environment variable is not set. Database features will not work.")
        return
        
    try:
        db_pool = await asyncpg.create_pool(database_url)
        logger.info("Successfully connected to the database pool.")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")

@app.on_event("shutdown")
async def shutdown():
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("Database pool closed.")

@app.get("/health")
async def health_check():
    """Health check endpoint for Railway and NGINX."""
    return {"status": "ok", "service": "backend"}

@app.get("/v1/routes")
async def get_routes():
    # Placeholder for IRouteQuery implementation
    return {"message": "Route optimization will be implemented here."}

@app.get("/metrics")
async def metrics():
    """Endpoint for Prometheus metrics (IMetricsSink)."""
    return {"metrics": "Not implemented yet"}
