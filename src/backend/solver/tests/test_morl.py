"""TI-SE-07b: the MORL policy package in the Backend (ROUTE_SOLVER_PROVIDER=morl).

Packages are built here from small untrained policy sets with the real packaging code, so
loading, hashing, the contract gate and the registry download are the production paths.
Route quality is not tested here (TI-AI-10); a fake policy family stands in where a test
needs a policy that always reaches the destination.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

torch = pytest.importorskip("torch")

from fastapi.testclient import TestClient  # noqa: E402

from osm.app import create_app  # noqa: E402
from osm.tests.conftest import VERSION  # noqa: E402
from public_api.schemas import RouteFeatureCollection  # noqa: E402
from route_model.model_package import PackageError, package_digest, sha256_file  # noqa: E402
from route_model.morl.infer import PolicyFailure, PolicyRoute, optimal_weighted_route, save_checkpoint  # noqa: E402
from route_model.morl.features import FEATURE_VERSION  # noqa: E402
from route_model.morl.packaging import package_policy_set  # noqa: E402
from route_model.morl.policy import ActorCritic  # noqa: E402
from solver import morl  # noqa: E402
from solver.config import SolverSettings  # noqa: E402
from solver.morl import MorlSolver, SubgraphRouter  # noqa: E402
from solver.orchestrator import solve_route_request  # noqa: E402
from solver.paths import shortest_path  # noqa: E402
from solver.registry import RegistryClient, RegistryError  # noqa: E402

from .biggrid import grid_input  # noqa: E402
from .conftest import request_body  # noqa: E402
from .test_geojson import INTERNAL_HINTS, assert_valid_geojson  # noqa: E402

FAST = dict(budget_ms=4000, response_margin_ms=0, grace_ms=200)


def build_package(root: Path, graph=VERSION, mode="bike", contract="1.0.0", family=None) -> Path:
    """A packaged two-member set (alpha 0 and 1) of untrained policies."""
    set_dir, out = root / "set", root / "package"
    members = []
    for index, alpha in enumerate((0.0, 1.0)):
        run = f"members/run-a{alpha:g}"
        (set_dir / run / "checkpoints").mkdir(parents=True)
        torch.manual_seed(index)
        save_checkpoint(set_dir / run / "checkpoints/best.pt", ActorCritic())
        (set_dir / run / "manifest.json").write_text(json.dumps({
            "data": {"graph_version": graph, "fields": {"dataset": {"version": "ds-v0.3.0"}}},
            "versions": {"python": "3.12", "torch": torch.__version__}}))
        members.append({"alpha": alpha, "run_dir": run, "status": "completed", "best": {
            "checkpoint": "checkpoints/best.pt", "sha256": sha256_file(set_dir / run / "checkpoints/best.pt")}})
    (set_dir / "set_manifest.json").write_text(json.dumps({
        "set_id": "set-test", "status": "completed", "feature_version": FEATURE_VERSION, "algorithm": "ppo",
        "selection_rule": "nearest alpha", "config_sha256": "c", "code": {"commit": "abc", "dirty": False},
        "config": {"env": {"max_steps": 200, "max_steps_factor": 3.0}},
        "members": members, "data": {"graph_version": graph, "mode": mode, "od_pairs_sha256": "o"}}))
    package_policy_set(set_dir, out, data_contracts_version=contract)
    if family is not None:  # package.json is not in its own artifact list, so this keeps the hashes valid
        manifest = json.loads((out / "package.json").read_text())
        manifest["inference"]["policy_family"] = family
        (out / "package.json").write_text(json.dumps(manifest))
    return out


@pytest.fixture
def oracle_family(monkeypatch):
    """A policy family whose 'policy' is the exact optimum for the preference: always reaches the goal."""
    def loader(directory, manifest, data_contracts_version):
        def decode(router, exposure, start, end, preference, timeout_ms):
            path, _, _ = router.search(start, end)
            t_ref = sum(router.edges[e].time_s for e, _ in path)
            e_ref = sum(exposure[e] * router.edges[e].time_s for e, _ in path)
            edges, _ = optimal_weighted_route(router, exposure, start, end, preference[0], preference[1], t_ref, e_ref)
            return preference[0], PolicyRoute(tuple(edges), (), 0.0, 0.0)
        return decode
    monkeypatch.setitem(morl.LOADERS, "oracle", loader)
    return "oracle"


def morl_settings(**kw) -> SolverSettings:
    return SolverSettings(_env_file=None, provider="morl", **{**FAST, **kw})


async def ready_solver(package: Path, **kw) -> MorlSolver:
    solver = MorlSolver(morl_settings(model_dir=str(package), **kw), data_contracts_version="1.0.0")
    assert await solver.load_once()
    return solver


async def solve(solver, osm_repo, pollution_repo, settings, mode="bike", alpha=0.3):
    return await solve_route_request(
        request_body(mode=mode, alpha=alpha, beta=round(1 - alpha, 6)), osm=osm_repo, pollution=pollution_repo,
        osm_settings=settings, solver=solver, solver_settings=solver.settings, request_id="req-morl",
        received_at=datetime.now(timezone.utc))


# ------------------------------------------------------------------ subgraph router

def test_subgraph_router_offers_the_planners_arcs_in_roadrouter_order():
    si = grid_input(4, reachable=True)
    router = SubgraphRouter(si)
    for node, arcs in router.arcs.items():
        assert arcs == sorted(arcs)
    edge = si.edges[0]
    assert (edge.v, edge.travel_time_s, edge.edge_id, True) in router.arcs[edge.u]
    assert (edge.u, edge.travel_time_s, edge.edge_id, False) in router.arcs[edge.v]
    path, seconds, _ = router.search(si.origin.node_id, si.destination.node_id)
    fastest = shortest_path(si, lambda e: e.travel_time_s)
    assert len(path) == len(fastest)
    assert seconds == pytest.approx(sum(e.travel_time_s for e in si.edges if e.edge_id in fastest))


# ------------------------------------------------------------------ serving

async def test_policy_route_is_served_with_its_identity(tmp_path, osm_repo, pollution_repo, settings, oracle_family):
    package = build_package(tmp_path, family=oracle_family)
    result = await solve(await ready_solver(package), osm_repo, pollution_repo, settings)
    assert (result.solver, result.route_source, result.preference_applied, result.fallback_reason) == (
        "morl", "policy", True, None)
    assert result.model_version == "morl-route-policy:local"
    info = result.route_model
    assert info["package_sha256"] == package_digest(package) and info["policy_family"] == "oracle"
    assert (info["status"], info["member_alpha"], info["compatibility"]) == ("ready", 0.3, "compatible")
    assert info["inference_ms"] >= 0


async def test_untrained_policy_never_shows_as_a_false_success(tmp_path, osm_repo, pollution_repo, settings):
    """A real (untrained) set either reaches the goal on a valid subgraph path, or the reply is the labelled fallback."""
    solver = await ready_solver(build_package(tmp_path))
    for alpha in (0.0, 0.3, 1.0):
        result = await solve(solver, osm_repo, pollution_repo, settings, alpha=alpha)
        if result.route_source == "policy":
            assert result.route_model["member_alpha"] == (0.0 if alpha < 0.5 else 1.0)
            assert result.recommended.metrics.edge_count > 0  # traced from origin to destination by the orchestrator
        else:
            assert result.fallback_reason in ("model_no_route", "model_timeout")
            assert result.recommended.edge_ids == result.comparison.edge_ids
            assert result.route_model["status"] == "ready" and result.route_model["member_alpha"] is None


async def test_plan_metadata_follows_the_public_contract(tmp_path, osm_repo, pollution_repo, settings, oracle_family):
    solver = await ready_solver(build_package(tmp_path, family=oracle_family))
    app = create_app(repository=osm_repo, settings=settings, pollution_repository=pollution_repo, solver=solver,
                     solver_settings=solver.settings)
    with TestClient(app) as client:
        fc = client.post("/v1/routes/plan", json=request_body(mode="bike", alpha=0.3, beta=0.7)).json()
        ready = client.get("/health/ready").json()
    assert_valid_geojson(fc)
    RouteFeatureCollection.model_validate(fc)
    meta = fc["metadata"]
    assert meta["solver"] == "morl" and meta["model_version"]["route_policy"] == "morl-route-policy:local"
    assert meta["route_model"]["package_sha256"] == package_digest(tmp_path / "package")
    assert ready["checks"]["route_model"] == "ready"


@pytest.mark.parametrize("case", ["not_loaded", "other_graph", "other_mode"])
async def test_unusable_model_falls_back_labelled(tmp_path, osm_repo, pollution_repo, settings, oracle_family, case):
    if case == "not_loaded":
        solver = MorlSolver(morl_settings(model_dir=str(tmp_path / "missing")), data_contracts_version="1.0.0")
        assert not await solver.load_once()
    else:
        solver = await ready_solver(build_package(tmp_path, family=oracle_family,
                                                  graph="jakarta-other" if case == "other_graph" else VERSION))
    result = await solve(solver, osm_repo, pollution_repo, settings, mode="walk" if case == "other_mode" else "bike")
    assert (result.route_source, result.preference_applied, result.fallback_reason) == ("baseline", False, "model_unavailable")
    assert result.solver == "baseline" and result.fallback.from_solver == "morl"
    assert result.recommended.edge_ids == result.comparison.edge_ids
    assert result.route_model["status"] == ("unavailable" if case == "not_loaded" else "ready")
    assert result.route_model["member_alpha"] is None
    assert not any(hint in " ".join(result.notes) for hint in INTERNAL_HINTS)


@pytest.mark.parametrize("failure, fallback_reason", [("timeout", "model_timeout"), ("dead_end", "model_no_route"),
                                                       ("step_limit", "model_no_route")])
async def test_policy_failure_falls_back(tmp_path, monkeypatch, osm_repo, pollution_repo, settings, failure, fallback_reason):
    def loader(directory, manifest, data_contracts_version):
        def decode(*args, **kwargs):
            raise PolicyFailure(failure, "injected")
        return decode
    monkeypatch.setitem(morl.LOADERS, "failing", loader)
    solver = await ready_solver(build_package(tmp_path, family="failing"))
    result = await solve(solver, osm_repo, pollution_repo, settings)
    assert (result.route_source, result.fallback_reason) == ("baseline", fallback_reason)


@pytest.mark.parametrize("case", ["changed_file", "other_contract_major", "unknown_family"])
async def test_package_that_must_not_load(tmp_path, case):
    package = build_package(tmp_path, contract="2.0.0" if case == "other_contract_major" else "1.0.0",
                            family="preference_conditioned_v9" if case == "unknown_family" else None)
    if case == "changed_file":
        checkpoint = next(package.rglob("best.pt"))
        checkpoint.write_bytes(checkpoint.read_bytes() + b"\0")
    solver = MorlSolver(morl_settings(model_dir=str(package)), data_contracts_version="1.0.0")
    assert not await solver.load_once()
    assert solver.state == "unavailable" and solver.describe()["package_sha256"] is None


async def test_start_does_not_wait_for_the_model(tmp_path):
    solver = MorlSolver(morl_settings(model_dir=str(build_package(tmp_path))), data_contracts_version="1.0.0")
    await solver.start()
    assert solver.state == "loading"
    await asyncio.wait_for(solver._task, 30)
    assert solver.state == "ready"
    await solver.close()


async def test_failed_load_is_retried(tmp_path, monkeypatch):
    package = build_package(tmp_path)
    real = MorlSolver._load
    attempts = []

    def flaky(self):
        attempts.append(1)
        if len(attempts) < 3:
            raise RegistryError("registry asleep")
        return real(self)
    monkeypatch.setattr(MorlSolver, "_load", flaky)
    solver = MorlSolver(morl_settings(model_dir=str(package), model_retry_initial_s=0.01, model_retry_max_s=0.02),
                        data_contracts_version="1.0.0")
    await solver.start()
    await asyncio.wait_for(solver._task, 30)
    assert solver.state == "ready" and len(attempts) == 3


def test_morl_needs_a_registry_or_a_package():
    with pytest.raises(ValueError, match="MLFLOW_TRACKING_URI"):
        SolverSettings(_env_file=None, provider="morl")
    assert SolverSettings(_env_file=None, provider="morl", registry_url="http://r:5000").registry_url == "http://r:5000"


def test_registry_url_comes_from_mlflow_tracking_uri(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://modelregistry.railway.internal:5000")
    assert SolverSettings(_env_file=None, provider="morl").registry_url == "http://modelregistry.railway.internal:5000"


# ------------------------------------------------------------------ registry

class FakeRegistry:
    """MLflow REST and artifact proxy over one package directory."""

    def __init__(self, package: Path, tag=None, source="mlflow-artifacts:/2/run123/artifacts/package", fail_first=0):
        self.package, self.source, self.fail_first = package, source, fail_first
        self.tag = package_digest(package) if tag is None else tag
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if self.fail_first:
            self.fail_first -= 1
            return httpx.Response(503)
        path = request.url.path
        if path == "/api/2.0/mlflow/registered-models/alias":
            assert request.url.params["name"] == "morl-route-policy"
            if request.url.params["alias"] != "production":
                return httpx.Response(404, json={"error_code": "RESOURCE_DOES_NOT_EXIST"})
            tags = [{"key": "package_sha256", "value": self.tag}] if self.tag else []
            return httpx.Response(200, json={"model_version": {"name": "morl-route-policy", "version": "1",
                                                               "source": self.source, "tags": tags}})
        prefix = "/api/2.0/mlflow-artifacts/artifacts"
        if path == prefix:
            directory = self.package / request.url.params["path"].removeprefix("2/run123/artifacts/package").strip("/")
            return httpx.Response(200, json={"files": [
                {"path": p.name, "is_dir": p.is_dir(), **({} if p.is_dir() else {"file_size": p.stat().st_size})}
                for p in sorted(directory.iterdir())]})
        relative = path.removeprefix(prefix + "/2/run123/artifacts/package/")
        return httpx.Response(200, content=(self.package / relative).read_bytes())

    def client(self, **kw) -> RegistryClient:
        return RegistryClient("http://registry:5000", backoff_s=0, transport=httpx.MockTransport(self.handler), **kw)


def test_registry_download_is_verified_and_cached(tmp_path):
    package = build_package(tmp_path / "src")
    registry = FakeRegistry(package)
    resolved = registry.client().resolve("morl-route-policy", "production", tmp_path / "cache")
    assert resolved.identity == "morl-route-policy:1" and resolved.package_sha256 == package_digest(package)
    assert resolved.directory == tmp_path / "cache/morl-route-policy/1" / package_digest(package)
    assert sorted(p.relative_to(resolved.directory) for p in resolved.directory.rglob("*")) == sorted(
        p.relative_to(package) for p in package.rglob("*"))
    downloads = len(registry.requests)
    again = registry.client().resolve("morl-route-policy", "production", tmp_path / "cache")
    assert again.directory == resolved.directory and len(registry.requests) == downloads + 1  # alias lookup only


@pytest.mark.parametrize("case, error", [
    ("wrong_tag", PackageError), ("no_tag", RegistryError), ("s3_source", RegistryError), ("unknown_alias", RegistryError)])
def test_registry_refuses_what_it_cannot_verify(tmp_path, case, error):
    package = build_package(tmp_path / "src")
    registry = FakeRegistry(package, tag={"wrong_tag": "0" * 64, "no_tag": ""}.get(case),
                            source="s3://bucket/2/run123/artifacts/package" if case == "s3_source"
                            else "mlflow-artifacts:/2/run123/artifacts/package")
    with pytest.raises(error):
        registry.client().resolve("morl-route-policy", "staging" if case == "unknown_alias" else "production",
                                  tmp_path / "cache")
    assert not list((tmp_path / "cache").rglob("package.json"))  # nothing partial or unverified is cached


def test_registry_retries_a_waking_registry_but_not_forever(tmp_path):
    package = build_package(tmp_path / "src")
    assert FakeRegistry(package, fail_first=2).client(attempts=3).resolve(
        "morl-route-policy", "production", tmp_path / "a").version == "1"
    with pytest.raises(RegistryError, match="503"):
        FakeRegistry(package, fail_first=5).client(attempts=3).resolve("morl-route-policy", "production", tmp_path / "b")


def test_cached_package_altered_on_disk_is_discarded(tmp_path):
    package = build_package(tmp_path / "src")
    resolved = FakeRegistry(package).client().resolve("morl-route-policy", "production", tmp_path / "cache")
    (resolved.directory / "od_pairs.json").write_text("{}tampered")
    with pytest.raises(PackageError):
        FakeRegistry(package).client().resolve("morl-route-policy", "production", tmp_path / "cache")
    assert not resolved.directory.exists()
    assert FakeRegistry(package).client().resolve("morl-route-policy", "production", tmp_path / "cache").directory.exists()


async def test_solver_loads_from_the_registry(tmp_path, osm_repo, pollution_repo, settings, oracle_family):
    package = build_package(tmp_path / "src", family=oracle_family)
    registry = FakeRegistry(package)
    solver = MorlSolver(morl_settings(registry_url="http://registry:5000", model_cache_dir=str(tmp_path / "cache"),
                                      registry_attempts=1),
                        data_contracts_version="1.0.0", registry_transport=httpx.MockTransport(registry.handler))
    assert await solver.load_once()
    result = await solve(solver, osm_repo, pollution_repo, settings)
    assert result.route_source == "policy" and result.model_version == "morl-route-policy:1"
    assert result.route_model["version"] == "1" and result.route_model["package_sha256"] == package_digest(package)
