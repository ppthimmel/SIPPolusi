"""The test collection (public_api/smoke.py, requests.http)."""
from __future__ import annotations

import pytest

from public_api import smoke

# Data-independent cases: validation, size and study-area checks come before any
# cache read, so they hold on the fake 5 x 5 grid too. The others need Jakarta data
# and are run against a deployment (README "Menguji").
OFFLINE = [c for c in smoke.CASES if all(status != 200 for status, _ in c.expect)
           and c.name != "Asal = tujuan"]


def test_http_file_is_current():
    assert smoke.HTTP_FILE.read_text(encoding="utf-8") == smoke.export_http(), \
        "requests.http usang: jalankan `python -m public_api.smoke --export-http`"


def test_collection_covers_the_acceptance_criteria():
    expected = {code for c in smoke.CASES for _, code in c.expect}
    assert {None, "invalid_request", "payload_too_large", "outside_study_area", "point_not_on_network",
            "no_route_found", "cache_unavailable"} <= expected
    assert {c.path for c in smoke.CASES} == {"/v1/routes", "/v1/exposure-surface"}


@pytest.mark.parametrize("case", OFFLINE, ids=[c.name for c in OFFLINE])
def test_offline_cases_pass_against_the_app(make_client, case):
    with make_client() as client:
        assert smoke.check(case, smoke.send(client, case)) is None
