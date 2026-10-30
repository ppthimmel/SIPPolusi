"""TI-SE-01: validate worker/cache exchanges without a database."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from contracts import EdgeWeight, TimeWindow
from spatial_model.inference.cache import _as_frame, write_pollution_weight

UTC = dt.timezone.utc
START = dt.datetime(2026, 10, 27, 5, tzinfo=UTC)


def frame(**change):
    return pd.DataFrame([dict(edge_id=10, pm25_ugm3=0., no2_ugm3=np.nan, exposure_index=0.,
                              confidence_score=0., background_source=None, estimation_source="idw",
                              model_metadata={}) | change])


@pytest.mark.parametrize("field,value", [("pm25_ugm3", -1.), ("no2_ugm3", np.inf),
                                         ("exposure_index", -0.1), ("confidence_score", 1.1),
                                         ("edge_id", 1.5), ("edge_id", True),
                                         ("model_metadata", {"score": np.nan}), ("background_source", "unknown")])
def test_cache_rejects_invalid_values_before_copy(field, value):
    with pytest.raises(ValueError):
        _as_frame(frame(**{field: value}))


def test_cache_preserves_missing_pollutant_and_measured_zero():
    out = _as_frame(frame())
    assert out.loc[0, "pm25_ugm3"] == 0
    assert pd.isna(out.loc[0, "no2_ugm3"])


def test_cache_rejects_missing_nullable_integer_id():
    values = frame()
    values["edge_id"] = pd.Series([pd.NA], dtype="Int64")
    with pytest.raises(ValueError, match="edge_id"):
        _as_frame(values)


def test_worker_window_normalizes_offset_to_utc():
    start = dt.datetime(2026, 10, 27, 12, tzinfo=dt.timezone(dt.timedelta(hours=7)))
    w = TimeWindow(start, start + dt.timedelta(hours=1))
    assert w.start.isoformat() == "2026-10-27T05:00:00+00:00"
    assert w.end.isoformat() == "2026-10-27T06:00:00+00:00"


def test_cache_rejects_weight_from_another_window_before_transaction():
    w = EdgeWeight(10, START - dt.timedelta(hours=1), 0., None, 0., 0., None, "idw")
    with pytest.raises(ValueError, match="time_window"):
        write_pollution_weight(None, [w], TimeWindow.starting_at(START), "g1", "idw-v1")


@pytest.mark.parametrize("kwargs", [{"graph_version": ""}, {"model_version": ""}, {"coverage_ratio": 1.1}])
def test_cache_rejects_invalid_batch_metadata_before_transaction(kwargs):
    params = dict(graph_version="g1", model_version="idw-v1", coverage_ratio=1.) | kwargs
    with pytest.raises(ValueError, match="graph_version|model_version|coverage_ratio"):
        write_pollution_weight(None, frame(), TimeWindow.starting_at(START), **params)
