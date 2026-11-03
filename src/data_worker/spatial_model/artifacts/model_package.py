"""Model package manifest (TI-AI-11): one format for the spatial and route models.

Standard library only. The backend and the Data Worker are separate containers, so this
file is copied verbatim to src/backend/route_model/model_package.py; a test keeps the two
copies identical. Edit one, then copy it over the other.

A package is a directory with package.json at its root. package.json lists the SHA-256 of
every other file, the contract the model was built for, its lineage, and its environment.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_FORMAT = "model-package-1"
PACKAGE_FILE = "package.json"
KINDS = ("spatial_downscaling", "route_policy")
REQUIRED_KEYS = ("package_format", "model_kind", "model_id", "model_version", "created_at", "contract",
                 "lineage", "environment", "inference", "evaluation", "artifacts")
CONTRACT_KEYS = ("data_contracts_version",)
# The route policy's features and actions are edge ids of one road graph. The spatial model
# predicts per grid cell and is aggregated onto whichever graph is active, so it has no graph.
GRAPH_BOUND_KINDS = ("route_policy",)

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_TEXT_SUFFIXES = {".json", ".toml", ".txt", ".md", ".csv", ".yml", ".yaml", ".ini", ".cfg", ".env", ".py"}
_SCAN_LIMIT = 5_000_000
# Credential shapes: cloud keys, private keys, passwords in URLs, key=value secrets.
_SECRET_PATTERNS = (
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("url_password", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
    ("secret_assignment", re.compile(
        r"(?i)[\"']?\b(aws_secret_access_key|secret_access_key|password|passwd|api[_-]?key|auth[_-]?token|access[_-]?token)\b[\"']?"
        r"\s*[:=]\s*[\"']?[^\s\"',}]{6,}")),
)
_SECRET_FILENAMES = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_rsa|credentials(\.json)?)$", re.I)


class PackageError(ValueError):
    """The package is malformed, altered, incomplete, or holds something it must not."""


class PackageIncompatible(PackageError):
    """The package was built for a different contract than the runtime provides."""

    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


@dataclass
class Compatibility:
    status: str = "compatible"          # compatible | flagged | rejected
    reasons: list = field(default_factory=list)

    def as_dict(self):
        return {"status": self.status, "reasons": list(self.reasons)}


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(root: Path):
    return sorted(p for p in root.rglob("*") if p.is_file() and p.relative_to(root).as_posix() != PACKAGE_FILE)


def scan_for_secrets(root) -> list[str]:
    """Findings (file: kind), never the secret itself. Binary files are not read."""
    root = Path(root)
    findings = []
    for path in _files(root):
        name = path.relative_to(root).as_posix()
        if _SECRET_FILENAMES.search(name):
            findings.append(f"{name}: credential-like file name")
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES or path.stat().st_size > _SCAN_LIMIT:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for kind, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"{name}: {kind}")
    return findings


def build_package(root, *, model_kind, model_id, model_version, contract, lineage, environment, inference,
                  evaluation, created_at=None) -> dict:
    """Hash every file under root, check the manifest fields and write root/package.json."""
    root = Path(root)
    if model_kind not in KINDS:
        raise PackageError(f"Unknown model kind: {model_kind!r}")
    required = CONTRACT_KEYS + (("graph_version",) if model_kind in GRAPH_BOUND_KINDS else ())
    missing = [key for key in required if not contract.get(key)]
    if missing or not _SEMVER.match(str(contract.get("data_contracts_version", ""))):
        raise PackageError(f"contract needs a semantic data_contracts_version" +
                           (" and graph_version" if "graph_version" in required else "") + f" ({missing or 'version'})")
    found = scan_for_secrets(root)
    if found:
        raise PackageError("Package would carry credentials or sensitive files: " + "; ".join(found))
    manifest = {
        "package_format": PACKAGE_FORMAT, "model_kind": model_kind, "model_id": model_id,
        "model_version": str(model_version),
        "created_at": created_at or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "contract": dict(contract), "lineage": lineage, "environment": environment, "inference": inference,
        "evaluation": evaluation,
        "artifacts": {p.relative_to(root).as_posix(): sha256_file(p) for p in _files(root)},
    }
    (root / PACKAGE_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
                                     encoding="utf-8")
    return manifest


def load_manifest(root) -> dict:
    path = Path(root) / PACKAGE_FILE
    if not path.is_file():
        raise PackageError(f"No {PACKAGE_FILE} in {root}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("package_format") != PACKAGE_FORMAT:
        raise PackageError(f"Unsupported package format: {manifest.get('package_format')!r}")
    missing = [key for key in REQUIRED_KEYS if key not in manifest]
    if missing:
        raise PackageError(f"Package manifest lacks: {', '.join(missing)}")
    return manifest


def validate_package(root, kind=None) -> dict:
    """Format, kind, every listed hash, no unlisted file, no credential. Returns the manifest."""
    root = Path(root).resolve()
    manifest = load_manifest(root)
    if manifest["model_kind"] not in KINDS or (kind and manifest["model_kind"] != kind):
        raise PackageError(f"Model kind mismatch: {manifest['model_kind']!r}, expected {kind!r}")
    listed = manifest["artifacts"]
    for name, expected in listed.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise PackageError(f"Package file missing or outside the package: {name}")
        if sha256_file(path) != expected:
            raise PackageError(f"Package file changed: {name}")
    extra = [p.relative_to(root).as_posix() for p in _files(root) if p.relative_to(root).as_posix() not in listed]
    if extra:
        raise PackageError("Package holds files its manifest does not list: " + ", ".join(extra))
    found = scan_for_secrets(root)
    if found:
        raise PackageError("Package carries credentials or sensitive files: " + "; ".join(found))
    return manifest


def package_digest(root) -> str:
    """Identity of a package: the SHA-256 of its package.json (which pins every other file)."""
    return sha256_file(Path(root) / PACKAGE_FILE)


def check_compatibility(manifest, runtime) -> Compatibility:
    """Compare what the package was built for with what the runtime provides.

    runtime: data_contracts_version, and for a route policy graph_version; optionally
    grid_spec_id, upstream_model ({name, version, bundle_sha256} of the spatial model now in production).
    Rejected: another contract major version; for a route policy also another graph (edge ids
    would not match). A spatial model is not bound to the road graph, so graph_version is ignored for it.
    Flagged: a newer or older minor/patch contract, another grid spec, another upstream model.
    """
    result = Compatibility()
    built = manifest["contract"]

    def reject(reason):
        result.status = "rejected"
        result.reasons.append(reason)

    def flag(reason):
        if result.status == "compatible":
            result.status = "flagged"
        result.reasons.append(reason)

    have, want = _SEMVER.match(str(built.get("data_contracts_version", ""))), _SEMVER.match(str(runtime["data_contracts_version"]))
    if not have or not want:
        reject(f"Unreadable contract version: package {built.get('data_contracts_version')!r}, runtime {runtime['data_contracts_version']!r}")
    elif have.group(1) != want.group(1):
        reject(f"Contract major version differs: package {built['data_contracts_version']}, runtime {runtime['data_contracts_version']}")
    elif have.groups() != want.groups():
        flag(f"Contract version differs: package {built['data_contracts_version']}, runtime {runtime['data_contracts_version']}")
    if manifest["model_kind"] in GRAPH_BOUND_KINDS and built.get("graph_version") != runtime.get("graph_version"):
        reject(f"Graph version differs: package {built.get('graph_version')!r}, runtime {runtime.get('graph_version')!r}")
    if runtime.get("grid_spec_id") and built.get("grid_spec_id") and built["grid_spec_id"] != runtime["grid_spec_id"]:
        flag(f"Grid spec differs: package {built['grid_spec_id']}, runtime {runtime['grid_spec_id']}")
    expected_upstream, current = manifest["lineage"].get("upstream_model"), runtime.get("upstream_model")
    if expected_upstream and current and expected_upstream.get("bundle_sha256") != current.get("bundle_sha256"):
        flag("Route policy was built on another spatial model version than the one in production")
    return result


def require_compatible(manifest, runtime) -> Compatibility:
    """check_compatibility, raising PackageIncompatible when rejected; a flagged result is returned."""
    result = check_compatibility(manifest, runtime)
    if result.status == "rejected":
        raise PackageIncompatible(result.reasons)
    return result
