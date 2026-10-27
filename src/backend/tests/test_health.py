from fastapi.testclient import TestClient

import main


def test_liveness_and_readiness_without_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("RAILWAY_ENVIRONMENT_NAME", raising=False)
    with TestClient(main.app) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "backend"}
        ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"] == {"database": "not_configured", "route_graph": "ok"}


def test_readiness_reports_unreachable_database_without_details(monkeypatch):
    # Nothing listens on port 1; the error text stays in the log, not the response.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:hunter2-secret@127.0.0.1:1/db")
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready", "service": "backend",
                            "checks": {"database": "unavailable", "route_graph": "ok"}}
    assert "hunter2" not in ready.text


def test_missing_route_graph_is_reported_but_does_not_gate_readiness(monkeypatch, tmp_path):
    # Public POST /v1/routes is served by the Cache API; the backend image ships no graph files
    # (the CI smoke test runs exactly that image and expects /health/ready to be 200).
    async def database_ok():
        return "ok"

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OSM_GRAPH_DIR", str(tmp_path))
    monkeypatch.setattr(main, "_check_database", database_ok)
    with TestClient(main.app) as client:
        ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "service": "backend",
                            "checks": {"database": "ok", "route_graph": "unavailable"}}
