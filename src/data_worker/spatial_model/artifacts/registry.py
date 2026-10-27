"""MLflow version registration and verified MinIO-backed artifact loading."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import re
from pathlib import Path

from spatial_model.artifacts.bundles import validate_bundle, digest

NAMES = {"pm25": "spatial-downscaling-pm25", "no2": "spatial-downscaling-no2"}


def client():
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        raise ValueError("MLFLOW_TRACKING_URI is required")
    from mlflow import MlflowClient
    return MlflowClient(tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"))


def register(bundle):
    import mlflow
    manifest = validate_bundle(bundle)
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    mlflow.set_experiment("spatial-downscaling")
    target = manifest["target"]
    name = os.environ.get(f"SPATIAL_{target.upper()}_MODEL", NAMES[target])
    with mlflow.start_run() as run:
        mlflow.set_tags({"dataset": manifest["dataset"], "target": manifest["target"], "code_revision": manifest["code_revision"]})
        mlflow.log_params({"seed": manifest["metadata"].get("seed", 42), "bundle_sha256": digest(Path(bundle) / "bundle.json")})
        configuration = manifest["metadata"].get("training_config", manifest["metadata"].get("hyperparameters", {}))
        mlflow.log_params({str(k): str(v) for k,v in configuration.items()})
        mlflow.log_artifacts(str(bundle), artifact_path="bundle")
        # Persist numeric evaluation results as queryable run metrics as well as artifacts.
        import math
        import pandas as pd
        comparison = Path(bundle) / "idw_comparison.csv"
        if comparison.exists():
            for row in pd.read_csv(comparison).to_dict("records"):
                prefix = f"{row['split']}.{str(row['method']).replace(' ', '_')}"
                mlflow.log_metrics({f"{prefix}.{k}": float(row[k]) for k in ("rmse", "mae", "r2", "bias")
                                    if k in row and math.isfinite(float(row[k]))})
        metrics_path = Path(bundle) / "metrics.json"
        if metrics_path.exists():
            for split, values in json.loads(metrics_path.read_text()).items():
                if not isinstance(values, dict):
                    continue  # Serving fits carry a status/reason rather than split metrics.
                mlflow.log_metrics({f"{split}.{k}": float(v) for k,v in values.items()
                                    if isinstance(v,(int,float)) and math.isfinite(v)})
        c = client()
        from mlflow.exceptions import MlflowException
        try:
            c.create_registered_model(name)
        except MlflowException as exc:
            if exc.error_code != "RESOURCE_ALREADY_EXISTS":
                raise
        version = c.create_model_version(name, f"{run.info.artifact_uri}/bundle", run.info.run_id,
                                         tags={"bundle_sha256": digest(Path(bundle) / "bundle.json")})
        c.set_registered_model_alias(name, "candidate", version.version)
    return {"name": name, "version": str(version.version), "run_id": run.info.run_id}


def resolve(target, selector="production", cache_dir=None):
    c = client()
    name = os.environ.get(f"SPATIAL_{target.upper()}_MODEL", NAMES[target])
    version = c.get_model_version(name, selector) if str(selector).isdigit() else c.get_model_version_by_alias(name, selector)
    identity = f"{name}:{version.version}"
    expected = version.tags.get("bundle_sha256")
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Registered model has no bundle integrity tag")
    root = Path(cache_dir or os.environ.get("SPATIAL_MODEL_CACHE", ".cache/spatial-models")) / name / str(version.version) / expected
    if not root.exists():
        import mlflow
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
            path = Path(mlflow.artifacts.download_artifacts(artifact_uri=version.source, dst_path=tmp,
                                                         tracking_uri=c.tracking_uri))
            validate_bundle(path, target)
            if digest(path / "bundle.json") != expected:
                raise ValueError("Registered bundle hash mismatch")
            shutil.copytree(path, root)
    validate_bundle(root, target)
    if digest(root / "bundle.json") != expected:
        raise ValueError("Cached bundle hash mismatch")
    return root, identity


def promote(target, version):
    from spatial_model.inference.serving import load_model
    root, identity = resolve(target, str(version))
    load_model(root, target)
    name = os.environ.get(f"SPATIAL_{target.upper()}_MODEL", NAMES[target])
    client().set_registered_model_alias(name, "production", str(version))
    return identity
