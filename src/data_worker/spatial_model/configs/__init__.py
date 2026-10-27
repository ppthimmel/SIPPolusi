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

