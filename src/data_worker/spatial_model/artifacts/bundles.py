"""Immutable, hashed serving bundles shared by training and inference."""
from __future__ import annotations

import hashlib
import json
import platform
import importlib.metadata
import shutil
import subprocess
import os
from pathlib import Path

from spatial_model.data.validation import verify_snapshot
from spatial_model.configs import SUPPORTED_DATASETS

FAMILIES = ("stgnn", "lightgbm", "xgboost")


def model_files(family):
    """Files a bundle of ``family`` must carry besides stations.parquet."""
    return {"stgnn": ["best.pt"], "lightgbm": ["model.txt", "metadata.json"],
            "xgboost": ["model.json", "metadata.json"]}[family]


def serving_family(manifest):
    """Model family of a bundle manifest; bundles made before the field existed are ST-GNN / LightGBM."""
    return manifest.get("model_family", "stgnn" if manifest["target"] == "pm25" else "lightgbm")


def library_versions(family):
    if family == "stgnn":
        return {name: importlib.metadata.version(name) for name in ["torch", "torch-geometric", "numpy", "pandas"]}
    versions = {name: importlib.metadata.version(name) for name in ["numpy", "pandas", "scikit-learn"]}
    if family == "lightgbm":
        versions["lightgbm"] = importlib.metadata.version("lightgbm")
    else:
        from spatial_model.models.xgboost.model import library_version
        versions["xgboost"] = library_version()
    return versions


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_bundle(output, target, snapshot, metadata):
    output, snapshot = Path(output), Path(snapshot)
    bundle = output / "bundle"
    bundle.mkdir(exist_ok=True)
    family = serving_family(dict(metadata, target=target))
    if family not in FAMILIES:
        raise ValueError("Unsupported model family")
    # Every training path records the snapshot's version; a missing or unknown one is an error, not a default.
    dataset = metadata.get("dataset")
    if dataset not in SUPPORTED_DATASETS:
        raise ValueError(f"Bundle metadata needs a supported dataset version, got {dataset!r}")
    files = model_files(family)
    files += [name for name in ("metrics.json", "evaluation_metrics.csv", "idw_comparison.csv", "history.csv", "evaluation.json") if (output / name).is_file()]
    for name in files:
        shutil.copyfile(output / name, bundle / name)
    # Station identity/coordinates are required for sensor-derived serving features.
    shutil.copyfile(snapshot / "stations.parquet", bundle / "stations.parquet")
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        revision = os.environ.get("GIT_COMMIT", "unavailable; see source_hashes")
    manifest = dict(format_version=1, model_family=family, target=target, dataset=dataset, unit="ug/m3",
                    metadata=metadata, snapshot_hashes=verify_snapshot(snapshot),
                    code_revision=revision, python=platform.python_version(),
                    config_hashes={p.name: digest(p) for p in sorted(
                        (Path(__file__).resolve().parent.parent / "configs").iterdir())
                        if p.suffix in (".json", ".toml")},
                    libraries=library_versions(family),
                    source_hashes={str(p.relative_to(Path(__file__).resolve().parent.parent)): digest(p)
                                   for p in sorted(Path(__file__).resolve().parent.parent.rglob("*.py"))},
                    hashes={p.name: digest(p) for p in bundle.iterdir() if p.is_file() and p.name != "bundle.json"})
    (bundle / "bundle.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return bundle


def validate_bundle(directory, target=None):
    root = Path(directory).resolve()
    manifest = json.loads((root / "bundle.json").read_text())
    if manifest["format_version"] != 1 or manifest["dataset"] not in SUPPORTED_DATASETS or manifest["unit"] != "ug/m3":
        raise ValueError("Unsupported model bundle contract")
    if manifest["target"] not in ("pm25", "no2") or (target and manifest["target"] != target):
        raise ValueError("Model target mismatch")
    family = serving_family(manifest)
    if family not in FAMILIES:
        raise ValueError("Unsupported model family")
    required = {"stations.parquet", *model_files(family)}
    if not required <= manifest["hashes"].keys():
        raise ValueError("Incomplete model bundle")
    for name, expected in manifest["hashes"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError(f"Model artifact integrity failure: {name}")
    return manifest
