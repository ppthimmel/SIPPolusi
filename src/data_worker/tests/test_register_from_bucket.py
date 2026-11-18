"""TI-DO-10b (#91): register a staged package through the registry's artifact proxy.

A real MLflow server runs with --serve-artifacts; its artifacts destination is a local folder that stands in for
the bucket path artifacts/. The client gets only MLFLOW_TRACKING_URI.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from spatial_model.artifacts import staged
from spatial_model.artifacts.bundles import digest
from spatial_model.artifacts.model_package import build_package, package_digest
from spatial_model.artifacts.staged import RegistrationError, register_from_bucket


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    root = tmp_path_factory.mktemp("registry")
    bucket = root / "bucket-artifacts"
    bucket.mkdir()
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AWS_", "MLFLOW_"))}
    command = [sys.executable, "-m", "mlflow", "server", "--host", "127.0.0.1", "--port", str(port),
               "--backend-store-uri", f"sqlite:///{(root / 'mlflow.db').as_posix()}", "--serve-artifacts",
               "--default-artifact-root", "mlflow-artifacts:/", "--artifacts-destination", bucket.as_uri()]
    if sys.platform != "win32":
        command += ["--workers", "1"]
    log = open(root / "server.log", "wb")
    server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env)
    uri = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90
    while True:
        try:
            urllib.request.urlopen(uri + "/health", timeout=2)
            break
        except OSError:
            if server.poll() is not None or time.monotonic() > deadline:
                server.kill()
                log.close()
                pytest.fail("MLflow server did not start:\n" + (root / "server.log").read_text(errors="ignore")[-3000:])
            time.sleep(0.5)
    yield SimpleNamespace(uri=uri, bucket=bucket)
    server.terminate()
    try:
        server.wait(timeout=15)
    except subprocess.TimeoutExpired:
        server.kill()
    log.close()


@pytest.fixture
def client(registry, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", registry.uri)
    for name in ("SPATIAL_PM25_MODEL", "SPATIAL_NO2_MODEL", "ROUTE_POLICY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    from mlflow import MlflowClient
    return MlflowClient(tracking_uri=registry.uri)


def _versions(client, name):
    return sorted(client.search_model_versions(f"name='{name}'"), key=lambda v: int(v.version))


def stage_route(bucket, prefix, seed="a"):
    folder = bucket / prefix
    (folder / "policies").mkdir(parents=True)
    (folder / "policies" / "policy_0.pt").write_bytes(f"weights-{seed}".encode())
    (folder / "set_manifest.json").write_text(json.dumps({"seed": seed}))
    build_package(folder, model_kind="route_policy", model_id="morl-route-policy", model_version=f"route-{seed}",
                  contract={"data_contracts_version": "1.1.0", "graph_version": "jakarta-test"},
                  lineage={"code_revision": "test"}, environment={}, inference={}, evaluation={})
    return folder


def stage_bundle(bucket, prefix, seed="a", fold=None):
    folder = bucket / prefix
    folder.mkdir(parents=True)
    (folder / "best.pt").write_bytes(f"stgnn-{seed}".encode())
    (folder / "stations.parquet").write_bytes(b"stations")
    (folder / "metrics.json").write_text(json.dumps({"test": {"rmse": 18.37, "n": 689}}))
    hashes = {p.name: digest(p) for p in folder.iterdir()}
    manifest = dict(format_version=1, model_family="stgnn", target="pm25", dataset="ds-v0.3.0", unit="ug/m3",
                    metadata={"seed": 42, "fold": fold}, snapshot_hashes={}, code_revision="test", python="3.11",
                    config_hashes={}, libraries={}, source_hashes={}, hashes=hashes)
    (folder / "bundle.json").write_text(json.dumps(manifest))
    return folder


def test_route_package_registers_once_with_candidate_only(registry, client):
    folder = stage_route(registry.bucket, "incoming/route/r1")
    result = register_from_bucket("incoming/route/r1/", wait=10)

    assert result["status"] == "registered" and result["aliases"] == ["candidate"]
    version = client.get_model_version("morl-route-policy", result["version"])
    assert version.tags["package_sha256"] == package_digest(folder)
    model = client.get_registered_model("morl-route-policy")
    assert model.aliases == {"candidate": result["version"]}  # never production

    again = register_from_bucket("incoming/route/r1", wait=10)
    assert again["status"] == "already_registered" and again["version"] == result["version"]
    assert len(_versions(client, "morl-route-policy")) == 1


def test_spatial_bundle_is_packaged_registered_and_not_duplicated(registry, client, capsys):
    folder = stage_bundle(registry.bucket, "incoming/spatial/pm25/b1")
    result = register_from_bucket("incoming/spatial/pm25/b1/", wait=10)

    assert result["status"] == "registered" and result["name"] == "spatial-downscaling-pm25"
    version = client.get_model_version("spatial-downscaling-pm25", result["version"])
    assert version.tags["bundle_sha256"] == digest(folder / "bundle.json") == result["bundle_sha256"]
    assert version.tags["package_sha256"] == result["package_sha256"]
    assert not (folder / "package.json").exists()  # packaged in the job's copy, the staged folder is untouched
    out = capsys.readouterr().out
    assert "package.json written here" in out and "RESULT: PASS" in out

    # Packaging again gives another package.json; the bundle hash still identifies the repeat.
    again = register_from_bucket("incoming/spatial/pm25/b1/", wait=10)
    assert again["status"] == "already_registered"
    assert len(_versions(client, "spatial-downscaling-pm25")) == 1


@pytest.mark.parametrize("damage,message", [
    (lambda f: (f / "policies" / "policy_0.pt").write_bytes(b"changed"), "Package file changed"),
    (lambda f: (f / "notes.txt").write_text("extra"), "does not list"),
    (lambda f: (f / "package.json").unlink(), "Neither package.json nor bundle.json"),
])
def test_damaged_route_package_registers_nothing(registry, client, damage, message, request):
    prefix = f"incoming/route/bad-{request.node.callspec.id}"
    before = len(_versions(client, "morl-route-policy"))
    damage(stage_route(registry.bucket, prefix, seed=request.node.callspec.id))
    with pytest.raises(RegistrationError, match=message):
        register_from_bucket(prefix, wait=10)
    assert len(_versions(client, "morl-route-policy")) == before


def test_credentials_and_folds_are_refused(registry, client):
    folder = stage_bundle(registry.bucket, "incoming/spatial/pm25/secret", seed="s")
    (folder / "config.json").write_text(json.dumps({"password": "hunter2-not-real"}))
    with pytest.raises(RegistrationError, match="credentials"):
        register_from_bucket("incoming/spatial/pm25/secret", wait=10)

    stage_bundle(registry.bucket, "incoming/spatial/pm25/fold", seed="f", fold=3)
    with pytest.raises(RegistrationError, match="LOSO fold"):
        register_from_bucket("incoming/spatial/pm25/fold", wait=10)

    with pytest.raises(RegistrationError, match="Nothing under"):
        register_from_bucket("incoming/does-not-exist/", wait=10)


def test_failure_after_the_version_exists_rolls_it_back(registry, client, monkeypatch):
    stage_route(registry.bucket, "incoming/route/rollback-good", seed="rollback-good")
    previous = register_from_bucket("incoming/route/rollback-good", wait=10)["version"]
    stage_route(registry.bucket, "incoming/route/rollback", seed="rollback")
    before = len(_versions(client, "morl-route-policy"))

    def broken(info, version):
        raise ValueError("resolved package differs")
    monkeypatch.setattr(staged, "_verify_registered", broken)
    with pytest.raises(RegistrationError, match="did not complete"):
        register_from_bucket("incoming/route/rollback", wait=10)
    assert len(_versions(client, "morl-route-policy")) == before
    assert client.get_registered_model("morl-route-policy").aliases["candidate"] == previous


def test_client_drops_object_storage_keys(registry, client, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "not-a-real-key")
    monkeypatch.setenv("MLFLOW_S3_ENDPOINT_URL", "http://example.invalid")
    stage_route(registry.bucket, "incoming/route/keys", seed="keys")
    assert register_from_bucket("incoming/route/keys", wait=10)["status"] == "registered"
    assert "AWS_ACCESS_KEY_ID" not in os.environ and "MLFLOW_S3_ENDPOINT_URL" not in os.environ


@pytest.mark.parametrize("prefix", ["", "/", "../x", "incoming/../x", "a//b"])
def test_bad_prefixes(prefix):
    with pytest.raises(RegistrationError, match="Invalid prefix"):
        staged.normalise_prefix(prefix)


def test_folder_placeholders_from_the_files_tab_are_skipped(tmp_path, monkeypatch):
    """S3 lists a 0-byte 'folder/' object as a file named like the folder; it is not part of the package."""
    entries = {None: [SimpleNamespace(path="pkg", is_dir=False, file_size=None),  # as the proxy returns it
                      SimpleNamespace(path="package.json", is_dir=False, file_size=10),
                      SimpleNamespace(path="sub", is_dir=True, file_size=None)],
               "sub": [SimpleNamespace(path="sub/sub", is_dir=False, file_size=0),
                       SimpleNamespace(path="sub/w.pt", is_dir=False, file_size=3)]}
    fetched = []

    class Repository:
        def list_artifacts(self, path=None):
            return entries[path]

        def download_artifacts(self, path, dst_path):
            fetched.append(path)
            (Path(dst_path) / path).write_text("x")

    import mlflow.store.artifact.artifact_repository_registry as registry_module
    monkeypatch.setattr(registry_module, "get_artifact_repository", lambda uri: Repository())
    assert staged.download_staged("incoming/pkg", tmp_path) == 2
    assert fetched == ["package.json", "sub/w.pt"]


def test_promote_takes_only_the_current_candidate(registry, client, monkeypatch, capsys):
    from spatial_model.training.__main__ import main

    first = register_from_bucket(_staged(registry, "incoming/route/p1", "p1"), wait=10)["version"]
    monkeypatch.delenv("REGISTER_PREFIX", raising=False)
    monkeypatch.setenv("PROMOTE", f"route:{first}")
    main(["railway-job"])
    assert client.get_registered_model("morl-route-policy").aliases["production"] == first
    main(["railway-job"])  # the variable left on the job: a rerun changes nothing
    assert "already production" in capsys.readouterr().out

    # Version two was never promoted and version three has since become the candidate: PROMOTE=two is stale.
    second = register_from_bucket(_staged(registry, "incoming/route/p2", "p2"), wait=10)["version"]
    third = register_from_bucket(_staged(registry, "incoming/route/p3", "p3"), wait=10)["version"]
    assert client.get_registered_model("morl-route-policy").aliases["candidate"] == third
    monkeypatch.setenv("PROMOTE", f"route:{second}")
    with pytest.raises(SystemExit) as exit_:
        main(["railway-job"])
    assert exit_.value.code == 1 and "not the current candidate" in capsys.readouterr().err
    assert client.get_registered_model("morl-route-policy").aliases["production"] == first


def test_local_folder_registers_then_promotes(registry, client, tmp_path):
    """The local compose job (register-dir --promote): same checks, then production; reruns change nothing."""
    from spatial_model.artifacts.staged import register_directory

    folder = stage_route(tmp_path, "route-local", seed="local")
    before = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
    first = register_directory(folder, promote=True, wait=10)
    assert first["status"] == "registered" and first["promotion"] == "promoted"
    assert client.get_registered_model("morl-route-policy").aliases["production"] == first["version"]
    again = register_directory(folder, promote=True, wait=10)
    assert again["status"] == "already_registered" and again["promotion"] == "skipped"
    assert "production" in again["aliases"]
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")) == before

    stage_bundle(tmp_path, "bundle-without-package", seed="local-b")
    result = register_directory(tmp_path / "bundle-without-package", wait=10)
    assert result["status"] == "registered" and "promotion" not in result
    assert not (tmp_path / "bundle-without-package" / "package.json").exists()  # packaged in the copy


def test_rerun_with_two_route_packages_keeps_aliases(registry, client, tmp_path, capsys):
    """The TI-AI-09 set in /out next to PIC A in .local-models: the second run must succeed and change nothing."""
    from spatial_model.artifacts.staged import register_directory

    older = stage_route(tmp_path, "out-route", seed="two-older")
    newer = stage_route(tmp_path, "local-models-route", seed="two-newer")
    first = register_directory(older, promote=True, wait=10)["version"]
    second = register_directory(newer, promote=True, wait=10)["version"]
    aliases = client.get_registered_model("morl-route-policy").aliases
    assert aliases["candidate"] == aliases["production"] == second
    count = len(_versions(client, "morl-route-policy"))
    capsys.readouterr()

    rerun_older = register_directory(older, promote=True, wait=10)
    assert (rerun_older["status"], rerun_older["version"]) == ("already_registered", first)
    assert rerun_older["promotion"] == "skipped" and rerun_older["aliases"] == []
    assert "promotion skipped" in capsys.readouterr().out
    rerun_newer = register_directory(newer, promote=True, wait=10)
    assert rerun_newer["promotion"] == "skipped" and rerun_newer["aliases"] == ["candidate", "production"]

    assert client.get_registered_model("morl-route-policy").aliases == aliases
    assert len(_versions(client, "morl-route-policy")) == count


def test_rerun_finishes_a_promotion_that_never_happened(registry, client, tmp_path):
    """Stopped after registering: the version is the candidate without production, so the next run promotes it."""
    from spatial_model.artifacts.staged import register_directory

    folder = stage_route(tmp_path, "route-interrupted", seed="interrupted")
    version = register_directory(folder, wait=10)["version"]
    assert client.get_registered_model("morl-route-policy").aliases["production"] != version

    again = register_directory(folder, promote=True, wait=10)
    assert again["status"] == "already_registered" and again["promotion"] == "promoted"
    assert client.get_registered_model("morl-route-policy").aliases["production"] == version


def test_railway_job_packages_a_bundle_with_the_grid_spec_id(registry, client, monkeypatch, capsys, tmp_path):
    from spatial_model.artifacts.registry import resolve
    from spatial_model.training.__main__ import main

    stage_bundle(registry.bucket, "incoming/spatial/pm25/grid", seed="grid")
    monkeypatch.delenv("PROMOTE", raising=False)
    monkeypatch.setenv("REGISTER_PREFIX", "incoming/spatial/pm25/grid/")
    monkeypatch.setenv("REGISTER_GRID_SPEC_ID", "grid-test")
    main(["railway-job"])

    version = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["version"]
    root, _ = resolve("pm25", version, cache_dir=tmp_path)
    assert json.loads((root / "package.json").read_text())["contract"]["grid_spec_id"] == "grid-test"


def _staged(registry, prefix, seed):
    stage_route(registry.bucket, prefix, seed=seed)
    return prefix


@pytest.mark.parametrize("environment,code,text", [
    ({}, 0, "nothing to do"),
    ({"REGISTER_PREFIX": "incoming/x/", "PROMOTE": "route:1"}, 1, "both set"),
    ({"PROMOTE": "production"}, 1, "PROMOTE must be"),
])
def test_railway_job_needs_exactly_one_instruction(monkeypatch, capsys, environment, code, text):
    from spatial_model.training.__main__ import main
    for name in ("REGISTER_PREFIX", "PROMOTE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SystemExit) as exit_:
        main(["railway-job"])
    assert exit_.value.code == code and text in capsys.readouterr().err
