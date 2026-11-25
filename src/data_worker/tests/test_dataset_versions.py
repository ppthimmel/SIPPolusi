"""ds-v0.4.0: label exclusion windows and the supported dataset versions."""
import json

import pandas as pd
import pytest

from spatial_model.configs import SUPPORTED_DATASETS, snapshot_version
from spatial_model.data.snapshot.build import station_hour_labels


def _readings(times):
    return pd.DataFrame({"station_uuid": "s1", "metric": "PM25", "value": 20.0,
                         "ts_utc": pd.to_datetime(times, utc=True), "exclusion_reason": pd.NA})


def _cfg(exclude=None):
    scope = {"first_window": "2026-11-10T00:00:00Z", "end_window": "2026-11-20T00:00:00Z", "targets": ["pm25"]}
    if exclude is not None:
        scope["exclude_windows"] = exclude
    return {"scope": scope}


def test_exclude_windows_drops_labels_inside_and_keeps_the_boundaries():
    times = ["2026-11-11T03:30Z",   # window 03:00, before the gap: kept
             "2026-11-11T04:10Z",   # window 04:00 = start (inclusive): dropped
             "2026-11-13T12:00Z",   # inside: dropped
             "2026-11-16T06:30Z",   # window 06:00, last hour inside: dropped
             "2026-11-16T07:20Z"]   # window 07:00 = end (exclusive): kept
    labels = station_hour_labels(_readings(times), _cfg([["2026-11-11T04:00:00Z", "2026-11-16T07:00:00Z"]]))
    assert list(labels.time_window_start) == list(pd.to_datetime(["2026-11-11T03:00Z", "2026-11-16T07:00Z"], utc=True))


def test_without_exclude_windows_every_label_is_kept():
    times = ["2026-11-11T03:30Z", "2026-11-13T12:00Z", "2026-11-16T07:20Z"]
    assert len(station_hour_labels(_readings(times), _cfg())) == 3


def _manifest(tmp_path, version):
    (tmp_path / "manifest.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("version", SUPPORTED_DATASETS)
def test_snapshot_version_accepts_supported_datasets(tmp_path, version):
    assert snapshot_version(_manifest(tmp_path, version)) == version


def test_snapshot_version_rejects_unknown_dataset(tmp_path):
    with pytest.raises(ValueError, match="Unsupported dataset ds-v9.9.9"):
        snapshot_version(_manifest(tmp_path, "ds-v9.9.9"))


def test_stgnn_prepare_rejects_unknown_dataset(tmp_path, monkeypatch):
    import spatial_model.models.stgnn.data as stgnn_data
    monkeypatch.setattr(stgnn_data, "verify_snapshot", lambda *a: {})
    with pytest.raises(ValueError, match="Unsupported dataset"):
        stgnn_data.prepare(_manifest(tmp_path, "ds-v9.9.9"), "loso", 0, target="no2")


def test_lightgbm_evaluation_rejects_unknown_dataset(tmp_path):
    from spatial_model.evaluation.lightgbm.evaluate import evaluate
    (tmp_path / "metadata.json").write_text(json.dumps(dict(target="no2", dataset="ds-v9.9.9", fold=0)))
    with pytest.raises(ValueError, match="supported dataset"):
        evaluate("unused", tmp_path, tmp_path / "out")


def test_bundle_export_requires_a_supported_dataset(tmp_path, monkeypatch):
    from spatial_model.artifacts import bundles
    monkeypatch.setattr(bundles, "verify_snapshot", lambda *a: {})
    for metadata in ({"model_family": "lightgbm"}, {"model_family": "lightgbm", "dataset": "ds-v9.9.9"}):
        with pytest.raises(ValueError, match="supported dataset"):
            bundles.export_bundle(tmp_path, "no2", tmp_path, metadata)


def test_bundle_validation_rejects_unknown_dataset(tmp_path):
    from spatial_model.artifacts.bundles import validate_bundle
    (tmp_path / "bundle.json").write_text(json.dumps(dict(format_version=1, dataset="ds-v9.9.9", unit="ug/m3",
                                                          target="no2", hashes={})))
    with pytest.raises(ValueError, match="Unsupported model bundle contract"):
        validate_bundle(tmp_path)
