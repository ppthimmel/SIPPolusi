"""Package and reload the selected PIC A checkpoint without retraining."""
import json
from pathlib import Path

import pytest
import torch

from src.backend.route_model.model_package import PackageError, PackageIncompatible
from src.backend.route_model.morl.infer import PolicyFailure
from src.backend.route_model.morl.packaging import (
    load_packaged_scalarized_policy, package_scalarized_policy, runtime_contract,
)
from src.backend.route_model.morl.scalarized import decode_preference_route, load_scalarized_policy
from src.backend.tests.test_morl_policy import EXPOSURE, two_route_router

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / "docs/experiments/TI-AI-09/scalarized/20261103T081938Z-scalarized-bike-v1-6eab87d4"
GRAPH = "jakarta-20261005"


@pytest.fixture
def packaged(tmp_path):
    directory = tmp_path / "package"
    manifest = package_scalarized_policy(RUN, directory, data_contracts_version="1.1.0", decision="conditional_candidate")
    return directory, manifest


def test_selected_model_reloads_and_reproduces_routes(packaged):
    directory, manifest = packaged
    model, _, compatibility = load_packaged_scalarized_policy(directory, runtime_contract(GRAPH, "1.1.0"))
    original, _ = load_scalarized_policy(RUN / "checkpoints/best.pt")
    assert compatibility.status == "compatible"
    assert manifest["inference"]["policy_family"] == "preference_conditioned"
    assert manifest["lineage"]["selected_update"] == 0
    assert manifest["evaluation"]["decision"] == "conditional_candidate"
    assert all(torch.equal(original.state_dict()[key], value) for key, value in model.state_dict().items())
    router = two_route_router()

    def result(policy, alpha):
        try:
            route = decode_preference_route(policy, router, EXPOSURE, 1, 4, (alpha, 1-alpha), max_steps=20)
            return ("route", route.edge_ids)
        except PolicyFailure as failure:
            return ("failure", failure.reason)

    for alpha in (0.0, 0.3, 0.5, 1.0):
        assert result(original, alpha) == result(model, alpha)


def test_wrong_graph_and_contract_rejected_minor_flagged(packaged):
    directory, _ = packaged
    for runtime in (runtime_contract("other", "1.1.0"), runtime_contract(GRAPH, "2.0.0")):
        with pytest.raises(PackageIncompatible):
            load_packaged_scalarized_policy(directory, runtime)
    _, _, result = load_packaged_scalarized_policy(directory, runtime_contract(GRAPH, "1.2.0"))
    assert result.status == "flagged"


def test_tampering_and_nonempty_target_rejected(packaged):
    directory, _ = packaged
    (directory / "checkpoints/best.pt").write_bytes(b"changed")
    with pytest.raises(PackageError, match="changed"):
        load_packaged_scalarized_policy(directory, runtime_contract(GRAPH, "1.1.0"))
    with pytest.raises(PackageError, match="not empty"):
        package_scalarized_policy(RUN, directory, data_contracts_version="1.1.0")


def test_manifest_does_not_include_local_machine_paths(packaged):
    directory, _ = packaged
    run = json.loads((directory / "run_manifest.json").read_text(encoding="utf-8"))
    assert "fields_dir" not in run and "graph_dir" not in run


def test_serving_import_does_not_load_training_modules(tmp_path):
    """Serving images do not include the repository-root layout required by training."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": str(ROOT / "src/backend")}
    subprocess.run([sys.executable, "-c",
                    "import sys; import route_model.morl.scalarized_serving; "
                    "assert 'route_model.morl.train' not in sys.modules; "
                    "assert 'route_model.morl.scalarized' not in sys.modules"],
                   cwd=tmp_path, env=env, check=True)
