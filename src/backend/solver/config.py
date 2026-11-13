"""Settings for route solving, from the environment or src/backend/.env (ROUTE_SOLVER_*)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from osm.config import BACKEND_DIR

Provider = Literal["mock", "baseline", "http", "morl"]
Fault = Literal["none", "timeout", "error", "no_route", "invalid"]
Fallback = Literal["baseline", "none"]


class SolverSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ROUTE_SOLVER_",
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # mock: deterministic, no AI service needed; baseline: fastest route with the
    # A* of route_model (TI-AI-07) in this process; http: the Multi-Objective Route
    # Model service (IRouteSolve, POST /internal/v1/solve); morl: the registered MORL
    # policy package loaded into this process (TI-SE-07b, solver.morl).
    provider: Provider = "mock"
    url: str | None = Field(None, description="Base URL of the Route Model service, for provider=http")

    # provider=morl: which registered version, and where it comes from. The registry is reached
    # through MLFLOW_TRACKING_URI (private networking, artifact proxy); model_dir loads a local
    # package instead (tests, local docker without a registry).
    model_name: str = "morl-route-policy"
    model_selector: str = Field("production", description="Registry alias, or a version number")
    model_dir: str | None = None
    model_cache_dir: str = "/tmp/sippolusi/route-models"
    registry_url: str | None = Field(
        None, validation_alias=AliasChoices("ROUTE_SOLVER_REGISTRY_URL", "MLFLOW_TRACKING_URI", "registry_url"))
    # The registry sleeps when idle and takes 10-30 s to wake: per call, then bounded retries.
    registry_timeout_s: float = Field(30.0, gt=0)
    registry_attempts: int = Field(3, ge=1)
    # A failed load is retried in the background, doubling up to model_retry_max_s.
    model_retry_initial_s: float = Field(10.0, gt=0)
    model_retry_max_s: float = Field(300.0, gt=0)
    # While the active graph rejects the loaded package, or while the model or alias is not registered
    # yet, the registry is asked again this often. Each check may wake the (sleeping) registry, so it is slow.
    model_recheck_s: float = Field(900.0, gt=0)
    torch_threads: int = Field(2, ge=1)
    model_concurrency: int = Field(2, ge=1)
    # Mock only: make every call fail in a given way (FAULT_INJECTION, IT-K-04).
    fault: Fault = "none"

    # compute_deadline (Tabel 3.6): Backend latency budget and the share kept for
    # building the response (R18 to R20).
    budget_ms: int = Field(8000, gt=0)
    response_margin_ms: int = Field(500, ge=0)
    # IRouteSolve HTTP timeout = deadline_ms + 300 ms (Tabel 3.6, 3.15).
    grace_ms: int = Field(300, ge=0)

    # TI-SE-07: when the configured solver times out, is unreachable, fails or replies
    # outside the contract, run the in-process baseline and mark the response
    # (route_source "baseline", fallback_reason "model_*"). "none" keeps the design's
    # behaviour (Tabel 3.12): 503 route_service_unavailable.
    fallback: Fallback = "baseline"
    # Upper bound for the fallback run, further capped so the whole request stays
    # within the API Gateway timeout (10 s, Tabel 3.15).
    fallback_timeout_ms: int = Field(1500, gt=0)
    gateway_timeout_ms: int = Field(10000, gt=0)

    @model_validator(mode="after")
    def _url_for_http(self) -> SolverSettings:
        if self.provider == "http" and not self.url:
            raise ValueError("ROUTE_SOLVER_URL wajib diisi bila ROUTE_SOLVER_PROVIDER=http")
        if self.provider == "morl" and not (self.registry_url or self.model_dir):
            raise ValueError("ROUTE_SOLVER_PROVIDER=morl membutuhkan MLFLOW_TRACKING_URI (registry) "
                             "atau ROUTE_SOLVER_MODEL_DIR (paket lokal)")
        return self


@lru_cache
def get_solver_settings() -> SolverSettings:
    return SolverSettings()
