"""Build the route policy package from the committed TI-AI-09 set, for the local registry.

Runs inside the Cache API image (it has PyTorch and the route_model / public_api code under /app):

    docker compose --profile tools run --rm register_models     # runs this first

Writes /out/route-policy (a shared volume), replacing an earlier copy. register_models then registers it.
created_at is the set's finished_at, not the packaging time, so the same set always gives the same package.json
(and package_sha256): rerunning register_models then finds it already registered instead of adding a version.
"""
import json
import os
import pathlib
import shutil
import sys

sys.path.insert(0, "/app")
from public_api.data_contracts import CONTRACT_VERSION  # noqa: E402
from route_model.morl.packaging import package_policy_set  # noqa: E402

SET_DIR = pathlib.Path(os.environ.get("ROUTE_SET_DIR", "/route-set"))
OUT = pathlib.Path(os.environ.get("ROUTE_PACKAGE_OUT", "/out/route-policy"))
DECISION = os.environ.get("ROUTE_DECISION",
                          "conditional_candidate (TI-AI-10): deploy with the A* fallback, no exposure-reduction claim")

shutil.rmtree(OUT, ignore_errors=True)
finished_at = json.loads((SET_DIR / "set_manifest.json").read_text(encoding="utf-8"))["finished_at"]
manifest = package_policy_set(SET_DIR, OUT, data_contracts_version=CONTRACT_VERSION, decision=DECISION,
                              created_at=finished_at)
print(f"packaged {manifest['model_id']} {manifest['model_version']}: {len(manifest['artifacts'])} files in {OUT}")
