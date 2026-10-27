"""Settings for the public API (TI-SE-08), from the environment or src/backend/.env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from osm.config import BACKEND_DIR


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="API_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Largest request body accepted by the Cache API. A RouteRequest is about 200
    # bytes; 16 KiB leaves room without letting large bodies reach the parser.
    max_body_bytes: int = Field(16 * 1024, gt=0)

    # GET /v1/exposure-surface (get_exposure_surface, Tabel 3.6).
    # zoom >= 14: one Feature per edge, bbox at most 25 km².
    surface_edge_max_km2: float = Field(25.0, gt=0)
    # zoom < 14: grid cells; bbox at most this many cells of the zoom's cell size.
    surface_max_cells: int = Field(10_000, gt=0)

    # Origins allowed by CORS, comma separated (e.g. the Frontend domain, Tabel 3.15).
    # Empty: no CORS headers, so browsers on other origins are refused.
    cors_origins: str = ""

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
