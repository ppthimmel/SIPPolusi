"""Regression checks for elapsed-time ISPU comparison windows."""

import numpy as np
import pandas as pd
import pytest

from scripts.check_ispu_units import BREAKPOINTS, ISPU, check


def _export(root, observations, published):
    pd.DataFrame(observations, columns=["station_uuid", "metric", "ts_utc", "value", "qc"]).to_parquet(
        root / "observation.parquet", index=False)
    pd.DataFrame(published, columns=["station_uuid", "metric", "ts_utc", "ispu"]).to_parquet(
        root / "observation_ispu.parquet", index=False)


def _ispu(value):
    return float(np.interp(value, BREAKPOINTS["PM25"], ISPU))


def test_month_gap_does_not_supply_24_hour_coverage(tmp_path):
    times = list(pd.date_range("2026-01-01", periods=18, freq="h", tz="UTC"))
    times.append(pd.Timestamp("2026-02-01", tz="UTC"))
    _export(tmp_path, [("a", "PM25", t, 20.0, "") for t in times],
            [("a", "PM25", times[-1], _ispu(20.0))])
    assert check(tmp_path)["results"]["PM25"]["ugm3"]["n"] == 0


@pytest.mark.parametrize("valid_hours, expected", [(17, 0), (18, 1), (24, 1)])
def test_coverage_counts_distinct_hours_within_window(tmp_path, valid_hours, expected):
    times = pd.date_range("2026-01-01", periods=24, freq="h", tz="UTC")
    # Two readings per hour must still count as one hour of coverage.
    observations = [("a", "PM25", t + offset, 20.0, "")
                    for t in times[-valid_hours:]
                    for offset in (pd.Timedelta(0), pd.Timedelta(minutes=30))]
    _export(tmp_path, observations, [("a", "PM25", times[-1], _ispu(20.0))])
    result = check(tmp_path)["results"]["PM25"]["ugm3"]
    assert result["n"] == expected
    if expected:
        assert result["within_tolerance"] == 1.0


def test_window_excludes_old_values_and_keeps_stations_separate(tmp_path):
    times = pd.date_range("2026-01-01", periods=24, freq="h", tz="UTC").append(
        pd.date_range("2026-01-02T06:00", periods=18, freq="h", tz="UTC"))
    observations = []
    published = []
    for station, current in (("a", 20.0), ("b", 80.0)):
        observations += [(station, "PM25", t, 300.0 if i < 24 else current, "")
                         for i, t in enumerate(times)]
        published.append((station, "PM25", times[-1], _ispu(current)))
    _export(tmp_path, observations, published)
    result = check(tmp_path)["results"]["PM25"]["ugm3"]
    assert result["n"] == 2
    assert result["median_abs_diff"] == 0.0
    assert result["within_tolerance"] == 1.0
