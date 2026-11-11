"""Route policy set packages (TI-AI-11): a trained set reduced to what serving needs, plus package.json.

A package keeps the set's directory layout (set_manifest.json, members/<run>/...), so
`infer.load_policy_set` reads it unchanged, but holds only each member's selected checkpoint
and its manifests. Training curves, periodic checkpoints and logs stay behind.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..model_package import (
    PACKAGE_FILE, PackageError, build_package, package_digest, require_compatible, sha256_file, validate_package,
)
from .infer import load_policy
from .features import FEATURE_VERSION

MODEL_ID = "morl-route-policy"
#: Says which loader a registered version needs; model_id alone does not. A single
#: preference-conditioned policy (load_scalarized_policy) would be another family.
POLICY_FAMILY = "fixed_preference_set"
_SET_FILES = ("set_manifest.json", "od_pairs.json", "evaluation.json")
_MEMBER_FILES = ("manifest.json", "config.json", "evaluation.json")


def runtime_contract(graph_version, data_contracts_version, upstream_model=None) -> dict:
    """What the backend provides; compared with the package's contract at load time."""
    runtime = {"data_contracts_version": data_contracts_version, "graph_version": graph_version}
    if upstream_model:
        runtime["upstream_model"] = upstream_model
    return runtime


def package_policy_set(set_dir, out, *, data_contracts_version, upstream_model=None, decision=None,
                       created_at=None) -> dict:
    """Copy the selected checkpoints and manifests of set_dir into out and write out/package.json.

    upstream_model: {name, version, bundle_sha256} of the spatial model whose exposure fields the
    set was trained on, when known. It is recorded; a different one in production only flags.
    decision: the evaluation outcome for THIS set (for example the TI-AI-10 outcome and its
    consequence). Recorded as given; None when the set has not been evaluated. Never inherited.
    created_at: recorded in package.json; defaults to now. Pass a fixed value (for example the set's
    finished_at) when the same set must always give the same package.json, and so the same hash.
    """
    set_dir, out = Path(set_dir), Path(out)
    manifest = json.loads((set_dir / "set_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "completed":
        raise PackageError(f"Policy set status is {manifest.get('status')!r}, not completed")
    if manifest.get("feature_version") != FEATURE_VERSION:
        raise PackageError(f"Set was trained on features {manifest.get('feature_version')!r}, code builds {FEATURE_VERSION!r}")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise PackageError(f"{out} is not empty")
    for name in _SET_FILES:
        if (set_dir / name).is_file():
            shutil.copyfile(set_dir / name, out / name)
    first = {}
    for member in manifest["members"]:
        best = member.get("best")
        if not (member.get("run_dir") and best):
            raise PackageError(f"Member alpha={member['alpha']} has no selected checkpoint")
        source = set_dir / member["run_dir"]
        checkpoint = source / best["checkpoint"]
        if sha256_file(checkpoint) != best["sha256"]:
            raise PackageError(f"Checkpoint of alpha={member['alpha']} does not match its recorded SHA-256")
        target = out / member["run_dir"]
        (target / best["checkpoint"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checkpoint, target / best["checkpoint"])
        for name in _MEMBER_FILES:
            if (source / name).is_file():
                shutil.copyfile(source / name, target / name)
        first = first or json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    data = manifest["data"]
    graph_version = data["graph_version"]
    # Every member must name the same graph as the set, or the package would describe two graphs.
    for member in manifest["members"]:
        member_graph = json.loads((set_dir / member["run_dir"] / "manifest.json").read_text(encoding="utf-8"))["data"]["graph_version"]
        if member_graph != graph_version:
            raise PackageError(f"Member alpha={member['alpha']} was trained on graph {member_graph}, the set on {graph_version}")
    fields = first["data"].get("fields", {})
    return build_package(
        out, model_kind="route_policy", model_id=MODEL_ID, model_version=manifest["set_id"],
        contract={"data_contracts_version": data_contracts_version, "graph_version": graph_version,
                  "feature_version": manifest["feature_version"], "mode": data.get("mode")},
        lineage={"dataset_id": fields.get("dataset", {}).get("version"), "dataset": fields.get("dataset"),
                 "exposure_estimator": fields.get("estimator"), "fields_manifest_sha256": fields.get("manifest_sha256"),
                 "graph_files_sha256": first["data"].get("graph_files_sha256"), "od_pairs_sha256": data.get("od_pairs_sha256"),
                 "code_revision": manifest["code"]["commit"], "code_dirty": manifest["code"].get("dirty"),
                 "config_sha256": manifest["config_sha256"], "algorithm": manifest["algorithm"],
                 "upstream_model": upstream_model},
        environment={"python": first["versions"]["python"],
                     "libraries": {k: v for k, v in first["versions"].items() if k != "python"}},
        inference={"entrypoint": "route_model.morl.packaging.load_packaged_policy_set", "policy_family": POLICY_FAMILY,
                   "device": "cpu",
                   "alphas": [m["alpha"] for m in manifest["members"]], "selection_rule": manifest["selection_rule"],
                   "fallback": "A* route when the policy returns PolicyFailure"},
        evaluation={"validation": manifest.get("evaluation"), "protocol": "docs/experiments/TI-AI-10/README.md",
                    "decision": decision},
        created_at=created_at)


def load_packaged_policy_set(package_dir, runtime, device="cpu"):
    """(alpha -> model, package manifest, Compatibility) from a package.

    Verifies every file hash, then the contract against runtime (see runtime_contract).
    Raises PackageError for an altered package, PackageIncompatible for another contract
    major version or graph; a flagged result is returned for the caller to log or expose.
    """
    package_dir = Path(package_dir)
    package = validate_package(package_dir, "route_policy")
    compatibility = require_compatible(package, runtime)
    set_manifest = json.loads((package_dir / "set_manifest.json").read_text(encoding="utf-8"))
    policies = {}
    for member in set_manifest["members"]:
        checkpoint = package_dir / member["run_dir"] / member["best"]["checkpoint"]
        if sha256_file(checkpoint) != member["best"]["sha256"]:
            raise PackageError(f"Checkpoint of alpha={member['alpha']} does not match set_manifest.json")
        policies[member["alpha"]] = load_policy(checkpoint, device)[0]
    return policies, package, compatibility


__all__ = ["MODEL_ID", "POLICY_FAMILY", "runtime_contract", "package_policy_set", "load_packaged_policy_set", "package_digest", "PACKAGE_FILE"]
