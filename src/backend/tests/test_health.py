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


def test_readiness_fails_without_the_route_graph(monkeypatch, tmp_path):
    # The process stays live (Railway's deploy check passes) but must not receive route traffic.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("OSM_GRAPH_DIR", str(tmp_path))
    with TestClient(main.app) as client:
        assert client.get("/health").status_code == 200
        ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"]["route_graph"] == "unavailable"
