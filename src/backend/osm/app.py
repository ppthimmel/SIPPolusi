"""Standalone FastAPI app for the cache endpoints (/v1/osm, /v1/pollution), the
request planner (/v1/routes/solver-input), route solving (/v1/routes/solve, /plan)
and the public API (POST /v1/routes, GET /v1/exposure-surface, TI-SE-08).

Run from src/backend:
    uvicorn osm.app:app --reload

To mount the same endpoints in another FastAPI app:
    from osm.app import osm_lifespan
    from osm.errors import install_error_handling
    from osm.router import router as osm_router
    from pollution.router import router as pollution_router

    app = FastAPI(lifespan=osm_lifespan)
    install_error_handling(app)
    app.include_router(osm_router)
    app.include_router(pollution_router)
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from log_config import configure_logging
from planner.router import router as planner_router
from public_api.config import ApiSettings, get_api_settings
from public_api.limits import BodySizeLimit
from public_api.openapi import install_openapi
from public_api.router import router as public_router
from solver.config import SolverSettings, get_solver_settings
from solver.contract import RouteSolver
from solver.factory import make_solver
from solver.router import router as solver_router
from pollution.config import get_pollution_settings
from pollution.repository import PollutionRepository
from pollution.router import router as pollution_router
from telemetry import install_request_observability

from .config import OsmSettings, get_settings
from .db import OsmDatabase
from .errors import CacheUnavailable, install_error_handling
from .repository import OsmRepository
from .router import router
from .schemas import HealthOut

logger = logging.getLogger("osm")

SERVICE = "cache-api"


@asynccontextmanager
async def osm_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the read-only pool (shared by both cache schemas) and put settings and
    repositories on app.state."""
    settings = get_settings()
    db = OsmDatabase(settings)
    await db.start()
    osm_repository = OsmRepository(db, settings.version_cache_ttl_s)
    app.state.osm_settings = settings
    app.state.osm_repository = osm_repository
    app.state.pollution_repository = PollutionRepository(
        db, osm_repository, get_pollution_settings().stale_after_s)
    solver_settings = get_solver_settings()
    app.state.solver_settings = solver_settings
    app.state.route_solver = make_solver(solver_settings)
    logger.info("Route solver provider: %s", app.state.route_solver.name)
    try:
        yield
    finally:
        await db.close()


def create_app(
    repository: OsmRepository | None = None,
    settings: OsmSettings | None = None,
    pollution_repository: PollutionRepository | None = None,
    solver: RouteSolver | None = None,
    solver_settings: SolverSettings | None = None,
    api_settings: ApiSettings | None = None,
) -> FastAPI:
    """Build the app. Passing repositories (and settings, solver) skips the database, for tests."""
    api_settings = api_settings or get_api_settings()
    if repository is None:
        lifespan = osm_lifespan
    else:
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            app.state.osm_settings = settings or get_settings()
            app.state.osm_repository = repository
            app.state.pollution_repository = pollution_repository
            app.state.solver_settings = solver_settings or SolverSettings(_env_file=None)
            app.state.route_solver = solver or make_solver(app.state.solver_settings)
            yield

    app = FastAPI(
        title="SIPPolusi Backend - Cache API",
        description="Read-only access to the OSM Cache DB (schema osm, Jakarta road graph) and the "
                    "Spatial Pollution Cache DB (schema pollution, EdgeWeight per time window). "
                    "Road data © OpenStreetMap contributors, ODbL "
                    "(https://www.openstreetmap.org/copyright).",
        version="1.2.0",
        lifespan=lifespan,
    )
    app.state.api_settings = api_settings
    install_error_handling(app)
    # Added later = further out: oversized bodies are refused before anything reads them.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    if api_settings.cors_origin_list():
        app.add_middleware(CORSMiddleware, allow_origins=api_settings.cors_origin_list(),
                           allow_methods=["GET", "POST"], allow_headers=["Content-Type", "X-Request-ID"],
                           expose_headers=["X-Request-ID"])
    app.add_middleware(BodySizeLimit, max_bytes=api_settings.max_body_bytes)
    # Outermost (it never reads the body), so refused requests (413) are logged and counted too.
    install_request_observability(app, SERVICE)
    install_openapi(app)
    app.include_router(public_router)
    app.include_router(router)
    app.include_router(pollution_router)
    app.include_router(planner_router)
    app.include_router(solver_router)

    @app.get("/health", response_model=HealthOut, tags=["health"])
    async def health():
        repo: OsmRepository = app.state.osm_repository
        if not await repo.ping():
            return HealthOut(status="degraded", database="unavailable", graph_version=None)
        try:
            version = (await repo.get_active_version()).version
        except CacheUnavailable:  # reachable but no active graph yet (cache_empty)
            return HealthOut(status="degraded", database="ok", graph_version=None)
        out = HealthOut(status="ok", database="ok", graph_version=version)
        pollution: PollutionRepository | None = app.state.pollution_repository
        if pollution is not None:
            try:
                window = await pollution.latest_complete_window()
            except CacheUnavailable:
                return out
            if window is not None:
                out.pollution_time_window = window.time_window_start
                out.pollution_data_stale = pollution.staleness(window)[0]
        return out

    @app.get("/health/live", tags=["health"])
    async def liveness():
        """Liveness: the process is up and serving; no dependency is touched."""
        return {"status": "ok", "service": SERVICE}

    @app.get("/health/ready", tags=["health"])
    async def readiness():
        """Readiness: 503 unless the cache database answers and holds an active road graph.

        Pollution data that is missing or stale does not make the API unready (routes are
        still served, labelled stale), but it is reported: it means the Data Worker is behind.
        """
        repo: OsmRepository = app.state.osm_repository
        checks = {"database": "ok", "graph": "ok", "pollution": "not_configured"}
        if not await repo.ping():
            checks.update(database="unavailable", graph="unknown", pollution="unknown")
        else:
            try:
                await repo.get_active_version()
            except CacheUnavailable as exc:
                checks["graph"] = "empty" if exc.reason == CacheUnavailable.CACHE_EMPTY else "unavailable"
            pollution: PollutionRepository | None = app.state.pollution_repository
            if pollution is not None:
                try:
                    window = await pollution.latest_complete_window()
                except CacheUnavailable:
                    window = None
                checks["pollution"] = ("empty" if window is None
                                       else "stale" if pollution.staleness(window)[0] else "ok")
        ready = checks["database"] == "ok" and checks["graph"] == "ok"
        if not ready:
            logger.warning("readiness failed: %s", checks)
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "not_ready", "service": SERVICE, "checks": checks},
        )

    return app


# Under uvicorn the root logger has no handler yet; tests and embedding apps keep theirs.
if not logging.getLogger().handlers:
    configure_logging(service=SERVICE)
app = create_app()
