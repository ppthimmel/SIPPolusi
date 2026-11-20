"""Maintained configuration shared by training, evaluation and inference.

Frozen snapshot configuration copies belong to their dataset and are never edited here.
"""
import json
import hashlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_SEED = 42
IDW_CONFIG = ROOT / "idw_baseline.toml"
SNAPSHOT_CONFIG = ROOT / "ds-v0.3.0.toml"
#: Snapshot versions the training, evaluation and bundle code accept (same layout and label rules).
SUPPORTED_DATASETS = ("ds-v0.3.0", "ds-v0.4.0")


def snapshot_version(snapshot) -> str:
    """``version`` of a frozen snapshot; raises if it is not a supported dataset."""
    version = json.loads((Path(snapshot) / "manifest.json").read_text(encoding="utf-8"))["version"]
    if version not in SUPPORTED_DATASETS:
        raise ValueError(f"Unsupported dataset {version}; expected one of {SUPPORTED_DATASETS}")
    return version


def load_model_config(model):
    if model not in ("stgnn", "lightgbm"):
        raise ValueError("Unknown model configuration")
    return json.loads((ROOT / f"{model}.json").read_text(encoding="utf-8"))


def load_idw_settings(path: Path = IDW_CONFIG) -> dict:
    path = Path(path)
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    model = config["model"]
    return {
        "power": float(model["power"]),
        "neighbors": int(model["neighbors"]),
        "qc": config["dataset"].get("qc", {}),
        "cell_m": float(config["evaluation"].get("grid_map", {}).get("cell_m", 100.0)),
        "config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }

