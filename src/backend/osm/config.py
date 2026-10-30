"""Settings for the OSM module.

Values come from environment variables first, then from src/backend/.env.
Only DATABASE_URL is required; everything else has a default taken from the
design document (Tabel 3.3, 3.6, 3.15).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models import BBox

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Daratan DKI Jakarta (±662 km², subbab 4.3.3). Kepulauan Seribu is outside the
# study area even though osm.road_edge contains its roads. Same box as
# src/data_worker/contracts.py JAKARTA_BBOX.
DEFAULT_STUDY_AREA = "106.68,-6.38,106.98,-6.08"


class OsmSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OSM_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL")

    # IRoadGraphRead: statement_timeout 1,5 detik (Tabel 3.15).
    statement_timeout_ms: int = Field(1500, gt=0)
    # Client-side limit on a whole query including result transfer. Server time
    # is bounded by statement_timeout; this only catches a stalled network.
    # Matches the API Gateway's 10 s upstream timeout (Tabel 3.15).
    command_timeout_s: float = Field(10.0, gt=0)
    connect_timeout_s: float = Field(5.0, gt=0)
    # Two connections so read_road_graph can fetch edges and nodes concurrently.
    pool_min_size: int = Field(2, ge=0)
    pool_max_size: int = Field(10, ge=1)

    # Active graph_version changes only on a weekly/manual import.
    version_cache_ttl_s: float = Field(60.0, ge=0)

    # snap_to_graph(max_distance_m=200) (Tabel 3.6).
    snap_max_distance_m: float = Field(200.0, gt=0)
    # Largest bbox accepted by /v1/osm/graph and /v1/osm/roads.
    max_bbox_km2: float = Field(25.0, gt=0)
    study_area_bbox: str = DEFAULT_STUDY_AREA

    # ModeProfile speeds (Tabel 3.3), configurable via OSM_WALK_SPEED_MPS and
    # OSM_BIKE_SPEED_MPS. When changing defaults, check route_model/astar_osm.py
    # and docs/route-time-exposure.md so standalone runs and documentation agree.
    walk_speed_mps: float = Field(1.3, gt=0)
    bike_speed_mps: float = Field(4.2, gt=0)

    @field_validator("database_url")
    @classmethod
    def _asyncpg_dsn(cls, value: str) -> str:
        # asyncpg wants a plain libpq URL; accept SQLAlchemy-style schemes too.
        for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
            if value.startswith(prefix):
                return "postgresql://" + value[len(prefix):]
        return value

    @field_validator("study_area_bbox")
    @classmethod
    def _valid_study_area(cls, value: str) -> str:
        BBox.parse(value)
        return value

    @property
    def study_area(self) -> BBox:
        return BBox.parse(self.study_area_bbox)


@lru_cache
def get_settings() -> OsmSettings:
    return OsmSettings()
