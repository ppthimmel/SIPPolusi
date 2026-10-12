"""Guard sensor leakage and the matched IDW comparison in the standalone experiment."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from spatial_model.idw import idw_interpolate

REPO = Path(__file__).resolve().parents[3]
NOTEBOOK = REPO / "src/data_worker/spatial_model/stgnn/train_idw_residual.ipynb"


@pytest.fixture(scope="module")
def experiment():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    scope = {"display": lambda value: None}
    for cell in notebook["cells"]:
        source = "".join(cell["source"])
        if cell["cell_type"] == "markdown":
            if source.strip() == "## Training setup":
                break
        else:
            exec(compile(source, str(NOTEBOOK), "exec"), scope)
            if "DATA_DIR = Path(" in source:
                scope["DATA_DIR"] = REPO / "docs/experiments/ds-v0.1.0"
    return scope


def test_interpolation_matches_shared_idw(experiment):
    rng = np.random.default_rng(12)
    sources = rng.normal(size=(15, 2)) * 1000
    destinations = np.vstack([sources[0], rng.normal(size=(8, 2)) * 1000])
    values = rng.uniform(0, 100, 15)
    values[3] = np.nan
    distances = np.linalg.norm(destinations[:, None] - sources[None], axis=-1)
    actual = experiment["idw_features"](distances, values)
    expected = idw_interpolate(sources, values, destinations, power=2, neighbors=8)
    np.testing.assert_allclose(actual[:, 0], expected["value"], rtol=1e-6)
    assert actual[0, 0] == pytest.approx(values[0])
    assert np.isnan(experiment["idw_features"](distances, np.full(15, np.nan))).all()


def test_target_group_cannot_reach_sensor_inputs(experiment):
    e = experiment
    sources = e["source_stations"]
    eligible = set(e["training_stations"].uuid)
    assert set(sources.uuid) <= eligible
    source_groups = sources.group_id.to_numpy()
    for label in e["split_labels"]["train"].drop_duplicates("station_uuid").itertuples():
        target_group = source_groups == label.group_id
        position = e["station_position"][label.station_uuid]
        assert np.isinf(e["station_distances"][position, target_group]).all()
        node = int(label.node_index)
        edges = e["edge_index"].numpy()
        incoming = edges[0, edges[1] == node]
        context = np.unique(np.r_[node, incoming])
        assert not e["source_allowed"][np.ix_(context, target_group)].any()


def test_idw_uses_exact_published_test_pairs(experiment):
    directory = REPO / "docs/experiments/TI-AI-04/runs/20261010T133746Z-idw-ds-v0.1.0-f6f15b8f"
    published = pd.read_parquet(directory / "predictions_fixed_split_pm25.parquet")
    published["time_utc"] = published.window_start_utc + pd.Timedelta(hours=1)
    actual = experiment["split_labels"]["test"]
    paired = actual.merge(published, on=["station_uuid", "time_utc"], validate="one_to_one")
    assert len(paired) == len(actual) == len(published) == 756
    np.testing.assert_array_equal(paired.value_ugm3, paired.observed)
    np.testing.assert_allclose(paired.idw_prediction, paired.predicted, rtol=1e-6, atol=1e-5)


def test_initial_model_is_idw_and_skips_are_accounted_for(experiment):
    e = experiment
    original = pd.read_parquet(e["DATA_DIR"] / "splits/train.parquet")
    original = original[(original.target == "pm25") & (original.protocol == "fixed_split")]
    assert len(e["split_labels"]["train"]) + len(e["skipped_training_labels"]) == len(original)
    assert len(e["skipped_training_labels"]) == 1
    table = e["split_labels"]["val"]
    expected = np.sqrt(np.mean((table.idw_prediction - table.value_ugm3) ** 2))
    assert e["validation_rmse"]() == pytest.approx(expected, rel=1e-6)


@pytest.mark.parametrize("zero_correction", [False, True])
def test_evaluation_reports_both_methods_on_identical_rows(zero_correction):
    baseline = np.array([12.0, 24.0, 36.0])
    corrected = baseline if zero_correction else np.array([11.0, 22.0, 33.0])
    source = "idw" if zero_correction else "idw_stgnn_residual"
    frame = pd.DataFrame(dict(station_uuid=["a", "a", "b"], observed=[10., 20., 30.],
                              idw_prediction=baseline, predicted=corrected,
                              clipped=False, estimation_source=source))
    scope = dict(np=np, pd=pd, display=lambda value: None,
                 artifact={"metadata": {"estimation_source": source}},
                 prediction_tables={"val": frame.copy(), "test": frame.copy()})
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    selected = False
    for cell in notebook["cells"]:
        text = "".join(cell["source"])
        if cell["cell_type"] == "markdown":
            if text.strip() == "## Save evaluation":
                break
            selected |= text.strip() == "## Metric calculation"
        elif selected:
            exec(text, scope)
    metrics = scope["overall_metrics"]
    assert len(metrics) == 4
    assert set(metrics.method) == {"IDW", "IDW-ST-GNN"}
    assert (metrics.n == 3).all()
    assert len(scope["station_metrics"]) == 8
    for split in ("val", "test"):
        paired = metrics[metrics.split == split].set_index("method")
        expected_ratio = 1.0 if zero_correction else 0.5
        assert paired.loc["IDW-ST-GNN", "rmse"] == pytest.approx(paired.loc["IDW", "rmse"] * expected_ratio)
        assert paired.loc["IDW-ST-GNN", "estimation_source"] == source
        tables = scope["evaluation_tables"]
        pd.testing.assert_series_equal(tables[(split, "IDW")].observed,
                                       tables[(split, "IDW-ST-GNN")].observed)
