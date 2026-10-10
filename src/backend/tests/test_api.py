import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import app
from route_model.inference import solve_baseline_route
from test_baseline import write_export


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_export(Path(self.tmp.name))
        self.env = patch.dict(os.environ, {"OSM_GRAPH_DIR": self.tmp.name})
        self.env.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.payload = {"origin": {"lon": 0, "lat": 0},
                        "destination": {"lon": .001, "lat": 0}, "mode": "bike"}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env.stop()
        self.tmp.cleanup()

    async def test_baseline_and_unavailable_policy_fallback(self):
        baseline = solve_baseline_route(**self.payload)
        self.assertEqual(baseline["status"], "ok")
        self.assertEqual(baseline["route_source"], "baseline")
        fallback = await self.client.post("/v1/routes", json=self.payload)
        self.assertEqual(fallback.status_code, 200)
        self.assertEqual(fallback.json()["fallback_reason"], "policy_unavailable")
        self.assertFalse(fallback.json()["preference_applied"])

    async def test_invalid_request_and_same_location(self):
        invalid = await self.client.post("/v1/routes", json={**self.payload, "mode": "car"})
        self.assertEqual(invalid.status_code, 422)
        same = await self.client.post("/v1/routes", json={**self.payload, "destination": self.payload["origin"]})
        self.assertEqual(same.json()["status"], "same_location")
        self.assertIsNone(same.json()["route"])

    async def test_missing_dataset_and_out_of_coverage(self):
        outside = await self.client.post("/v1/routes", json={**self.payload, "origin": {"lon": 10, "lat": 10}})
        self.assertEqual(outside.status_code, 404)
        self.assertEqual(outside.json()["status"], "snap_failed")
        with patch.dict(os.environ, {"OSM_GRAPH_DIR": str(Path(self.tmp.name) / "missing")}):
            missing = await self.client.post("/v1/routes", json=self.payload)
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(missing.json()["status"], "data_unavailable")

    async def test_policy_exception_marks_fallback(self):
        with patch("main.solve_route", side_effect=RuntimeError("test failure")):
            response = await self.client.post("/v1/routes", json=self.payload)
        self.assertEqual(response.json()["route_source"], "fallback")
        self.assertEqual(response.json()["fallback_reason"], "policy_error")
