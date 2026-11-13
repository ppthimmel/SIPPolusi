"""MORL route policy in the Backend process (TI-SE-07b, ROUTE_SOLVER_PROVIDER=morl).

At start-up a background task resolves the registered package (solver.registry:
morl-route-policy@production over private networking, or ROUTE_SOLVER_MODEL_DIR),
verifies it and loads it with the loader its package.json names
(inference.policy_family). Start-up never waits for the registry: until the policy
is loaded, and whenever it cannot be used, solve() raises SolverError and the
orchestrator answers with the A* baseline, labelled (route_source "baseline",
fallback_reason "model_*").

Loading: a failure the registry or the package can fix by itself (timeout, 5xx while it
wakes) is retried with a capped backoff. A model, alias or version that is not registered
yet (registry.ModelNotRegistered, e.g. the Cache API started before register_models) is
waited for: logged as outcome=waiting and asked again every ROUTE_SOLVER_MODEL_RECHECK_S,
so the policy is loaded at the first check after it is registered, without a restart. The
interval is slow because each check may wake a sleeping registry. Anything else (another
4xx, a missing or wrong tag, an altered package, an unknown policy family, another contract
major) stops the attempts with status "unavailable", and the cause is logged; it needs a
registry change and a redeploy. Status is "unavailable" while waiting too, so routes get the
A* fallback with fallback_reason model_unavailable. Loads run in a daemon thread and check a
stop event, so a load stuck on a sleeping registry never delays shutdown.

Per request:
    contract  package graph_version and data contract major against the request's graph
              (route_model.model_package.require_compatible). Rejected -> model_unavailable and
              status "rejected". While rejected, the registry alias is resolved again every
              ROUTE_SOLVER_MODEL_RECHECK_S (first at once), so a package for the new graph is picked
              up without a redeploy, whenever it is registered. /health/ready checks the active
              graph too, so a mismatch shows before the first request
    mode      the travel mode the policy was trained for; another mode -> model_unavailable
    decode    nearest-alpha member, greedy decoding on the planner's subgraph within what is
              left of deadline_ms after waiting for a decode slot. PolicyFailure timeout ->
              model_timeout, dead_end/step_limit -> model_no_route. A cancelled request stops
              its decode at the next step, and keeps its slot until then, so no more than
              model_concurrency decodes ever run
    reply     recommended = the policy's route, comparison = fastest route of the subgraph

The decoder reads the RoadRouter interface (coords, edges, arcs, search). SubgraphRouter
provides it from the solver input, so the policy sees the same edges, travel times
and exposure the Backend checks the route against and draws.

Not enforced here: the package's lineage.upstream_model (the spatial model its training
fields came from). The pollution cache identifies a window by a composite model_version,
not by the registered bundle's SHA-256, so the Backend has nothing to compare it with; the
current package records no upstream model either. A recorded one is logged at load.
"""
from __future__ import annotations

import asyncio
import heapq
import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from planner.models import SolverInput
from route_model.model_package import (
    Compatibility, PackageIncompatible, load_manifest, package_digest, require_compatible,
)

from .cancel import CHECK_EVERY, CancelToken
from .config import SolverSettings
from .contract import SOLVER_NO_ROUTE, SOLVER_TIMEOUT, SOLVER_UNREACHABLE, SolveRequest, SolverError, SolverReply

logger = logging.getLogger("solver")


class UnusablePackage(Exception):
    """The package loaded but cannot serve (unknown policy family)."""


@dataclass(frozen=True, slots=True)
class _Edge:
    length_m: float
    time_s: float


class SubgraphRouter:
    """The part of route_model.astar_osm.RoadRouter the MORL decoder reads, built from one solver input.

    It also carries its own reverse arcs (features.reverse_arcs uses them instead of its
    module-level cache), and remembers its searches, so the fastest route the reply needs
    and the reference the decoder computes are one search.
    """

    def __init__(self, si: SolverInput, token: CancelToken | None = None):
        self.token = token
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
        self.reverse_arcs: dict[int, list[tuple[int, float, int]]] = {}
        for u, arcs in self.arcs.items():
            for v, seconds, edge_id, _ in arcs:
                self.reverse_arcs.setdefault(v, []).append((u, seconds, edge_id))
        self._searches: dict[tuple[int, int], tuple] = {}

    def search(self, start: int, end: int):
        """Dijkstra on travel time; (path [(edge_id, forward)], seconds, settled) like RoadRouter.search."""
        key = (start, end)
        if key not in self._searches:
            self._searches[key] = self._search(start, end)
        return self._searches[key]

    def _search(self, start: int, end: int):
        best, previous, settled, queue = {start: 0.0}, {}, set(), [(0.0, start)]
        while queue:
            if self.token is not None:
                self.token.check()
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


# decode(router, exposure, start, end, (alpha, beta), timeout_ms, cancel_check) -> (member alpha or None, PolicyRoute)
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

    def decode(router, exposure, start, end, preference, timeout_ms, cancel_check=None):
        return decode_for_preference(policies, router, exposure, start, end, preference, timeout_ms=timeout_ms,
                                     cancel_check=cancel_check, **limits)
    return decode


def _load_preference_conditioned(directory: Path, manifest: dict, data_contracts_version: str) -> Decoder:
    """PIC A: pass each requested preference directly to the conditioned policy."""
    from route_model.morl.packaging import load_packaged_scalarized_policy, runtime_contract
    from route_model.morl.scalarized import decode_preference_route

    model, _, _ = load_packaged_scalarized_policy(
        directory, runtime_contract(manifest["contract"].get("graph_version"), data_contracts_version))
    env = json.loads((directory / "run_manifest.json").read_text(encoding="utf-8")).get("config", {}).get("env", {})
    limits = {"max_steps": env.get("max_steps", 1000), "max_steps_factor": env.get("max_steps_factor", 3.0)}

    def decode(router, exposure, start, end, preference, timeout_ms, cancel_check=None):
        route = decode_preference_route(model, router, exposure, start, end, preference, timeout_ms=timeout_ms,
                                        cancel_check=cancel_check, **limits)
        return None, route  # There is no discrete member in a conditioned policy.
    return decode


#: policy_family in package.json -> loader.
LOADERS: dict[str, Callable[[Path, dict, str], Decoder]] = {
    "fixed_preference_set": _load_fixed_preference_set,
    "preference_conditioned": _load_preference_conditioned,
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


def _retryable(exc: BaseException) -> bool:
    """Whether asking again can succeed without anyone changing the registry or the package."""
    from .registry import RegistryError
    if isinstance(exc, RegistryError):
        return exc.retryable
    return isinstance(exc, OSError)  # disk or network trouble below the registry client


def _not_registered(exc: BaseException) -> bool:
    """The registry holds no such model, alias or version yet: registering it fixes this, no redeploy."""
    from .registry import ModelNotRegistered
    return isinstance(exc, ModelNotRegistered)


async def _in_daemon_thread(fn: Callable[[], Any]) -> Any:
    """fn() in a daemon thread: unlike asyncio.to_thread, interpreter shutdown does not wait for it."""
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(setter, value):
        if not future.done():
            setter(value)

    def hand_over(setter, value):
        try:
            loop.call_soon_threadsafe(deliver, setter, value)
        except RuntimeError:
            pass  # the loop closed (shutdown): nobody is waiting any more

    def run():
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 - handed to the awaiting task
            hand_over(future.set_exception, exc)
        else:
            hand_over(future.set_result, result)

    threading.Thread(target=run, name="route-model-load", daemon=True).start()
    return await future


class MorlSolver:
    name = "morl"

    def __init__(self, settings: SolverSettings, *, data_contracts_version: str | None = None,
                 registry_transport=None):
        if data_contracts_version is None:
            from public_api.data_contracts import CONTRACT_VERSION as data_contracts_version
        self.settings = settings
        self.data_contracts_version = data_contracts_version
        self.state = "loading"            # loading | ready | rejected | unavailable
        self._loaded: _Loaded | None = None
        self._task: asyncio.Task | None = None
        self._recheck: asyncio.Task | None = None
        self._graph: str | None = None    # the active graph, from the latest request or readiness check
        self._reported: set[tuple[str, str]] = set()  # (package, graph) rejections already logged
        self._stop = threading.Event()
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
                                transport=self._registry_transport, stop=self._stop)
        resolved = client.resolve(s.model_name, s.model_selector, s.model_cache_dir)
        return load_package(resolved.directory, resolved.name, resolved.version, self.data_contracts_version)

    async def _attempt_load(self) -> tuple[_Loaded | None, BaseException | None]:
        started = time.perf_counter()
        try:
            loaded = await _in_daemon_thread(self._load)
        except Exception as exc:  # noqa: BLE001 - any failure leaves the baseline fallback in place
            return None, exc
        upstream = loaded.manifest.get("lineage", {}).get("upstream_model")
        logger.info("route_model_load outcome=ok model=%s package_sha256=%s package_version=%s policy_family=%s "
                    "graph_version=%s mode=%s upstream_model=%s (recorded, not checked) load_ms=%.0f",
                    loaded.identity, loaded.package_sha256, loaded.manifest.get("model_version"), loaded.family,
                    loaded.manifest["contract"].get("graph_version"), loaded.mode, upstream,
                    (time.perf_counter() - started) * 1000)
        return loaded, None

    def _log_failure(self, exc: BaseException, retry: bool) -> None:
        logger.warning("route_model_load outcome=failed model=%s@%s error=%s: %s action=%s", self.settings.model_name,
                       self.settings.model_selector, type(exc).__name__, exc,
                       "retry" if retry else "stop (needs a registry change and a redeploy)")

    def _log_waiting(self, exc: BaseException, first: bool) -> None:
        """The model is not registered yet. Logged as a warning once, then per check at info."""
        logger.log(logging.WARNING if first else logging.INFO,
                   "route_model_load outcome=waiting model=%s@%s reason=not_registered (%s) action=check again in "
                   "%.0f s; routes use the A* fallback (model_unavailable) until it is registered, no restart needed",
                   self.settings.model_name, self.settings.model_selector, exc, self.settings.model_recheck_s)

    async def load_once(self) -> bool:
        """One load attempt; True when the policy is ready. Failures are logged, never raised."""
        loaded, exc = await self._attempt_load()
        if loaded is None:
            self.state = "unavailable"
            if _not_registered(exc):
                self._log_waiting(exc, first=True)
            else:
                self._log_failure(exc, _retryable(exc))
            return False
        self._loaded, self.state = loaded, "ready"
        return True

    async def _load_until_ready(self) -> None:
        """Load, retrying what can fix itself. Three outcomes per failed attempt:
        not registered yet   wait model_recheck_s (slow: each check may wake a sleeping registry) and ask again
        retryable            timeouts and 5xx while the registry wakes: backoff from model_retry_initial_s
        anything else        stop; the registry or the package has to change
        """
        delay = self.settings.model_retry_initial_s
        waiting = False
        while True:
            loaded, exc = await self._attempt_load()
            if loaded is not None:
                self._loaded, self.state = loaded, "ready"
                return
            self.state = "unavailable"
            if _not_registered(exc) and not self._stop.is_set():
                self._log_waiting(exc, first=not waiting)
                waiting, delay = True, self.settings.model_retry_initial_s
                await asyncio.sleep(self.settings.model_recheck_s)
                continue
            retry = _retryable(exc) and not self._stop.is_set()
            self._log_failure(exc, retry)
            if not retry:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, self.settings.model_retry_max_s)

    async def _recheck_while_rejected(self) -> None:
        """Resolve the alias again, at once and then every model_recheck_s, until the registry holds a
        package the active graph accepts (or the graph returns to the loaded package's)."""
        while self.state == "rejected" and not self._stop.is_set():
            graph = self._graph
            loaded, exc = await self._attempt_load()
            if loaded is None:
                self._log_failure(exc, True)
            elif graph is not None:
                try:
                    require_compatible(loaded.manifest, self._runtime(graph))
                except PackageIncompatible as incompatible:
                    logger.info("route_model_recheck model=%s graph_version=%s: no package for this graph yet (%s); "
                                "next check in %.0f s", loaded.identity, graph, "; ".join(incompatible.reasons),
                                self.settings.model_recheck_s)
                else:
                    if self.state == "rejected":
                        self._loaded, self.state = loaded, "ready"
                        logger.info("route_model_reload outcome=ok model=%s graph_version=%s", loaded.identity, graph)
                    return
            await asyncio.sleep(self.settings.model_recheck_s)

    async def start(self) -> None:
        """Load in the background; requests are answered by the fallback until it is ready."""
        if self._task is None:
            self._task = asyncio.create_task(self._load_until_ready(), name="route-model-load")

    async def close(self) -> None:
        self._stop.set()  # ends registry retries and their waits in the load thread
        for task in (self._task, self._recheck):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._task = self._recheck = None

    def observe_graph(self, graph_version: str) -> None:
        """The active graph, from the readiness check: a package it rejects is reported (and re-checked)
        before any request arrives."""
        self._graph = graph_version
        if self._loaded is not None:
            try:
                self._compatibility(self._loaded, graph_version)
            except PackageIncompatible:
                pass  # recorded by _compatibility

    def describe(self) -> dict[str, Any]:
        """Which policy this solver serves (or tried to), for fallback metadata and readiness. No error text.
        member_alpha, inference_ms and compatibility describe a served route, so they are null here."""
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

    def _runtime(self, graph_version: str) -> dict:
        from route_model.morl.packaging import runtime_contract
        return runtime_contract(graph_version, self.data_contracts_version)

    def _compatibility(self, loaded: _Loaded, graph_version: str) -> Compatibility:
        """Compatibility with this graph; raises PackageIncompatible, after marking the solver rejected."""
        result = loaded.compatibility.get(graph_version)
        if result is None:
            try:
                result = require_compatible(loaded.manifest, self._runtime(graph_version))
            except PackageIncompatible as exc:
                self._reject(loaded, graph_version, exc)
                raise
            loaded.compatibility[graph_version] = result
            if result.status == "flagged":
                logger.warning("route_model_contract status=flagged model=%s reasons=%s", loaded.identity, result.reasons)
        if self.state == "rejected" and self._loaded is loaded:
            self.state = "ready"  # the graph went back to one this package was built for
        return result

    def _reject(self, loaded: _Loaded, graph_version: str, exc: PackageIncompatible) -> None:
        if self._loaded is not loaded:
            return
        self.state = "rejected"
        if (loaded.package_sha256, graph_version) not in self._reported:
            self._reported.add((loaded.package_sha256, graph_version))
            logger.warning("route_model_contract status=rejected model=%s graph_version=%s reasons=%s; "
                           "checking the registry every %.0f s", loaded.identity, graph_version, exc.reasons,
                           self.settings.model_recheck_s)
        if self._recheck is None or self._recheck.done():
            self._recheck = asyncio.create_task(self._recheck_while_rejected(), name="route-model-recheck")

    async def solve(self, request: SolveRequest) -> SolverReply:
        entered = time.perf_counter()
        loaded = self._loaded
        if loaded is None:
            raise SolverError(SOLVER_UNREACHABLE, f"MORL policy not loaded (state {self.state})")
        si = request.solver_input
        self._graph = si.source.graph_version
        try:
            compatibility = self._compatibility(loaded, si.source.graph_version)
        except PackageIncompatible as exc:
            raise SolverError(SOLVER_UNREACHABLE, f"MORL package rejected: {exc}") from exc
        if loaded.mode and si.mode.value != loaded.mode:
            raise SolverError(SOLVER_UNREACHABLE, f"MORL policy was trained for mode {loaded.mode}, not {si.mode.value}")
        async with self._slots:
            budget_ms = request.deadline_ms - (time.perf_counter() - entered) * 1000
            if budget_ms <= 0:
                raise SolverError(SOLVER_TIMEOUT, "No time left after waiting for a decode slot")
            token = CancelToken()
            task = asyncio.ensure_future(asyncio.to_thread(_decode, token, loaded, compatibility, request, budget_ms))
            try:
                return await asyncio.shield(task)
            finally:
                if not task.done():
                    # Cancelled (deadline, client gone): stop the decode at its next step and keep
                    # the slot until it has, so no more than model_concurrency decodes run.
                    token.cancel()
                    await asyncio.gather(task, return_exceptions=True)


def _decode(token: CancelToken, loaded: _Loaded, compatibility: Compatibility, request: SolveRequest,
            budget_ms: float) -> SolverReply:
    from route_model.morl.infer import PolicyFailure

    si = request.solver_input
    started = time.perf_counter()
    router = SubgraphRouter(si, token)
    path, _, _ = router.search(si.origin.node_id, si.destination.node_id)
    if path is None:
        return SolverReply("no_route", "policy", False, None, loaded.identity)
    fastest = tuple(edge_id for edge_id, _ in path)
    exposure = {e.edge_id: e.exposure_index for e in si.edges}
    decode_started = time.perf_counter()
    remaining_ms = budget_ms - (decode_started - started) * 1000
    try:
        member, route = loaded.decode(router, exposure, si.origin.node_id, si.destination.node_id,
                                      (si.preference.alpha, si.preference.beta), timeout_ms=max(remaining_ms, 1.0),
                                      cancel_check=token.check)
    except PolicyFailure as failure:
        reason = SOLVER_TIMEOUT if failure.reason == "timeout" else SOLVER_NO_ROUTE
        raise SolverError(reason, f"MORL policy {failure.reason}: {failure}") from failure
    details = {
        "name": loaded.name, "version": loaded.version, "package_sha256": loaded.package_sha256,
        "package_version": loaded.manifest.get("model_version"), "policy_family": loaded.family,
        "status": "ready", "member_alpha": member,
        # The policy's own work (its reference search is the one above, already done).
        "inference_ms": round((time.perf_counter() - decode_started) * 1000, 1), "compatibility": compatibility.status,
    }
    return SolverReply("ok", "policy", True, None, loaded.identity, tuple(route.edge_ids), fastest, details)
