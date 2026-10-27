"""Saved-model evaluation must reuse contracts without fitting preprocessing."""
from dataclasses import asdict
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import torch

from spatial_model.evaluation.metrics import compute_metrics
from spatial_model.models.stgnn.model import ModelConfig, STGNN
from spatial_model.models.stgnn.preprocessing import Preprocessor, TEMPORAL_COLUMNS, LOCAL_COLUMNS


def test_shared_metrics_empty_constant_and_missing():
    assert compute_metrics([], [])["n"] == 0
    result = compute_metrics([2, 2, np.nan], [1, 3, 99])
    assert result["n"] == 2 and result["rmse"] == 1 and result["bias"] == 0
    assert np.isnan(result["r2"])


def test_stgnn_saved_evaluation_never_fits(tmp_path, monkeypatch):
    import spatial_model.evaluation.stgnn.evaluate as evaluator
    config = ModelConfig(temporal_channels=58, local_channels=8, hidden_channels=16,
                         local_hidden_channels=8, heads=2, dropout=.2)
    model = STGNN(config)
    torch.nn.init.zeros_(model.regression[-1].weight)
    torch.nn.init.zeros_(model.regression[-1].bias)
    temporal = np.ones((24, 2, len(TEMPORAL_COLUMNS)), dtype="float32")
    local = np.ones((24, 2, len(LOCAL_COLUMNS)), dtype="float32")
    preprocessor = Preprocessor().fit(temporal, local, [10, 20])
    hour = pd.Timestamp("2026-09-20T00:00:00Z")
    labels = pd.DataFrame(dict(time_utc=[hour, hour], station_uuid=["a", "b"],
                               node_index=[0, 1], value_ugm3=[12., 18.], idw_prediction=[10., 20.]))
    data = SimpleNamespace(hashes={"input": "hash"}, source_ids=["source"],
        split_hours={"val": [hour], "test": [hour]}, split_labels={"val": labels, "test": labels},
        hour_index={hour: 23}, window=24, temporal_features=temporal, local_features=local,
        edge_index=torch.tensor([[0, 1], [1, 0]]))
    checkpoint = tmp_path / "best.pt"
    metadata = dict(protocol="fixed_split", fold=None, snapshot_hashes=data.hashes,
                    sensor_stations=data.source_ids, estimation_source="idw")
    torch.save(dict(artifact_version="idw_residual_v1", model_config=asdict(config),
                    state_dict=model.state_dict(), preprocessing=preprocessor.state(), metadata=metadata), checkpoint)
    monkeypatch.setattr(evaluator, "prepare", lambda *args: data)
    def fail_fit(*args, **kwargs):
        raise AssertionError("Evaluation must not fit")
    monkeypatch.setattr(Preprocessor, "fit", fail_fit)
    report = evaluator.evaluate("unused", checkpoint, tmp_path / "report", batch_size=1)
    assert (report.rmse == 2).all()
    for split in ("val", "test"):
        frame = pd.read_parquet(tmp_path / "report" / f"{split}_predictions.parquet")
        np.testing.assert_array_equal(frame.predicted, frame.idw_prediction)
    provenance = json.loads((tmp_path / "report/evaluation.json").read_text())
    assert provenance["protocol"] == "fixed_split" and len(provenance["checkpoint_sha256"]) == 64
    data.hashes = {"input": "changed"}
    with pytest.raises(ValueError, match="mismatch"):
        evaluator.evaluate("unused", checkpoint, tmp_path / "bad")


def test_lightgbm_rejects_serving_fit_and_hash_mismatch(tmp_path, monkeypatch):
    import spatial_model.evaluation.lightgbm.evaluate as evaluator
    metadata = dict(target="no2", dataset="ds-v0.3.0", fold=None)
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="held-out"):
        evaluator.evaluate("unused", tmp_path, tmp_path / "out")
    metadata.update(fold=0, snapshot_hashes={"input": "old"})
    path.write_text(json.dumps(metadata))
    monkeypatch.setattr(evaluator, "verify_snapshot", lambda *a: {"input": "new"})
    with pytest.raises(ValueError, match="hash mismatch"):
        evaluator.evaluate("unused", tmp_path, tmp_path / "out")


def test_evaluation_rejects_training_split(tmp_path):
    from spatial_model.evaluation.lightgbm.evaluate import evaluate
    with pytest.raises(ValueError, match="val/test"):
        evaluate("unused", "unused", tmp_path, splits=("train",))
