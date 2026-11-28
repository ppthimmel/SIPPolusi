"""NO2 XGBoost + IDW correction: training, bundle, evaluation, registration boundary and serving."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("xgboost")

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "docs/experiments/ds-v0.3.0"


@pytest.fixture(scope="module")
def fold_bundle(tmp_path_factory):
    from spatial_model.models.lightgbm.train import train
    run = tmp_path_factory.mktemp("xgb") / "run"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("THREADS", "2")
        return train(SNAPSHOT, run, target="no2", protocol="loso", fold=0, family="xgboost")


def test_training_records_family_transform_and_held_out_sensors(fold_bundle):
    from spatial_model.artifacts.bundles import validate_bundle
    from spatial_model.configs import load_model_config
    metadata = json.loads((fold_bundle / "metadata.json").read_text())
    config = load_model_config("xgboost")
    assert metadata["model_family"] == "xgboost"
    assert metadata["target_transform"] == "resid_idw" == config["target"]
    assert metadata["hyperparameters"] == config["hp"]
    manifest = validate_bundle(fold_bundle, "no2")
    assert manifest["model_family"] == "xgboost"
    assert {"model.json", "metadata.json", "stations.parquet"} <= manifest["hashes"].keys()
    import xgboost
    assert manifest["libraries"]["xgboost"] == xgboost.__version__ and "lightgbm" not in manifest["libraries"]
    folds = pd.read_parquet(SNAPSHOT / "splits/loso_folds.parquet")
    held = folds[(folds.target == "no2") & (folds.fold == 0)].group_id.iloc[0]
    stations = pd.read_parquet(SNAPSHOT / "stations.parquet")
    assert not set(metadata["sensor_stations"]) & set(stations.loc[stations.group_id == held, "uuid"])


def test_saved_evaluation_reproduces_training_predictions(fold_bundle, tmp_path):
    from spatial_model.evaluation.lightgbm.evaluate import evaluate
    evaluate(SNAPSHOT, fold_bundle, tmp_path / "evaluation")
    report = pd.read_csv(tmp_path / "evaluation" / "evaluation_metrics.csv")
    assert set(report.method) == {"XGBoost"}
    assert json.loads((tmp_path / "evaluation" / "evaluation.json").read_text())["model"] == "xgboost"
    for split in ("val", "test"):
        original = pd.read_parquet(fold_bundle.parent / f"{split}_predictions.parquet")
        saved = pd.read_parquet(tmp_path / "evaluation" / f"{split}_predictions.parquet")
        assert len(saved) > 0
        np.testing.assert_allclose(original.predicted, saved.predicted, rtol=0, atol=1e-6)


def test_summary_pools_xgboost_folds_and_detects_stale_metadata(fold_bundle, tmp_path):
    import shutil
    from spatial_model.evaluation.lightgbm.evaluate import evaluate
    from spatial_model.training.results import summarize
    run = tmp_path / "fold-0"
    shutil.copytree(fold_bundle.parent, run, ignore=shutil.ignore_patterns("bundle"))
    evaluate(SNAPSHOT, run, run / "evaluation")
    summary, coverage = summarize(tmp_path, [0])
    assert set(coverage.status) == {"available"}
    pooled = summary[(summary.scope == "pooled") & (summary.method == "model")].set_index("split")
    for split in ("val", "test"):
        assert pooled.loc[split, "n"] == len(pd.read_parquet(run / "evaluation" / f"{split}_predictions.parquet"))
    metadata = json.loads((run / "metadata.json").read_text())
    (run / "metadata.json").write_text(json.dumps(dict(metadata, note="edited")))
    assert set(summarize(tmp_path, [0])[1].status) == {"stale"}


def test_worker_grid_prediction_with_the_xgboost_bundle(fold_bundle):
    """Worker path end to end: live-shaped inputs -> tabular_frame -> XGBoost -> grid values."""
    from types import SimpleNamespace
    from spatial_model.data.grid import GridSpec
    from spatial_model.inference.serving import load_model, predict_grid
    loaded = load_model(fold_bundle, "no2")
    columns = loaded.metadata["columns"]
    raw = [c for c in columns[columns.index("geoscf_no2_ugm3"):]
           if not c.startswith("ctm_") and c not in ("wind_u", "wind_v")] + ["wind_direction_10m"]
    hours = pd.date_range("2026-10-01T00:00Z", periods=24, freq="h")
    rng = np.random.default_rng(3)

    def frames(nodes, stations, target, end):
        ids = list(dict.fromkeys([*nodes.grid_id, *stations.grid_id]))
        index = pd.MultiIndex.from_product([hours, ids], names=["time_utc", "grid_id"])
        frame = pd.DataFrame({c: rng.uniform(1, 60, len(index)) for c in raw}, index=index)
        frame["wind_direction_10m"] = rng.uniform(0, 360, len(index))
        return hours, frame, rng.uniform(5, 80, (len(hours), len(stations)))

    spec = GridSpec.from_bbox((106.8, -6.2, 106.803, -6.197))
    values = predict_grid(loaded, SimpleNamespace(frames=frames), spec, fold_bundle, hours[-1])
    assert len(values) == spec.n_cells
    assert np.isfinite(values).all() and (values >= 0).all()


def test_prediction_is_idw_plus_learned_correction(fold_bundle):
    from spatial_model.models.lightgbm.model import predict
    from spatial_model.models.tabular import load_native
    metadata = json.loads((fold_bundle / "metadata.json").read_text())
    model, _ = load_native(fold_bundle, metadata)
    rng = np.random.default_rng(0)
    frame = pd.DataFrame(rng.uniform(1, 80, size=(32, len(metadata["columns"]))), columns=metadata["columns"])
    correction = model.predict(frame)
    np.testing.assert_allclose(predict(model, metadata, frame), np.maximum(frame.idw_p2_k8 + correction, 0), rtol=1e-6)


def test_loader_rejects_reordered_columns(fold_bundle):
    from spatial_model.models.tabular import load_native
    metadata = json.loads((fold_bundle / "metadata.json").read_text())
    with pytest.raises(ValueError, match="feature order"):
        load_native(fold_bundle, dict(metadata, columns=list(reversed(metadata["columns"]))))


def test_serving_loads_the_no2_bundle(fold_bundle):
    from spatial_model.inference.serving import load_model
    loaded = load_model(fold_bundle, "no2")
    assert loaded.metadata["model_family"] == "xgboost"
    assert loaded.compatibility.status == "flagged"  # no package.json in a training output


def test_registration_rejects_a_loso_fold_but_accepts_the_family(fold_bundle, tmp_path):
    import shutil
    from spatial_model.artifacts.staged import RegistrationError, _inspect_spatial
    copy = tmp_path / "bundle"
    shutil.copytree(fold_bundle, copy)
    with pytest.raises(RegistrationError, match="LOSO fold"):
        _inspect_spatial(copy, False, None)


def test_xgboost_is_not_a_pm25_family(tmp_path, monkeypatch):
    from spatial_model.artifacts import bundles
    from spatial_model.inference.serving import load_model
    from spatial_model.models.lightgbm.train import train
    with pytest.raises(ValueError, match="NO2 only"):
        train(SNAPSHOT, tmp_path / "pm", target="pm25", protocol="loso", fold=0, family="xgboost")
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "stations.parquet").write_bytes(b"stations")
    output = tmp_path / "run"
    output.mkdir()
    (output / "model.json").write_bytes(b"model")
    (output / "metadata.json").write_text("{}")
    monkeypatch.setattr(bundles, "verify_snapshot", lambda *a: {})
    monkeypatch.setattr(bundles, "library_versions", lambda family: {})
    bundle = bundles.export_bundle(output, "pm25", snapshot, {"model_family": "xgboost", "dataset": "ds-v0.4.0"})
    with pytest.raises(ValueError, match="production"):
        load_model(bundle, "pm25")


def test_serving_rejects_metadata_family_that_disagrees_with_bundle(tmp_path, monkeypatch):
    from spatial_model.artifacts import bundles
    from spatial_model.inference.serving import load_model
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "stations.parquet").write_bytes(b"stations")
    output = tmp_path / "run"
    output.mkdir()
    (output / "model.json").write_bytes(b"model")
    (output / "metadata.json").write_text(json.dumps({"model_family": "lightgbm"}))
    monkeypatch.setattr(bundles, "verify_snapshot", lambda *a: {})
    monkeypatch.setattr(bundles, "library_versions", lambda family: {})
    bundle = bundles.export_bundle(output, "no2", snapshot, {"model_family": "xgboost", "dataset": "ds-v0.4.0"})
    with pytest.raises(ValueError, match="disagree"):
        load_model(bundle, "no2")


@pytest.mark.parametrize("target,metadata,source", [
    ("pm25", {}, "idw_stgnn_residual"),
    ("no2", {}, "lightgbm"),
    ("no2", {"model_family": "lightgbm", "target_transform": "direct"}, "lightgbm"),
    ("no2", {"model_family": "xgboost", "target_transform": "resid_idw"}, "idw_xgboost_residual"),
    ("no2", {"model_family": "xgboost", "target_transform": "direct"}, "xgboost"),
])
def test_recorded_estimation_source(target, metadata, source):
    from spatial_model.inference.runtime import model_source
    assert model_source(target, metadata) == source


def test_cli_rejects_family_for_pm25(tmp_path):
    from spatial_model.training.__main__ import main
    with pytest.raises(SystemExit):
        main(["train", "pm25", "--out", str(tmp_path / "o"), "--family", "xgboost"])


def test_cli_trains_xgboost_for_no2_by_default(tmp_path, monkeypatch):
    from spatial_model.models.lightgbm import train as train_module
    from spatial_model.training.__main__ import main
    calls = []
    monkeypatch.setattr(train_module, "train", lambda *a, **k: calls.append(k["family"]) or tmp_path)
    main(["train", "no2", "--snapshot", str(tmp_path), "--out", str(tmp_path / "a")])
    main(["train", "no2", "--snapshot", str(tmp_path), "--out", str(tmp_path / "b"), "--family", "lightgbm"])
    assert calls == ["xgboost", "lightgbm"]


def test_cli_requires_snapshot_for_no2(tmp_path, monkeypatch, capsys):
    from spatial_model.models.lightgbm import train as train_module
    from spatial_model.training.__main__ import main
    calls = []
    monkeypatch.setattr(train_module, "train", lambda *a, **k: calls.append(a) or tmp_path)
    for family in ([], ["--family", "lightgbm"]):
        with pytest.raises(SystemExit):
            main(["train", "no2", "--out", str(tmp_path / "a"), "--register", *family])
        assert "needs --snapshot" in capsys.readouterr().err
    assert calls == []


def test_evaluation_cli_rejects_the_other_family(fold_bundle, tmp_path):
    from spatial_model.evaluation.__main__ import main
    with pytest.raises(ValueError, match="checkpoint is xgboost"):
        main(["lightgbm", "--snapshot", str(SNAPSHOT), "--checkpoint", str(fold_bundle.parent),
              "--out", str(tmp_path / "e")])
