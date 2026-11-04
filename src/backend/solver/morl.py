"""MORL route policy in the Backend process (TI-SE-07b, ROUTE_SOLVER_PROVIDER=morl).

At start-up a background task resolves the registered package (solver.registry:
morl-route-policy@production over private networking, or ROUTE_SOLVER_MODEL_DIR),
verifies it and loads it with the loader its package.json names
(inference.policy_family). Start-up never waits for the registry: until the policy
is loaded, and whenever it cannot be used, solve() raises SolverError and the
orchestrator answers with the A* baseline, labelled (route_source "baseline",
fallback_reason "model_*"). A failed load is retried with a capped backoff; a
version that loaded stays in use until the next restart.

Per request:
    contract  package graph_version and data contract major against the request's graph
              (route_model.model_package.require_compatible); rejected -> model_unavailable
    mode      the travel mode the policy was trained for; another mode -> model_unavailable
    decode    nearest-alpha member, greedy decoding on the planner's subgraph within
              deadline_ms; PolicyFailure timeout -> model_timeout, dead_end/step_limit
              -> model_no_route
    reply     recommended = the policy's route, comparison = fastest route of the subgraph

The decoder reads the RoadRouter interface (coords, edges, arcs, search). SubgraphRouter
provides it from the solver input, so the policy sees the same edges, travel times
and exposure the Backend checks the route against and draws.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from planner.models import SolverInput
from route_model.model_package import Compatibility, load_manifest, package_digest, require_compatible

from .cancel import CHECK_EVERY, CancelToken, run_cancellable
from .config import SolverSettings
from .contract import SOLVER_NO_ROUTE, SOLVER_TIMEOUT, SOLVER_UNREACHABLE, SolveRequest, SolverError, SolverReply
from .paths import shortest_path

logger = logging.getLogger("solver")


class UnusablePackage(Exception):
    """The package loaded but cannot serve (unknown policy family, refused contract)."""


@dataclass(frozen=True, slots=True)
class _Edge:
    length_m: float
    time_s: float


class SubgraphRouter:
    """The part of route_model.astar_osm.RoadRouter the MORL decoder reads, built from one solver input."""

    def __init__(self, si: SolverInput, token: CancelToken | None = None):
        self.coords = {node: (p.lon, p.lat) for node, p in si.nodes.items()}
        self.edges: dict[int, _Edge] = {}
        self.arcs: dict[int, list[tuple[int, float, int, bool]]] = {}
        for i, e in enumerate(si.edges):
            if token is not None and i % CHECK_EVERY == 0:
                token.check()
            self.edges[e.edge_id] = _Edge(e.length_m, e.travel_time_s)
            if e.u == e.v:
                continue
            self.arcs.setdefault(e.u, []).append((e.v, e.travel_time_s, e.edge_id, True))
            if e.bidirectional:
                self.arcs.setdefault(e.v, []).append((e.u, e.travel_time_s, e.edge_id, False))
        for arcs in self.arcs.values():
            arcs.sort()  # RoadRouter order: the policy's action index depends on it

    def search(self, start: int, end: int):
        """Dijkstra on travel time; (path [(edge_id, forward)], seconds, settled) like RoadRouter.search."""
        import heapq
        best, previous, settled, queue = {start: 0.0}, {}, set(), [(0.0, start)]
        while queue:
            cost, node = heapq.heappop(queue)
            if node in settled or cost > best[node]:
                continue
            settled.add(node)
            if node == end:
                path = []
                while node != start:
                    parent, edge_id, forward = previous[node]
                    path.append((edge_id, forward))
                    node = parent
                return path[::-1], cost, len(settled)
            for neighbor, seconds, edge_id, forward in self.arcs.get(node, ()):
                if cost + seconds < best.get(neighbor, math.inf):
                    best[neighbor] = cost + seconds
                    previous[neighbor] = (node, edge_id, forward)
                    heapq.heappush(queue, (cost + seconds, neighbor))
        return None, None, len(settled)


# decode(router, exposure, start, end, (alpha, beta), timeout_ms) -> (member alpha or None, PolicyRoute)
Decoder = Callable[..., tuple[float | None, Any]]


def _load_fixed_preference_set(directory: Path, manifest: dict, data_contracts_version: str) -> Decoder:
    """TI-AI-09 PIC B: one policy per alpha, the nearest member serves a request."""
    from route_model.morl.infer import decode_for_preference
    from route_model.morl.packaging import load_packaged_policy_set, runtime_contract

    # The request's graph is checked per request; here only the files and the contract major.
    policies, _, _ = load_packaged_policy_set(
        directory, runtime_contract(manifest["contract"].get("graph_version"), data_contracts_version))
    env = json.loads((directory / "set_manifest.json").read_text(encoding="utf-8")).get("config", {}).get("env", {})
    limits = {"max_steps": env.get("max_steps", 1000), "max_steps_factor": env.get("max_steps_factor", 3.0)}

    def decode(router, exposure, start, end, preference, timeout_ms):
        return decode_for_preference(policies, router, exposure, start, end, preference, timeout_ms=timeout_ms,
                                     **limits)
    return decode


#: policy_family in package.json -> loader. A preference-conditioned policy (TI-AI-09 PIC A)
#: is added here with its own family name; the rest of the Backend does not change.
LOADERS: dict[str, Callable[[Path, dict, str], Decoder]] = {
    "fixed_preference_set": _load_fixed_preference_set,
}


@dataclass
class _Loaded:
    name: str
    version: str
    package_sha256: str
    manifest: dict
    decode: Decoder
    compatibility: dict[str, Compatibility] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.version}"

    @property
    def family(self) -> str:
        return self.manifest["inference"]["policy_family"]

    @property
    def mode(self) -> str | None:
        return self.manifest["contract"].get("mode")


def load_package(directory: Path, name: str, version: str, data_contracts_version: str) -> _Loaded:
    """Load a verified package directory with the loader of its policy family."""
    manifest = load_manifest(directory)
    family = manifest.get("inference", {}).get("policy_family")
    loader = LOADERS.get(family)
    if loader is None:
        raise UnusablePackage(f"policy_family {family!r} has no loader in this Backend")
    decode = loader(directory, manifest, data_contracts_version)
    return _Loaded(name, version, package_digest(directory), manifest, decode)


class MorlSolver:
    name = "morl"

    def __init__(self, settings: SolverSettings, *, data_contracts_version: str | None = None,
                 registry_transport=None):
        if data_contracts_version is None:
            from public_api.data_contracts import CONTRACT_VERSION as data_contracts_version
        self.settings = settings
        self.data_contracts_version = data_contracts_version
        self.state = "loading"            # loading | ready | unavailable
        self._loaded: _Loaded | None = None
        self._task: asyncio.Task | None = None
        self._registry_transport = registry_transport  # tests inject httpx.MockTransport
        self._slots = asyncio.Semaphore(max(1, settings.model_concurrency))

    # ---------------------------------------------------------------- loading

    def _load(self) -> _Loaded:
        import torch
        torch.set_num_threads(max(1, self.settings.torch_threads))
        s = self.settings
        if s.model_dir:
            directory = Path(s.model_dir)
            from route_model.model_package import validate_package
            validate_package(directory, "route_policy")
            return load_package(directory, s.model_name, "local", self.data_contracts_version)
        from .registry import RegistryClient
        client = RegistryClient(s.registry_url, timeout_s=s.registry_timeout_s, attempts=s.registry_attempts,
                                transport=self._registry_transport)
        resolved = client.resolve(s.model_name, s.model_selector, s.model_cache_dir)
        return load_package(resolved.directory, resolved.name, resolved.version, self.data_contracts_version)

    async def load_once(self) -> bool:
        """One load attempt; True when the policy is ready. Failures are logged, never raised."""
        started = time.perf_counter()
        try:
            loaded = await asyncio.to_thread(self._load)
        except Exception as exc:  # noqa: BLE001 - any failure leaves the baseline fallback in place
            self.state = "unavailable"
            logger.warning("route_model_load outcome=failed model=%s@%s error=%s: %s", self.settings.model_name,
                           self.settings.model_selector, type(exc).__name__, exc)
            return False
        self._loaded, self.state = loaded, "ready"
        logger.info("route_model_load outcome=ok model=%s package_sha256=%s package_version=%s policy_family=%s "
                    "graph_version=%s mode=%s load_ms=%.0f", loaded.identity, loaded.package_sha256,
                    loaded.manifest.get("model_version"), loaded.family, loaded.manifest["contract"].get("graph_version"),
                    loaded.mode, (time.perf_counter() - started) * 1000)
        return True

    async def _load_until_ready(self) -> None:
        delay = self.settings.model_retry_initial_s
        while not await self.load_once():
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.settings.model_retry_max_s)

    async def start(self) -> None:
        """Load in the background; requests are answered by the fallback until it is ready."""
        if self._task is None:
            self._task = asyncio.create_task(self._load_until_ready(), name="route-model-load")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    def describe(self) -> dict[str, Any]:
        """Which policy this solver serves (or tried to), for response metadata and readiness. No error text."""
        loaded = self._loaded
        return {
            "name": loaded.name if loaded else self.settings.model_name,
            "version": loaded.version if loaded else None,
            "package_sha256": loaded.package_sha256 if loaded else None,
            "package_version": loaded.manifest.get("model_version") if loaded else None,
            "policy_family": loaded.family if loaded else None,
            "status": self.state,
            "member_alpha": None,
            "inference_ms": None,
            "compatibility": None,
        }

    # ---------------------------------------------------------------- solving

    def _compatibility(self, loaded: _Loaded, graph_version: str) -> Compatibility:
        result = loaded.compatibility.get(graph_version)
        if result is None:
            from route_model.morl.packaging import runtime_contract
            result = loaded.compatibility[graph_version] = require_compatible(
                loaded.manifest, runtime_contract(graph_version, self.data_contracts_version))
            if result.status == "flagged":
                logger.warning("route_model_contract status=flagged model=%s reasons=%s", loaded.identity, result.reasons)
        return result

    async def solve(self, request: SolveRequest) -> SolverReply:
        loaded = self._loaded
        if loaded is None:
            raise SolverError(SOLVER_UNREACHABLE, f"MORL policy not loaded (state {self.state})")
        si = request.solver_input
        try:
            compatibility = self._compatibility(loaded, si.source.graph_version)
        except ValueError as exc:  # PackageIncompatible
            raise SolverError(SOLVER_UNREACHABLE, f"MORL package rejected: {exc}") from exc
        if loaded.mode and si.mode.value != loaded.mode:
            raise SolverError(SOLVER_UNREACHABLE, f"MORL policy was trained for mode {loaded.mode}, not {si.mode.value}")
        async with self._slots:
            return await run_cancellable(_decode, loaded, compatibility, request)


def _decode(token: CancelToken, loaded: _Loaded, compatibility: Compatibility, request: SolveRequest) -> SolverReply:
    from route_model.morl.infer import PolicyFailure

    si = request.solver_input
    started = time.perf_counter()
    fastest = shortest_path(si, lambda e: e.travel_time_s, token)
    if fastest is None:
        return SolverReply("no_route", "policy", False, None, loaded.identity)
    router = SubgraphRouter(si, token)
    exposure = {e.edge_id: e.exposure_index for e in si.edges}
    token.check()
    remaining_ms = request.deadline_ms - (time.perf_counter() - started) * 1000
    try:
        member, route = loaded.decode(router, exposure, si.origin.node_id, si.destination.node_id,
                                      (si.preference.alpha, si.preference.beta), timeout_ms=max(remaining_ms, 1.0))
    except PolicyFailure as failure:
        reason = SOLVER_TIMEOUT if failure.reason == "timeout" else SOLVER_NO_ROUTE
        raise SolverError(reason, f"MORL policy {failure.reason}: {failure}") from failure
    details = {
        "name": loaded.name, "version": loaded.version, "package_sha256": loaded.package_sha256,
        "package_version": loaded.manifest.get("model_version"), "policy_family": loaded.family,
        "status": "ready", "member_alpha": member,
        "inference_ms": round((time.perf_counter() - started) * 1000, 1), "compatibility": compatibility.status,
    }
    return SolverReply("ok", "policy", True, None, loaded.identity, tuple(route.edge_ids), fastest, details)
