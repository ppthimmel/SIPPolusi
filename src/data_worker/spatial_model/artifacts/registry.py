"""MLflow version registration and verified MinIO-backed artifact loading."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import re
from pathlib import Path

from spatial_model.artifacts.bundles import validate_bundle, digest
from spatial_model.artifacts.model_package import PACKAGE_FILE, package_digest, validate_package

NAMES = {"pm25": "spatial-downscaling-pm25", "no2": "spatial-downscaling-no2"}
ROUTE_NAME = "morl-route-policy"


def client():
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        raise ValueError("MLFLOW_TRACKING_URI is required")
    from mlflow import MlflowClient
    return MlflowClient(tracking_uri=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"))


def register(bundle):
    manifest = validate_bundle(bundle)
    packaged = (Path(bundle) / PACKAGE_FILE).is_file()
    if packaged:
        validate_package(bundle, "spatial_downscaling")
    expected_family = "stgnn" if manifest["target"] == "pm25" else "lightgbm"
    if manifest.get("model_family", expected_family) != expected_family:
        raise ValueError("Research model family is incompatible with the production target pairing")
    if manifest["metadata"].get("fold") is not None:
        raise ValueError("Cannot register a LOSO fold checkpoint as a serving fit")
    import mlflow
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
                                         tags={"bundle_sha256": digest(Path(bundle) / "bundle.json"),
                                                 **({"package_sha256": package_digest(bundle)} if packaged else {})})
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
            _check_package_tag(path, version)
            shutil.copytree(path, root)
    validate_bundle(root, target)
    if digest(root / "bundle.json") != expected:
        raise ValueError("Cached bundle hash mismatch")
    _check_package_tag(root, version)
    return root, identity


def _check_package_tag(root, version):
    """A version tagged with package_sha256 must carry the package.json it was registered with."""
    expected = version.tags.get("package_sha256")
    if expected and (not (Path(root) / PACKAGE_FILE).is_file() or package_digest(root) != expected):
        raise ValueError("Registered package hash mismatch")


def promote(target, version):
    from spatial_model.inference.serving import load_model
    root, identity = resolve(target, str(version))
    model = load_model(root, target)
    if model.metadata.get("fold") is not None:
        raise ValueError("Cannot promote a LOSO fold checkpoint to production")
    name = os.environ.get(f"SPATIAL_{target.upper()}_MODEL", NAMES[target])
    client().set_registered_model_alias(name, "production", str(version))
    return identity


def register_route_package(package_dir):
    """Register a route policy package (route_model.morl.packaging) as a version of morl-route-policy."""
    manifest = validate_package(package_dir, "route_policy")
    if manifest["model_id"] != ROUTE_NAME:
        raise ValueError("Package is not a morl-route-policy")
    import mlflow
    mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"))
    mlflow.set_experiment("route-policy")
    name = os.environ.get("ROUTE_POLICY_MODEL", ROUTE_NAME)
    package_sha256 = package_digest(package_dir)
    with mlflow.start_run() as run:
        mlflow.set_tags({"model_kind": "route_policy", "graph_version": manifest["contract"]["graph_version"],
                         "code_revision": str(manifest["lineage"]["code_revision"])})
        mlflow.log_params({"package_sha256": package_sha256, "model_version": manifest["model_version"]})
        mlflow.log_artifacts(str(package_dir), artifact_path="package")
        c = client()
        from mlflow.exceptions import MlflowException
        try:
            c.create_registered_model(name)
        except MlflowException as exc:
            if exc.error_code != "RESOURCE_ALREADY_EXISTS":
                raise
        version = c.create_model_version(name, f"{run.info.artifact_uri}/package", run.info.run_id,
                                         tags={"package_sha256": package_sha256})
        c.set_registered_model_alias(name, "candidate", version.version)
    return {"name": name, "version": str(version.version), "run_id": run.info.run_id}


def resolve_route_package(selector="production", cache_dir=None):
    """Download and verify a registered route package; returns (directory, 'name:version')."""
    c = client()
    name = os.environ.get("ROUTE_POLICY_MODEL", ROUTE_NAME)
    version = c.get_model_version(name, selector) if str(selector).isdigit() else c.get_model_version_by_alias(name, selector)
    expected = version.tags.get("package_sha256")
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Registered route package has no integrity tag")
    root = Path(cache_dir or os.environ.get("ROUTE_MODEL_CACHE", ".cache/route-models")) / name / str(version.version) / expected
    if not root.exists():
        import mlflow
        root.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
            path = Path(mlflow.artifacts.download_artifacts(artifact_uri=version.source, dst_path=tmp,
                                                         tracking_uri=c.tracking_uri))
            validate_package(path, "route_policy")
            if package_digest(path) != expected:
                raise ValueError("Registered package hash mismatch")
            shutil.copytree(path, root)
    validate_package(root, "route_policy")
    if package_digest(root) != expected:
        raise ValueError("Cached package hash mismatch")
    return root, f"{name}:{version.version}"
