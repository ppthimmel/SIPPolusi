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


@lru_cache
def get_pollution_settings() -> PollutionSettings:
    return PollutionSettings()
