"""Research training target/protocol matrix and production boundaries."""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "docs/experiments/ds-v0.3.0"


def test_stgnn_no2_uses_no2_labels_and_excludes_held_group():
    import pandas as pd
    from spatial_model.models.stgnn.data import prepare
    data = prepare(SNAPSHOT, "loso", 0, target="no2")
    labels = pd.read_parquet(SNAPSHOT / "labels.parquet")
    folds = pd.read_parquet(SNAPSHOT / "splits/loso_folds.parquet")
    held = folds[(folds.target == "no2") & (folds.fold == 0)].group_id.iloc[0]
    stations = pd.read_parquet(SNAPSHOT / "stations.parquet")
    assert not set(stations.loc[stations.group_id == held, "uuid"]) & set(data.source_ids)
    assert (data.split_labels["test"].group_id == held).all()
    actual = data.split_labels["train"].merge(labels, on=["station_uuid", "time_utc"], suffixes=("", "_original"))
    assert (actual.value_ugm3 == actual.no2_ugm3).all()


@pytest.mark.parametrize("family", ["stgnn", "lightgbm"])
def test_reject_no2_fixed_split_before_reading_inputs(family, tmp_path):
    from importlib import import_module
    train = import_module(f"spatial_model.models.{family}.train").train
    with pytest.raises(ValueError, match="PM2.5|pm25"):
        train("missing", tmp_path / "out", target="no2", protocol="fixed_split")


@pytest.mark.parametrize("target,family,filename", [("pm25", "lightgbm", "model.txt"), ("no2", "stgnn", "best.pt")])
def test_bundle_records_family_and_rejects_wrong_production_pairing(target, family, filename, tmp_path, monkeypatch):
    from spatial_model.artifacts import bundles
    from spatial_model.inference.serving import load_model
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "stations.parquet").write_bytes(b"stations")
    output = tmp_path / "run"
    output.mkdir()
    (output / filename).write_bytes(b"model")
    (output / "metadata.json").write_text("{}")
    monkeypatch.setattr(bundles, "verify_snapshot", lambda *a: {})
    bundle = bundles.export_bundle(output, target, snapshot, {"model_family": family})
    manifest = bundles.validate_bundle(bundle, target)
    assert manifest["model_family"] == family
    assert filename in manifest["hashes"]
    with pytest.raises(ValueError, match="production"):
        load_model(bundle, target)


@pytest.mark.parametrize("target,protocol,fold", [("pm25", "fixed_split", None), ("pm25", "loso", 0), ("no2", "loso", 0)])
def test_lightgbm_training_and_saved_evaluation_matrix(target, protocol, fold, tmp_path, monkeypatch):
    import numpy as np
    import pandas as pd
    from spatial_model.models.lightgbm.train import train
    from spatial_model.evaluation.lightgbm.evaluate import evaluate
    monkeypatch.setenv("THREADS", "2")
    bundle = train(SNAPSHOT, tmp_path / "run", target=target, protocol=protocol, fold=fold)
    metadata = json.loads((bundle / "metadata.json").read_text())
    assert metadata["target"] == target and metadata["protocol"] == protocol
    stations = pd.read_parquet(SNAPSHOT / "stations.parquet")
    if protocol == "fixed_split":
        assert set(metadata["sensor_stations"]) == set(stations.loc[stations.station_split == "train", "uuid"])
    else:
        folds = pd.read_parquet(SNAPSHOT / "splits/loso_folds.parquet")
        held = folds[(folds.target == target) & (folds.fold == fold)].group_id.iloc[0]
        assert not set(metadata["sensor_stations"]) & set(stations.loc[stations.group_id == held, "uuid"])
    evaluate(SNAPSHOT, bundle, tmp_path / "evaluation")
    for split in ("val", "test"):
        original = pd.read_parquet(tmp_path / "run" / f"{split}_predictions.parquet")
        saved = pd.read_parquet(tmp_path / "evaluation" / f"{split}_predictions.parquet")
        np.testing.assert_array_equal(original.predicted, saved.predicted)
        assert len(saved) > 0


def test_stgnn_fold_without_test_labels_is_trainable():
    import pandas as pd
    from spatial_model.models.stgnn.data import prepare
    folds = pd.read_parquet(SNAPSHOT / "splits/loso_folds.parquet")
    labels = pd.read_parquet(SNAPSHOT / "labels.parquet")
    test_groups = set(labels.loc[(labels.period == "test") & labels.no2_ugm3.notna(), "group_id"])
    fold = folds[(folds.target == "no2") & ~folds.group_id.isin(test_groups)].fold.iloc[0]
    data = prepare(SNAPSHOT, "loso", int(fold), target="no2")
    assert not data.split_labels["train"].empty
    assert not data.split_labels["val"].empty
    assert data.split_labels["test"].empty


def test_summary_pools_rows_and_reports_missing_folds(tmp_path):
    import hashlib
    import pandas as pd
    from spatial_model.training.results import summarize
    for fold, observed in [(0, [0., 0.]), (1, [0.])]:
        path = tmp_path / f"fold-{fold}" / "evaluation"
        path.mkdir(parents=True)
        (path.parent / "best.pt").write_bytes(b"checkpoint")
        (path / "evaluation.json").write_text(json.dumps({"model": "stgnn", "splits": ["test"],
            "checkpoint_sha256": hashlib.sha256(b"checkpoint").hexdigest()}))
        pd.DataFrame({"observed": observed, "predicted": [float(fold + 1)] * len(observed)}).to_parquet(path / "test_predictions.parquet")
    summary, coverage = summarize(tmp_path, [0, 1, 2], splits=("test",))
    row = summary[(summary.scope == "pooled") & (summary.method == "model")].iloc[0]
    assert row.n == 3 and row.rmse == pytest.approx(2 ** .5)
    assert coverage.set_index("fold").loc[2, "status"] == "missing"


def test_summary_rejects_stale_checkpoint_predictions(tmp_path):
    import hashlib
    import pandas as pd
    from spatial_model.training.results import summarize
    path = tmp_path / "fold-0/evaluation"
    path.mkdir(parents=True)
    (path.parent / "best.pt").write_bytes(b"new checkpoint")
    (path / "evaluation.json").write_text(json.dumps({"model": "stgnn", "splits": ["test"],
        "checkpoint_sha256": hashlib.sha256(b"old checkpoint").hexdigest()}))
    pd.DataFrame({"observed": [1.], "predicted": [2.]}).to_parquet(path / "test_predictions.parquet")
    summary, coverage = summarize(tmp_path, [0], splits=("test",))
    assert summary.empty and coverage.iloc[0].status == "stale"


def test_stgnn_no2_training_exports_correct_target(tmp_path):
    from spatial_model.models.stgnn.train import train
    bundle = train(SNAPSHOT, tmp_path / "no2", target="no2", protocol="loso", fold=0, epochs=1)
    manifest = json.loads((bundle / "bundle.json").read_text())
    assert manifest["target"] == "no2"
    assert manifest["model_family"] == "stgnn"


def test_notebook_multifold_resume_starts_untouched_folds(tmp_path, monkeypatch):
    from spatial_model.models.stgnn import train as module
    calls = []
    monkeypatch.setattr(module, "train", lambda *args, **kwargs: calls.append(kwargs) or Path("bundle"))
    started = tmp_path / "fold-0"
    started.mkdir()
    (started / "progress.pt").write_bytes(b"interrupted")
    notebook = json.loads((ROOT / "notebooks/spatial_model/train_stgnn.ipynb").read_text(encoding="utf-8"))
    cell = next(cell for cell in notebook["cells"] if cell["id"] == "train")
    scope = dict(RUN_TRAINING=True, RESUME=True, RUN_ROOT=tmp_path, selected_folds=[0, 1],
                 SNAPSHOT=SNAPSHOT, TARGET="pm25", PROTOCOL="loso", SEED=42,
                 EPOCHS=1, DEVICE="cpu", BATCH_SIZE=8, THREADS=2)
    exec("".join(cell["source"]), scope)
    assert [call["resume"] for call in calls] == [True, False]


@pytest.mark.parametrize("family", ["stgnn", "lightgbm"])
def test_notebook_schema_defaults_and_disabled_publication(family):
    import ast
    import nbformat
    notebook = nbformat.read(ROOT / f"notebooks/spatial_model/train_{family}.ipynb", as_version=4)
    nbformat.validate(notebook)
    cells = {cell.id: cell for cell in notebook.cells}
    for cell in notebook.cells:
        if cell.cell_type == "code":
            ast.parse(cell.source)
    # Publication cells must work without any registry configuration or bundle.
    scope = dict(REGISTER_MODEL=False, PROMOTE_MODEL=False)
    exec(cells["register"].source, scope)
    exec(cells["promote"].source, scope)
    assert scope["registered"] is None
    assert "register" not in scope and "promote" not in scope


@pytest.mark.parametrize("family", ["stgnn", "lightgbm"])
def test_notebook_reuse_rejects_wrong_target(family, tmp_path):
    notebook = json.loads((ROOT / f"notebooks/spatial_model/train_{family}.ipynb").read_text(encoding="utf-8"))
    cell = next(cell for cell in notebook["cells"] if cell["id"] == "train")
    bundle = tmp_path / "fold-0/bundle"
    bundle.mkdir(parents=True)
    (bundle / "bundle.json").write_text(json.dumps({"target": "pm25", "model_family": family,
                                                   "metadata": {"protocol": "loso", "fold": 0}}))
    scope = dict(RUN_TRAINING=False, RUN_ROOT=tmp_path, selected_folds=[0], MODEL_FAMILY=family,
                 TARGET="no2", PROTOCOL="loso", Path=Path, json=json)
    with pytest.raises(ValueError, match="settings"):
        exec("".join(cell["source"]), scope)
