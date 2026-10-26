from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pollution.models import TimeWindow

UTC = timezone.utc


@pytest.mark.parametrize("text", ["2026-10-13T05:00:00Z", "2026-10-13T05:00:00+00:00", "2026-10-13T12:00:00+07:00"])
def test_parse_normalises_to_utc(text):
    window = TimeWindow.parse(text)
    assert window.start == datetime(2026, 10, 13, 5, tzinfo=UTC)
    assert window.end - window.start == timedelta(hours=1)


@pytest.mark.parametrize("text", ["2026-10-13T05:30:00Z", "2026-10-13T05:00:00", "kemarin", ""])
def test_parse_rejects(text):
    with pytest.raises(ValueError):
        TimeWindow.parse(text)
