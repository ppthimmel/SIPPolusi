"""Register a model package staged in the registry's bucket (TI-DO-10b, #91).

Run inside Railway's private network (a one-off job built from the Data Worker image), since the registry has
no public domain:

    python -m spatial_model.training register-from-bucket incoming/spatial/pm25/<model_version>/

The prefix is relative to the registry's artifact root, i.e. the bucket path ``artifacts/<prefix>``. The files are
read through the registry's artifact proxy (``mlflow-artifacts:/``), so this client needs only
``MLFLOW_TRACKING_URI`` and never holds object-storage keys.

Steps: wait for the registry, download, verify (bundle.json and package.json hashes, no unlisted file, no
credential), write package.json for a spatial bundle that has none, skip a package that is already registered,
register it, resolve the new version again and compare its hashes, and only then give it the alias ``candidate``. A
failure after the version was created deletes that version, so no half-registered version is left behind and
``candidate`` stays on the previous version. ``production`` is never set here; that is ``promote``.
"""
from __future__ import annotations

import os
import posixpath
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from spatial_model.artifacts.bundles import digest, serving_family, validate_bundle
from spatial_model.configs import SERVING_FAMILIES
from spatial_model.artifacts.model_package import (PACKAGE_FILE, PackageError, load_manifest, package_digest,
                                                   validate_package)
from spatial_model.artifacts.registry import NAMES, ROUTE_NAME

#: Variables that would give this client direct access to the bucket. Removed before any MLflow call, so every
#: upload and download goes through the registry's artifact proxy.
S3_VARIABLES = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION",
                "MLFLOW_S3_ENDPOINT_URL")
EXPERIMENTS = {"spatial_downscaling": "spatial-downscaling", "route_policy": "route-policy"}


class RegistrationError(RuntimeError):
    """The staged package was refused, or registration did not complete (nothing is left registered)."""


def _say(message):
    print(message, flush=True)


def normalise_prefix(prefix) -> str:
    """'incoming/x/' -> 'incoming/x'. Relative to the artifact root; no '..', no empty path."""
    raw = str(prefix or "").strip()
    if raw.startswith("mlflow-artifacts:"):
        raw = raw[len("mlflow-artifacts:"):]
    path = raw.strip("/")
    parts = path.split("/")
    if not path or any(part in ("", ".", "..") for part in parts):
        raise RegistrationError(f"Invalid prefix {prefix!r}: give a folder relative to the registry's artifact root, "
                                "e.g. incoming/spatial/pm25/<model_version>/")
    return path


def drop_s3_credentials() -> list[str]:
    """Remove object-storage variables from this process; returns the names that were set."""
    found = [name for name in S3_VARIABLES if os.environ.get(name)]
    for name in S3_VARIABLES:
        os.environ.pop(name, None)
    return found


def wait_for_registry(tracking_uri, timeout=120.0, interval=5.0):
    """Poll GET /health until 200. A sleeping Railway service wakes on private-network traffic in 10-30 s."""
    url = tracking_uri.rstrip("/") + "/health"
    deadline = time.monotonic() + timeout
    last = None
    while True:
        try:
            with urllib.request.urlopen(url, timeout=min(30.0, max(1.0, timeout))) as response:
                if response.status == 200:
                    return
                last = f"HTTP {response.status}"
        except (urllib.error.URLError, OSError) as exc:
            last = str(getattr(exc, "reason", exc))
        if time.monotonic() >= deadline:
            raise RegistrationError(f"Registry not reachable at {url} after {timeout:.0f} s ({last}). Is "
                                    "MLFLOW_TRACKING_URI the private address, and is the service deployed?")
        time.sleep(interval)


def download_staged(prefix, destination) -> int:
    """Copy every file under the prefix, through the artifact proxy, into destination. Returns the file count.

    A 0-byte object named like its own folder is the placeholder the bucket's Files tab makes for a folder;
    it is skipped.
    """
    from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository

    repository = get_artifact_repository("mlflow-artifacts:/" + prefix)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    count = 0

    def walk(relative):
        nonlocal count
        here = posixpath.basename(relative) if relative else posixpath.basename(prefix)
        for entry in repository.list_artifacts(relative or None):
            name = posixpath.basename(entry.path)
            if entry.is_dir:
                walk(entry.path)
            elif not entry.file_size and name == here:  # the proxy omits file_size 0 (protobuf default): None
                continue
            else:
                target = (destination / entry.path).resolve()
                if not target.is_relative_to(destination.resolve()):
                    raise RegistrationError(f"Staged file outside the prefix: {entry.path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                repository.download_artifacts(entry.path, dst_path=str(destination))
                count += 1

    walk("")
    return count


def inspect_package(root, grid_spec_id=None) -> dict:
    """Validate a downloaded package and describe what would be registered. Raises RegistrationError."""
    root = Path(root)
    has_package, has_bundle = (root / PACKAGE_FILE).is_file(), (root / "bundle.json").is_file()
    if not has_package and not has_bundle:
        raise RegistrationError("Neither package.json nor bundle.json at the top of the folder; point the prefix or folder "
                                "at the package itself")
    try:
        kind = load_manifest(root)["model_kind"] if has_package else "spatial_downscaling"
        if kind == "spatial_downscaling":
            if not has_bundle:
                raise RegistrationError("Package refused: a spatial_downscaling package needs bundle.json")
            return _inspect_spatial(root, has_package, grid_spec_id)
        if kind != "route_policy":
            raise RegistrationError(f"Package refused: unknown model_kind {kind!r}")
        if grid_spec_id:
            raise RegistrationError("--grid-spec-id applies only to a spatial bundle")
        manifest = validate_package(root, "route_policy")
    except (PackageError, ValueError, KeyError) as exc:
        if isinstance(exc, RegistrationError):
            raise
        raise RegistrationError(f"Package refused: {exc}") from exc
    if manifest["model_id"] != ROUTE_NAME:
        raise RegistrationError(f"Package refused: model_id {manifest['model_id']!r} is not {ROUTE_NAME}")
    return {"kind": "route_policy", "name": os.environ.get("ROUTE_POLICY_MODEL", ROUTE_NAME),
            "model_version": manifest["model_version"], "files": len(manifest["artifacts"]),
            "tags": {"package_sha256": package_digest(root)}, "packaged_here": False}


def _inspect_spatial(root, has_package, grid_spec_id):
    bundle = validate_bundle(root)
    target = bundle["target"]
    if serving_family(bundle) not in SERVING_FAMILIES[target]:
        raise RegistrationError(f"Package refused: model family {serving_family(bundle)!r} is not a serving "
                                f"family for {target} ({', '.join(SERVING_FAMILIES[target])})")
    if bundle["metadata"].get("fold") is not None:
        raise RegistrationError("Package refused: a LOSO fold checkpoint cannot be registered as a serving model")
    if not has_package:
        from spatial_model.artifacts.packaging import package_bundle
        package_bundle(root, grid_spec_id=grid_spec_id)  # scans for credentials before writing
    elif grid_spec_id:
        raise RegistrationError("--grid-spec-id applies only to a bundle without package.json; this one is "
                                "already packaged")
    manifest = validate_package(root, "spatial_downscaling")
    if manifest["model_id"] != NAMES[target]:
        raise RegistrationError(f"Package refused: model_id {manifest['model_id']!r} does not match the bundle "
                                f"target {target}")
    return {"kind": "spatial_downscaling", "target": target,
            "name": os.environ.get(f"SPATIAL_{target.upper()}_MODEL", NAMES[target]),
            "model_version": manifest["model_version"], "files": len(manifest["artifacts"]),
            "tags": {"bundle_sha256": digest(root / "bundle.json"), "package_sha256": package_digest(root)},
            "packaged_here": not has_package}


def find_registered(client, info):
    """The existing version holding this model: same package hash, or for a spatial bundle the same bundle hash
    (packaging a bundle again gives a new package.json, so its hash alone would not catch a repeat)."""
    for version in sorted(client.search_model_versions(f"name='{info['name']}'"), key=lambda v: int(v.version)):
        if any(version.tags.get(key) == value for key, value in info["tags"].items()):
            return version
    return None


def _check_proxied(client, kind):
    """Refuse to upload when the experiment would write straight to object storage (needs S3 keys here)."""
    experiment = client.get_experiment_by_name(EXPERIMENTS[kind])
    if experiment is None:
        experiment = client.get_experiment(client.create_experiment(EXPERIMENTS[kind]))
    if not experiment.artifact_location.startswith("mlflow-artifacts:"):
        raise RegistrationError(
            f"Experiment {experiment.name!r} stores artifacts at {experiment.artifact_location!r}, not through the "
            "registry's proxy (mlflow-artifacts:/). Registering would need S3 keys on this client; refused.")


def _verify_registered(info, version):
    """Download the new version through the registry and check it, as a serving client would."""
    from spatial_model.artifacts.registry import resolve, resolve_route_package

    with tempfile.TemporaryDirectory() as cache:
        if info["kind"] == "route_policy":
            root, identity = resolve_route_package(str(version), cache_dir=cache)
        else:
            root, identity = resolve(info["target"], str(version), cache_dir=cache)
        for key, value in info["tags"].items():
            actual = package_digest(root) if key == "package_sha256" else digest(Path(root) / "bundle.json")
            if actual != value:
                raise RegistrationError(f"Resolved {identity} has another {key}")
        return identity, sum(1 for p in Path(root).rglob("*") if p.is_file() and p.name != PACKAGE_FILE)


def _delete_created(client, info, before):
    """Delete versions of this package created by this run (none existed before it)."""
    found = find_registered(client, info)
    while found is not None and int(found.version) > before:
        client.delete_model_version(info["name"], found.version)
        _say(f"rolled back: deleted {info['name']} version {found.version}")
        found = find_registered(client, info)


def promote_candidate(selection, *, wait=120.0) -> dict:
    """PROMOTE=<pm25|no2|route>:<version>: give that version production, if it is the current candidate.

    The guard makes a value left on the job harmless: once the version is production a rerun changes nothing, and
    after a newer registration (candidate moved on) the old value is refused instead of moving production back.
    """
    target, _, version = str(selection or "").partition(":")
    if target not in ("pm25", "no2", "route") or not version.isdigit():
        raise RegistrationError(f"PROMOTE must be <pm25|no2|route>:<version>, got {selection!r}")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise RegistrationError("MLFLOW_TRACKING_URI is required")
    drop_s3_credentials()
    if wait:
        wait_for_registry(tracking_uri, timeout=wait)
    from spatial_model.artifacts.registry import client as registry_client, promote, promote_route

    name = (os.environ.get("ROUTE_POLICY_MODEL", ROUTE_NAME) if target == "route"
            else os.environ.get(f"SPATIAL_{target.upper()}_MODEL", NAMES[target]))
    aliases = registry_client().get_registered_model(name).aliases
    if aliases.get("production") == version:
        _say(f"{name} version {version} is already production; nothing changed")
        return {"status": "already_production", "name": name, "version": version}
    if aliases.get("candidate") != version:
        raise RegistrationError(f"{name} version {version} is not the current candidate "
                                f"(candidate: {aliases.get('candidate')}); nothing promoted")
    _say(f"promoting {name} version {version} (production was {aliases.get('production')})")
    identity = promote_route(version) if target == "route" else promote(target, version)
    _say(f"promoted: {identity} is production")
    return {"status": "promoted", "name": name, "version": version, "previous": aliases.get("production")}


def _connect(wait):
    """Check MLFLOW_TRACKING_URI, drop S3 keys from this process, wait for the registry."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        raise RegistrationError("MLFLOW_TRACKING_URI is required (on Railway: http://modelregistry.railway.internal:5000)")
    dropped = drop_s3_credentials()
    if dropped:
        _say("ignoring object-storage variables on this client: " + ", ".join(dropped))
    os.environ.setdefault("GIT_PYTHON_REFRESH", "quiet")  # the image has no git; MLflow would warn on every run
    if wait:
        _say(f"waiting for the registry at {tracking_uri} (up to {wait:.0f} s)")
        wait_for_registry(tracking_uri, timeout=wait)


def register_from_bucket(prefix, *, grid_spec_id=None, wait=120.0, workdir=None) -> dict:
    """Download, verify, and register the package under prefix. Returns a summary; raises RegistrationError."""
    prefix = normalise_prefix(prefix)
    _connect(wait)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        root = Path(tmp) / "package"
        from mlflow.exceptions import MlflowException
        try:
            count = download_staged(prefix, root)
        except MlflowException as exc:
            raise RegistrationError(f"Could not read mlflow-artifacts:/{prefix} through the registry: {exc}") from exc
        if not count:
            raise RegistrationError(f"Nothing under mlflow-artifacts:/{prefix} (bucket path artifacts/{prefix}/). "
                                    "Upload the package folder there first.")
        _say(f"downloaded {count} files from mlflow-artifacts:/{prefix}")
        return register_package_root(root, grid_spec_id=grid_spec_id)


def register_directory(directory, *, grid_spec_id=None, promote=False, wait=120.0, workdir=None) -> dict:
    """Same as register_from_bucket for a local folder (the local docker compose job register_models).

    The folder is copied first, so packaging a bundle never writes into it. promote=True then gives a newly
    registered version production through promote_candidate: the local stack serves what it registers, Railway does
    not. A folder that is already registered keeps its aliases, because with several packages of one model only the
    last is the candidate and a rerun must not fail on the others. The exception is the current candidate that never
    reached production: a run stopped between registering and promoting finishes on the next run.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise RegistrationError(f"No such folder: {directory}")
    _connect(wait)
    with tempfile.TemporaryDirectory(dir=workdir) as tmp:
        root = Path(tmp) / "package"
        shutil.copytree(directory, root)
        _say(f"copied {sum(1 for p in root.rglob('*') if p.is_file())} files from {directory}")
        result = register_package_root(root, grid_spec_id=grid_spec_id)
    if not promote:
        return result
    unfinished = "candidate" in result["aliases"] and "production" not in result["aliases"]
    if result["status"] == "registered" or unfinished:
        promotion = promote_candidate(f"{result['target']}:{result['version']}", wait=0)
        result["aliases"] = sorted(set(result["aliases"]) | {"production"})
        result["promotion"] = promotion["status"]
    else:
        _say(f"promotion skipped: {result['name']} version {result['version']} is already registered "
             f"(aliases: {', '.join(result['aliases']) or 'none'})")
        result["promotion"] = "skipped"
    return result


def register_package_root(root, *, grid_spec_id=None) -> dict:
    """Verify the package in root, skip it if registered, else register, resolve, compare, then set candidate."""
    info = inspect_package(root, grid_spec_id)
    target = "route" if info["kind"] == "route_policy" else info["target"]
    hashes = " ".join(f"{k} {v[:16]}..." for k, v in info["tags"].items())
    _say(f"verified: {info['name']} {info['model_version']} {hashes} {info['files']} files + package.json"
         + (" (package.json written here)" if info["packaged_here"] else ""))

    from spatial_model.artifacts.registry import client as registry_client, register, register_route_package
    client = registry_client()
    existing = find_registered(client, info)
    if existing is not None:
        _say(f"already registered: {info['name']} version {existing.version} holds this package; nothing registered")
        aliases = client.get_registered_model(info["name"]).aliases  # search results carry no aliases
        return {"status": "already_registered", "name": info["name"], "target": target,
                "version": str(existing.version),
                "aliases": sorted(a for a, v in aliases.items() if str(v) == str(existing.version)),
                **{key: existing.tags.get(key) for key in info["tags"]}}
    _check_proxied(client, info["kind"])
    before = max((int(v.version) for v in client.search_model_versions(f"name='{info['name']}'")), default=0)
    try:
        result = (register_route_package(root, set_candidate=False) if info["kind"] == "route_policy"
                  else register(root, set_candidate=False))
        identity, files = _verify_registered(info, result["version"])
        client.set_registered_model_alias(result["name"], "candidate", result["version"])
        _say(f"registered: {result['name']} version {result['version']} aliases candidate")
    except BaseException as exc:  # also Ctrl-C: never leave a version behind
        try:
            _delete_created(client, info, before)
        except Exception as cleanup:  # report both; the original error is the one that matters
            _say(f"rollback failed: {cleanup}; check {info['name']} versions after {before} by hand")
        if isinstance(exc, RegistrationError) or not isinstance(exc, Exception):
            raise
        raise RegistrationError(f"Registration did not complete: {exc}") from exc
    _say(f"RESOLVED {identity}: all {files} files + package.json match.  RESULT: PASS")
    return {"status": "registered", "name": result["name"], "target": target, "version": result["version"],
            "run_id": result["run_id"], "aliases": ["candidate"], "model_version": info["model_version"],
            **info["tags"]}
