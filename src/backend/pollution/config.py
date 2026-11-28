"""Settings for the pollution module, from the environment or src/backend/.env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from osm.config import BACKEND_DIR


class PollutionSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="POLLUTION_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # data_stale = true when the latest complete time window ended more than this
    # long ago (Tabel 3.4 and 3.15: "time window terakhir lebih lama dari 3 jam").
    stale_after_s: float = Field(3 * 3600, gt=0)
    # work_mem for the EdgeWeight read only (SET LOCAL), so the join and sort of a
    # long trip stay in memory. Empty keeps the server default.
    weights_work_mem: str = Field("32MB", pattern=r"^(\d+(kB|MB|GB))?$")

    # Heatmap (GET /v1/exposure-surface) queries share the connection pool with route
    # planning. A zoomed-out surface aggregates hundreds of thousands of rows; while the
    # user pans, the Frontend aborts and resends, but an aborted request's query keeps
    # running on the server. Without a limit these fill the pool and route requests
    # fail with cache_unavailable. So per instance:
    # - at most surface_max_concurrency surface queries hold a connection at once;
    # - a request waits surface_wait_s for a slot, then gets 503 surface_busy (the
    #   Frontend keeps the previous heatmap, dimmed);
    # - each surface query has its own statement_timeout (SET LOCAL), shorter than the
    #   pool default, since the Frontend gives up after 5 s (VITE_SURFACE_TIMEOUT_MS).
    surface_max_concurrency: int = Field(2, ge=1)
    surface_wait_s: float = Field(1.0, ge=0)
    surface_statement_timeout_ms: int = Field(6000, gt=0)


@lru_cache
def get_pollution_settings() -> PollutionSettings:
    return PollutionSettings()
