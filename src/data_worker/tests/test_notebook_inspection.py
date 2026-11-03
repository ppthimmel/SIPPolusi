"""Manual inspection must retain missing-fold coverage and reject stale plots."""
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[3]


def _cells(family):
    notebook = json.loads((ROOT / f"notebooks/spatial_model/train_{family}.ipynb").read_text(encoding="utf-8"))
    return {cell["id"]: "".join(cell["source"]) for cell in notebook["cells"]}


def _saved_fold(root, family, fold, *, stale=False):
    output = root / ("fixed-split" if fold is None else f"fold-{fold}")
    (output / "bundle").mkdir(parents=True)
    target = "pm25" if fold is None else "no2"
    metadata = dict(target=target, protocol="fixed_split" if fold is None else "loso", fold=fold)
    (output / "bundle/bundle.json").write_text(json.dumps(dict(
        target=target, model_family=family, metadata=metadata)), encoding="utf-8")
    (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    checkpoint = output / ("best.pt" if family == "stgnn" else "model.txt")
    checkpoint.write_bytes(b"updated" if stale else b"saved")
    evaluation = output / "evaluation"
    evaluation.mkdir()
    (evaluation / "evaluation.json").write_text(json.dumps(dict(
        model=family, splits=["test"], checkpoint_sha256=hashlib.sha256(b"saved").hexdigest(),
        metadata_sha256=hashlib.sha256((output / "metadata.json").read_bytes()).hexdigest())), encoding="utf-8")
    pd.DataFrame(dict(observed=[1., 2.], predicted=[2., 3.])).to_parquet(evaluation / "test_predictions.parquet")
    return output


@pytest.mark.parametrize("family", ["stgnn", "lightgbm"])
@pytest.mark.parametrize("has_complete_fold", [False, True])
def test_incomplete_loso_inspection_reaches_coverage(family, has_complete_fold, tmp_path, monkeypatch):
    complete = _saved_fold(tmp_path, family, 1) if has_complete_fold else None
    evaluator = importlib.import_module(f"spatial_model.evaluation.{family}.evaluate")
    calls = []
    monkeypatch.setattr(evaluator, "evaluate", lambda *args, **kwargs: calls.append(args))
    scope = dict(RUN_ROOT=tmp_path, RUN_TRAINING=False, RUN_EVALUATION=True,
                 TARGET="no2", MODEL_FAMILY=family, PROTOCOL="loso", selected_folds=[0, 1],
                 available=[0, 1], SNAPSHOT="unused", EVALUATE_TEST=True,
                 DEVICE="cpu", BATCH_SIZE=8, THREADS=2, Path=Path, json=json,
                 pd=pd, plt=plt, display=lambda value: None)
    cells = _cells(family)
    exec(cells["train"], scope)
    exec(cells["evaluate"], scope)
    # The first fold is unfinished; only the completed fold is evaluated.
    assert len(calls) == int(has_complete_fold)
    if has_complete_fold:
        assert calls[0][1] == (complete / "best.pt" if family == "stgnn" else complete)
    exec(cells["summary"], scope)
    coverage = scope["coverage"].set_index(["fold", "split"])
    assert coverage.loc[(0, "test"), "status"] == "missing"
    assert coverage.loc[(1, "test"), "status"] == ("available" if has_complete_fold else "missing")
    assert (tmp_path / "coverage.csv").exists()
    plt.close("all")


@pytest.mark.parametrize("family", ["stgnn", "lightgbm"])
def test_notebook_plot_uses_only_available_coverage(family, tmp_path, monkeypatch):
    stale = _saved_fold(tmp_path, family, 0, stale=True)
    current = _saved_fold(tmp_path, family, 1)
    reads = []
    read = pd.read_parquet
    def record_read(path, *args, **kwargs):
        reads.append(Path(path))
        return read(path, *args, **kwargs)
    monkeypatch.setattr(pd, "read_parquet", record_read)
    scope = dict(RUN_ROOT=tmp_path, selected_folds=[0, 1], evaluation_splits=("test",),
                 run_paths={0: stale, 1: current}, PROTOCOL="loso", available=[0, 1],
                 pd=pd, plt=plt, display=lambda value: None)
    exec(_cells(family)["summary"], scope)
    assert scope["first"] == current / "evaluation/test_predictions.parquet"
    assert stale / "evaluation/test_predictions.parquet" not in reads
    plt.close("all")


@pytest.mark.parametrize("family", ["stgnn", "lightgbm"])
def test_notebook_fixed_split_plot_handles_none_fold(family, tmp_path):
    output = _saved_fold(tmp_path, family, None)
    scope = dict(RUN_ROOT=tmp_path, selected_folds=[None], evaluation_splits=("test",),
                 run_paths={None: output}, PROTOCOL="fixed_split",
                 pd=pd, plt=plt, display=lambda value: None)
    exec(_cells(family)["summary"], scope)
    assert scope["first"] == output / "evaluation/test_predictions.parquet"
    plt.close("all")


@pytest.mark.parametrize("target", ["pm25", "no2"])
def test_promote_rejects_existing_registered_fold(target, monkeypatch):
    from spatial_model.artifacts import registry
    from spatial_model.inference import serving
    calls = []
    monkeypatch.setattr(registry, "resolve", lambda *args: (Path("registered"), "version:1"))
    monkeypatch.setattr(serving, "load_model", lambda *args: SimpleNamespace(metadata={"fold": 0}))
    monkeypatch.setattr(registry, "client", lambda: SimpleNamespace(
        set_registered_model_alias=lambda *args: calls.append(args)))
    with pytest.raises(ValueError, match="LOSO"):
        registry.promote(target, "1")
    assert calls == []


@pytest.mark.parametrize("target", ["pm25", "no2"])
def test_promote_allows_non_fold_legacy_metadata(target, monkeypatch):
    from spatial_model.artifacts import registry
    from spatial_model.inference import serving
    calls = []
    monkeypatch.setattr(registry, "resolve", lambda *args: (Path("registered"), "version:1"))
    monkeypatch.setattr(serving, "load_model", lambda *args: SimpleNamespace(metadata={}))
    monkeypatch.setattr(registry, "client", lambda: SimpleNamespace(
        set_registered_model_alias=lambda *args: calls.append(args)))
    assert registry.promote(target, "1") == "version:1"
    assert calls == [(registry.NAMES[target], "production", "1")]
