"""Register a staged model package in the MLflow registry. Run it inside the model_registry container (Railway Console).

The registry is reachable only over Railway private networking, so this cannot run from a laptop. Stage the files in the
bucket first, wake the registry, then paste the one-liner that `scripts/railway/README.md` describes.

Environment:
  REGISTER_PREFIX    bucket prefix of the staged package, with a trailing slash (required)
  REGISTER_ALIASES   comma-separated aliases to set (default: candidate). Promotion is a deliberate choice: add
                     "production" only when the team has decided to.
  REGISTER_LOCAL_DIR use this local directory instead of the bucket (testing)

What it does: download the package, verify every file against package.json (and bundle.json for a spatial bundle),
refuse anything unlisted, then drop its own S3 credentials and register through the registry's artifact proxy, tag
the version with the package hash, set the aliases, and resolve it again to check what comes back.
Supported: spatial_downscaling (spatial-downscaling-pm25 / -no2) and route_policy (morl-route-policy).
"""
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import tempfile

PREFIX = os.environ["REGISTER_PREFIX"]
assert PREFIX.endswith("/"), "REGISTER_PREFIX must end with /"  # else it also matches sibling folders and keys start with /
ALIASES = [a.strip() for a in os.environ.get("REGISTER_ALIASES", "candidate").split(",") if a.strip()]
NAMES = {"spatial-downscaling-pm25", "spatial-downscaling-no2", "morl-route-policy"}
sha = lambda p: hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()

root = pathlib.Path(tempfile.mkdtemp()) / "package"
if os.environ.get("REGISTER_LOCAL_DIR"):
    shutil.copytree(os.environ["REGISTER_LOCAL_DIR"], root)
    n = sum(1 for p in root.rglob("*") if p.is_file())
else:
    import boto3
    from botocore.config import Config

    bucket = re.match(r"s3://([^/]+)/", os.environ["MLFLOW_ARTIFACTS_DESTINATION"]).group(1)
    s3 = boto3.client("s3", endpoint_url=os.environ["MLFLOW_S3_ENDPOINT_URL"],
                      region_name=os.environ.get("AWS_DEFAULT_REGION", "auto"), config=Config(s3={"addressing_style": "virtual"}))
    n = 0
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith("/"):  # folder placeholder made by the bucket's Files tab (0 bytes)
                continue
            dest = root / obj["Key"][len(PREFIX):]
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, obj["Key"], str(dest))
            n += 1
print("downloaded", n, "files")
assert n, f"nothing under {PREFIX}"

package = json.loads((root / "package.json").read_text())
assert package["package_format"] == "model-package-1", "unsupported package format"
kind, NAME = package["model_kind"], package["model_id"]
assert NAME in NAMES, f"unknown model_id {NAME}"
assert (kind, NAME.startswith("morl")) in {("spatial_downscaling", False), ("route_policy", True)}, "kind and model_id disagree"
listed = package["artifacts"]
for name, expected in listed.items():
    p = (root / name).resolve()
    assert p.is_relative_to(root.resolve()) and p.is_file() and sha(p) == expected, f"package hash mismatch: {name}"
extra = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != "package.json"
         and p.relative_to(root).as_posix() not in listed]
assert not extra, f"unlisted files: {extra}"
package_sha = sha(root / "package.json")
tags = {"package_sha256": package_sha}
params = {"package_sha256": package_sha, "model_version": package["model_version"]}
run_tags = {"model_kind": kind, "code_revision": str(package["lineage"]["code_revision"])}
metrics = {}
if kind == "spatial_downscaling":
    bundle = json.loads((root / "bundle.json").read_text())
    assert bundle["format_version"] == 1 and bundle["dataset"] == "ds-v0.3.0" and bundle["unit"] == "ug/m3"
    assert f"spatial-downscaling-{bundle['target']}" == NAME
    assert bundle["metadata"].get("fold") is None, "a LOSO fold cannot be registered as a serving model"
    for name, expected in bundle["hashes"].items():
        p = (root / name).resolve()
        assert p.is_relative_to(root.resolve()) and sha(p) == expected, f"bundle hash mismatch: {name}"
    tags["bundle_sha256"] = params["bundle_sha256"] = sha(root / "bundle.json")
    params["seed"] = bundle["metadata"].get("seed", 42)
    run_tags.update(dataset=bundle["dataset"], target=bundle["target"])
    experiment, artifact_path = "spatial-downscaling", "bundle"
    if (root / "metrics.json").is_file():
        for split, values in json.loads((root / "metrics.json").read_text()).items():
            if isinstance(values, dict):
                metrics.update({f"{split}.{k}": float(v) for k, v in values.items()
                                if isinstance(v, (int, float)) and math.isfinite(v)})
else:
    run_tags["graph_version"] = package["contract"]["graph_version"]
    experiment, artifact_path = "route-policy", "package"
print("verified:", NAME, package["model_version"], "package_sha256", package_sha[:16] + "...", len(listed), "files")

# From here the client has no S3 access: everything goes through the registry's artifact proxy.
for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "MLFLOW_S3_ENDPOINT_URL"):
    os.environ.pop(var, None)
os.environ.setdefault("MLFLOW_TRACKING_URI", "http://localhost:5000")
import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

client = MlflowClient()
try:
    client.create_registered_model(NAME)
except MlflowException as exc:
    if exc.error_code != "RESOURCE_ALREADY_EXISTS":
        raise
for existing in client.search_model_versions(f"name='{NAME}'"):
    assert existing.tags.get("package_sha256") != package_sha, (
        f"{NAME} version {existing.version} already holds this package; nothing registered")

mlflow.set_experiment(experiment)
with mlflow.start_run(run_name=package["model_version"]) as run:
    mlflow.set_tags(run_tags)
    mlflow.log_params(params)
    mlflow.log_artifacts(str(root), artifact_path=artifact_path)
    if metrics:
        mlflow.log_metrics(metrics)
version = client.create_model_version(NAME, f"{run.info.artifact_uri}/{artifact_path}", run.info.run_id, tags=tags)
for alias in ALIASES:
    client.set_registered_model_alias(NAME, alias, version.version)
print("registered:", NAME, "version", version.version, "aliases", ", ".join(ALIASES) or "(none)")

v = client.get_model_version(NAME, version.version)
assert all(v.tags[k] == t for k, t in tags.items())
out = pathlib.Path(mlflow.artifacts.download_artifacts(artifact_uri=v.source, dst_path=tempfile.mkdtemp()))
assert sha(out / "package.json") == package_sha
for name, expected in listed.items():
    assert sha(out / name) == expected, f"resolved file differs: {name}"
print("RESOLVED", NAME + ":" + str(v.version), "all", len(listed), "files match.  RESULT: PASS")
