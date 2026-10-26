"""Backend runtime configuration, validated once at start-up.

Every problem names the variable and how to fix it, and never echoes a value
that may hold a credential.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

ENVIRONMENTS = ("development", "staging", "production")
_ALIASES = {"dev": "development", "local": "development", "stage": "staging", "prod": "production"}
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class ConfigError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(f"- {p}" for p in problems))
        self.problems = problems


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str | None
    port: int
    log_level: str

    def summary(self) -> str:
        """One line safe to log: no credentials."""
        database = "not set"
        if self.database_url:
            parts = urlsplit(self.database_url)
            database = f"{parts.hostname}:{parts.port or 5432}{parts.path}"
        return f"environment={self.environment} port={self.port} log_level={self.log_level} database={database}"


def _environment(env: Mapping[str, str], problems: list[str]) -> str:
    explicit = env.get("ENVIRONMENT", "").strip().lower()
    if explicit:
        name = _ALIASES.get(explicit, explicit)
        if name not in ENVIRONMENTS:
            problems.append(f"ENVIRONMENT={explicit!r} is not recognised; use one of {', '.join(ENVIRONMENTS)}.")
            return "development"
        return name
    # Railway always injects its environment name. Anything other than the
    # development environment is a shared deployment, so treat it as staging.
    railway = env.get("RAILWAY_ENVIRONMENT_NAME", "").strip().lower()
    if railway:
        name = _ALIASES.get(railway, railway)
        return name if name in ENVIRONMENTS else "staging"
    return "development"


def check_database_url(url: str) -> str | None:
    """Return a problem description, or None if the DSN looks usable."""
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError on a non-numeric port
    except ValueError:
        return "DATABASE_URL could not be parsed; expected postgresql://USER:PASSWORD@HOST:5432/DBNAME."
    if parts.scheme not in ("postgresql", "postgres", "postgresql+asyncpg"):
        return (
            f"DATABASE_URL uses scheme {parts.scheme!r}; expected postgresql://USER:PASSWORD@HOST:5432/DBNAME "
            "(on Railway use the reference ${{database.DATABASE_URL}})."
        )
    if not parts.hostname or parts.path in ("", "/"):
        return "DATABASE_URL is missing the host or database name; expected postgresql://USER:PASSWORD@HOST:5432/DBNAME."
    return None


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    problems: list[str] = []
    environment = _environment(env, problems)

    raw_port = env.get("PORT", "8000").strip() or "8000"
    port = int(raw_port) if raw_port.isdigit() else 0
    if not 1 <= port <= 65535:
        problems.append(f"PORT={raw_port!r} must be a number between 1 and 65535 (Railway sets it automatically).")

    log_level = env.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
    if log_level not in _LOG_LEVELS:
        problems.append(f"LOG_LEVEL={log_level!r} is not recognised; use one of {', '.join(_LOG_LEVELS)}.")

    database_url = env.get("DATABASE_URL", "").strip() or None
    if database_url:
        problem = check_database_url(database_url)
        if problem:
            problems.append(problem)
    elif environment != "development":
        problems.append(
            f"DATABASE_URL is required in {environment}; set it in the service variables "
            "(Railway: ${{database.DATABASE_URL}})."
        )

    if problems:
        raise ConfigError(problems)
    return Settings(environment=environment, database_url=database_url, port=port, log_level=log_level)
