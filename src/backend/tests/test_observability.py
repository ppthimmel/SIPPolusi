"""TI-DO-08: a fallback or failed inference is logged and counted, not only returned as 200."""
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from log_config import RedactingFormatter
from main import app
from telemetry import METRICS
from test_baseline import write_export


class JsonLog(logging.Handler):
    """Records as Railway receives them (JSON lines)."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.formatter_ = RedactingFormatter(as_json=True, service="backend")
        self.entries = []

    def emit(self, record):
        if not record.name.startswith("httpx"):
            self.entries.append(json.loads(self.formatter_.format(record)))

    def events(self, name):
        return [e for e in self.entries if e.get("event") == name]


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write_export(Path(self.tmp.name))
        self.env = patch.dict(os.environ, {"OSM_GRAPH_DIR": self.tmp.name})
        self.env.start()
        self.log = JsonLog()
        root = logging.getLogger()
        self.old_level = root.level
        root.addHandler(self.log)
        root.setLevel(logging.DEBUG)
        METRICS.reset()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.payload = {"origin": {"lon": 0, "lat": 0}, "destination": {"lon": .001, "lat": 0}, "mode": "bike"}

    async def asyncTearDown(self):
        await self.client.aclose()
        logging.getLogger().removeHandler(self.log)
        logging.getLogger().setLevel(self.old_level)
        self.env.stop()
        self.tmp.cleanup()

    async def test_unavailable_policy_is_a_logged_and_counted_fallback(self):
        response = await self.client.post("/v1/routes", json=self.payload, headers={"X-Request-ID": "gw-route-1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "gw-route-1")
        [event] = self.log.events("route_inference")
        self.assertEqual(event["level"], "warn")
        self.assertEqual((event["outcome"], event["fallback_reason"]), ("fallback", "policy_unavailable"))
        self.assertEqual(event["correlation_id"], "gw-route-1")
        self.assertEqual(METRICS.value("sippolusi_route_fallback_total", reason="policy_unavailable"), 1)
        self.assertEqual(METRICS.value("sippolusi_route_requests_total", outcome="fallback"), 1)

    async def test_policy_exception_is_logged_with_the_request_correlation_id(self):
        with patch("main.solve_route", side_effect=RuntimeError("policy crashed")):
            response = await self.client.post("/v1/routes", json=self.payload)
        request_id = response.headers["X-Request-ID"]
        failure = [e for e in self.log.entries if e["level"] == "error" and "policy crashed" in e["message"]]
        self.assertEqual(len(failure), 1)
        self.assertEqual(failure[0]["correlation_id"], request_id)
        self.assertEqual(METRICS.value("sippolusi_route_inference_failures_total", reason="policy_error"), 1)
        [event] = self.log.events("route_inference")
        self.assertEqual((event["outcome"], event["fallback_reason"]), ("fallback", "policy_error"))

    async def test_route_failure_is_logged_as_failed(self):
        with patch.dict(os.environ, {"OSM_GRAPH_DIR": str(Path(self.tmp.name) / "missing")}):
            response = await self.client.post("/v1/routes", json=self.payload)
        self.assertEqual(response.status_code, 503)
        [event] = self.log.events("route_inference")
        self.assertEqual((event["outcome"], event["status"]), ("failed", "data_unavailable"))
        [request] = self.log.events("http_request")
        self.assertEqual((request["level"], request["status"], request["route"]), ("error", 503, "/v1/routes"))

    async def test_coordinates_are_not_logged(self):
        payload = {**self.payload, "origin": {"lon": 0.000123, "lat": 0.000456}}
        await self.client.post("/v1/routes", json=payload)
        logged = json.dumps(self.log.entries)
        self.assertNotIn("0.000123", logged)
        self.assertNotIn("0.000456", logged)

    async def test_metrics_endpoint_serves_prometheus_text(self):
        await self.client.post("/v1/routes", json=self.payload)
        await self.client.get("/health")
        metrics = await self.client.get("/metrics")
        self.assertTrue(metrics.headers["content-type"].startswith("text/plain"))
        self.assertIn("# TYPE sippolusi_http_requests_total counter", metrics.text)
        self.assertIn('sippolusi_route_fallback_total{reason="policy_unavailable"} 1', metrics.text)
        self.assertIn('route="/health",service="backend",status="200"} 1', metrics.text)
        # Probes are counted but logged at DEBUG only.
        health = [e for e in self.log.events("http_request") if e["route"] == "/health"]
        self.assertEqual([e["level"] for e in health], ["debug"])


if __name__ == "__main__":
    unittest.main()
