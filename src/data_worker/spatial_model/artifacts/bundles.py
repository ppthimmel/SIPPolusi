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


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export_bundle(output, target, snapshot, metadata):
    output, snapshot = Path(output), Path(snapshot)
    bundle = output / "bundle"
    bundle.mkdir(exist_ok=True)
    files = ["best.pt"] if target == "pm25" else ["model.txt", "metadata.json"]
    files += [name for name in ("metrics.json", "evaluation_metrics.csv", "idw_comparison.csv", "history.csv", "evaluation.json") if (output / name).is_file()]
    for name in files:
        shutil.copyfile(output / name, bundle / name)
    # Station identity/coordinates are required for sensor-derived serving features.
    shutil.copyfile(snapshot / "stations.parquet", bundle / "stations.parquet")
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        revision = os.environ.get("GIT_COMMIT", "unavailable; see source_hashes")
    manifest = dict(format_version=1, target=target, dataset="ds-v0.3.0", unit="ug/m3",
                    metadata=metadata, snapshot_hashes=verify_snapshot(snapshot),
                    code_revision=revision, python=platform.python_version(),
                    config_hashes={p.name: digest(p) for p in sorted(
                        (Path(__file__).resolve().parent.parent / "configs").iterdir())
                        if p.suffix in (".json", ".toml")},
                    libraries={name: importlib.metadata.version(name) for name in
                               (["torch", "torch-geometric", "numpy", "pandas"] if target == "pm25" else ["lightgbm", "numpy", "pandas", "scikit-learn"])},
                    source_hashes={str(p.relative_to(Path(__file__).resolve().parent.parent)): digest(p)
                                   for p in sorted(Path(__file__).resolve().parent.parent.rglob("*.py"))},
                    hashes={p.name: digest(p) for p in bundle.iterdir() if p.is_file() and p.name != "bundle.json"})
    (bundle / "bundle.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    return bundle


def validate_bundle(directory, target=None):
    root = Path(directory).resolve()
    manifest = json.loads((root / "bundle.json").read_text())
    if manifest["format_version"] != 1 or manifest["dataset"] != "ds-v0.3.0" or manifest["unit"] != "ug/m3":
        raise ValueError("Unsupported model bundle contract")
    if manifest["target"] not in ("pm25", "no2") or (target and manifest["target"] != target):
        raise ValueError("Model target mismatch")
    required = {"stations.parquet", "best.pt"} if manifest["target"] == "pm25" else {"stations.parquet", "model.txt", "metadata.json"}
    if not required <= manifest["hashes"].keys():
        raise ValueError("Incomplete model bundle")
    for name, expected in manifest["hashes"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or digest(path) != expected:
            raise ValueError(f"Model artifact integrity failure: {name}")
    return manifest
