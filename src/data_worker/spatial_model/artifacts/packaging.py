"""Spatial downscaling model packages (TI-AI-11): package.json on top of a verified bundle."""
from __future__ import annotations

import json
from pathlib import Path

from spatial_model.artifacts.bundles import validate_bundle
from spatial_model.artifacts.model_package import build_package, package_digest, validate_package

#: Must equal backend/public_api/data_contracts.py CONTRACT_VERSION; a backend test checks it.
DATA_CONTRACTS_VERSION = "1.0.0"

_INFERENCE = {
    "entrypoint": "spatial_model.inference.serving.load_model",
    "device": "cpu",
    "input": "24 h of sensor readings and as-of environmental features per grid cell",
    "output": "concentration per grid cell in ug/m3; the IDW fallback covers cells the model cannot serve",
}


def runtime_contract(graph_version, spec=None, upstream_model=None) -> dict:
    """What this Worker run provides; compared with the package's contract at load time."""
    runtime = {"data_contracts_version": DATA_CONTRACTS_VERSION, "graph_version": graph_version}
    if spec is not None:
        from spatial_model.inference.aggregate import grid_spec_id
        runtime["grid_spec_id"] = grid_spec_id(spec)
    if upstream_model:
        runtime["upstream_model"] = upstream_model
    return runtime


def package_bundle(bundle, *, graph_version, grid_spec_id=None, feature_matrix_version=None,
                   data_contracts_version=DATA_CONTRACTS_VERSION) -> dict:
    """Write package.json into a bundle made by export_bundle, after validating the bundle."""
    bundle = Path(bundle)
    manifest = validate_bundle(bundle)
    metrics_path = bundle / "metrics.json"
    headline = {}
    if metrics_path.is_file():
        for split, values in json.loads(metrics_path.read_text(encoding="utf-8")).items():
            if isinstance(values, dict):
                headline[split] = {k: v for k, v in values.items() if isinstance(v, (int, float))}
    target, family = manifest["target"], manifest.get("model_family")
    return build_package(
        bundle, model_kind="spatial_downscaling", model_id=f"spatial-downscaling-{target}",
        model_version=f"{manifest['dataset']}-{package_bundle_hash(bundle)[:12]}",
        contract={"data_contracts_version": data_contracts_version, "graph_version": graph_version,
                  "grid_spec_id": grid_spec_id, "feature_matrix_version": feature_matrix_version or manifest["dataset"]},
        lineage={"dataset_id": manifest["dataset"], "snapshot_hashes": manifest["snapshot_hashes"],
                 "code_revision": manifest["code_revision"], "config_hashes": manifest["config_hashes"],
                 "target": target, "model_family": family, "upstream_model": None},
        environment={"python": manifest["python"], "libraries": manifest["libraries"]},
        inference=dict(_INFERENCE, unit=manifest["unit"]),
        evaluation={"metrics_file": "metrics.json" if metrics_path.is_file() else None, "headline_metrics": headline,
                    "protocol": "docs/experiments/TI-AI-05/README.md"})


def package_bundle_hash(bundle) -> str:
    from spatial_model.artifacts.bundles import digest
    return digest(Path(bundle) / "bundle.json")


def verify_package(bundle, target=None) -> dict:
    """validate_bundle plus validate_package; returns the package manifest."""
    validate_bundle(bundle, target)
    return validate_package(bundle, "spatial_downscaling")


__all__ = ["DATA_CONTRACTS_VERSION", "runtime_contract", "package_bundle", "verify_package", "package_digest"]
