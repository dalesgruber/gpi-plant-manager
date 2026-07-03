from fastapi.testclient import TestClient

from zira_dashboard import api_keys
from zira_dashboard.app import app


client = TestClient(app)


def test_execute_requires_bearer_key(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    r = client.post("/api/v1/object/execute", json={"model": "plant.person", "method": "search_read"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "auth_required"


def test_execute_rejects_invalid_key(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.setattr(api_keys, "verify_key", lambda token: None)
    r = client.post(
        "/api/v1/object/execute",
        headers={"Authorization": "Bearer gpi_live_bad"},
        json={"model": "plant.person", "method": "search_read"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"


def test_models_lists_registered_models(monkeypatch):
    monkeypatch.setattr(
        api_keys,
        "verify_key",
        lambda token: {"id": 1, "name": "Test", "scopes": ["admin:*"], "allowed_ips": []},
    )
    r = client.get("/api/v1/object/models", headers={"Authorization": "Bearer gpi_live_good"})
    assert r.status_code == 200
    assert any(model["model"] == "plant.person" for model in r.json()["models"])
