"""Review-requested guards at shared publishing and evaluation boundaries."""
import hashlib
import json
from pathlib import Path
from types import ModuleType
import sys

import pytest


def _bundle(root, target, family, fold):
    root.mkdir()
    payloads = {"stations.parquet": b"stations"}
    payloads.update({"best.pt": b"checkpoint"} if family == "stgnn" else {"model.txt": b"model", "metadata.json": b"{}"})
    for name, content in payloads.items():
        (root / name).write_bytes(content)
    manifest = dict(format_version=1, dataset="ds-v0.3.0", unit="ug/m3", target=target,
                    model_family=family, metadata={"fold": fold},
                    hashes={name: hashlib.sha256(content).hexdigest() for name, content in payloads.items()})
    (root / "bundle.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.mark.parametrize("target,family,fold", [
    ("pm25", "lightgbm", None), ("no2", "stgnn", None),
    ("pm25", "stgnn", 0), ("no2", "lightgbm", 0),
])
def test_register_rejects_research_or_fold_before_mlflow(target, family, fold, tmp_path, monkeypatch):
    from spatial_model.artifacts.registry import register
    bundle = _bundle(tmp_path / "bundle", target, family, fold)
    mlflow = ModuleType("mlflow")
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid bundle must not access MLflow")
    mlflow.set_tracking_uri = forbidden
    mlflow.set_experiment = forbidden
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    with pytest.raises(ValueError, match="production|LOSO"):
        register(bundle)


@pytest.mark.parametrize("folds", [[None, 0], [1, None], [None, 0, 1]])
def test_summary_rejects_mixed_protocols(tmp_path, folds):
    from spatial_model.training.results import summarize
    with pytest.raises(ValueError, match="fixed.split.*LOSO|LOSO.*fixed.split"):
        summarize(tmp_path, folds)


def test_fixed_split_rejects_empty_test_labels(monkeypatch):
    import pandas as pd
    from spatial_model.models.stgnn import data
    root = Path(__file__).resolve().parents[3] / "docs/experiments/ds-v0.3.0"
    read = pd.read_parquet
    def empty_test(path, *args, **kwargs):
        frame = read(path, *args, **kwargs)
        if Path(path).name == "test.parquet":
            return frame.iloc[:0]
        return frame
    monkeypatch.setattr(pd, "read_parquet", empty_test)
    with pytest.raises(ValueError, match="Empty.*test"):
        data.prepare(root, "fixed_split", target="pm25")
