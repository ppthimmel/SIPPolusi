"""Standalone FastAPI app for the OSM endpoints.

Run from src/backend:
    uvicorn osm.app:app --reload

To mount the same endpoints in another FastAPI app:
    from osm.app import osm_lifespan
    from osm.errors import install_error_handling
    from osm.router import router as osm_router

    app = FastAPI(lifespan=osm_lifespan)
    install_error_handling(app)
    app.include_router(osm_router)
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import OsmSettings, get_settings
from .db import OsmDatabase
from .errors import CacheUnavailable, install_error_handling
from .repository import OsmRepository
from .router import router
from .schemas import HealthOut

logger = logging.getLogger("osm")


@asynccontextmanager
async def osm_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the read-only pool and put settings and repository on app.state."""
    settings = get_settings()
    db = OsmDatabase(settings)
    await db.start()
    app.state.osm_settings = settings
    app.state.osm_repository = OsmRepository(db, settings.version_cache_ttl_s)
    try:
        yield
    finally:
        await db.close()


def create_app(repository: OsmRepository | None = None, settings: OsmSettings | None = None) -> FastAPI:
    """Build the app. Passing repository (and settings) skips the database, for tests."""
    if repository is None:
        lifespan = osm_lifespan
    else:
        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            app.state.osm_settings = settings or get_settings()
            app.state.osm_repository = repository
            yield

    app = FastAPI(
        title="SIPPolusi Backend - OSM Cache DB",
        description="Read-only access to the Jakarta road graph (schema osm). "
                    "Road data © OpenStreetMap contributors, ODbL "
                    "(https://www.openstreetmap.org/copyright).",
        version="1.0.0",
        lifespan=lifespan,
    )
    install_error_handling(app)
    app.include_router(router)

    @app.get("/health", response_model=HealthOut, tags=["health"])
    async def health():
        repo: OsmRepository = app.state.osm_repository
        try:
            if not await repo.ping():
                raise CacheUnavailable("ping failed")
            version = (await repo.get_active_version()).version
        except CacheUnavailable:
            return HealthOut(status="degraded", database="unavailable", graph_version=None)
        return HealthOut(status="ok", database="ok", graph_version=version)

    return app


logging.basicConfig(level=logging.INFO)
app = create_app()
