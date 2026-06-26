from unittest.mock import MagicMock

import pytest

from zira_dashboard import forklift_client


def _json_response(body):
    r = MagicMock()
    r.json.return_value = body
    r.raise_for_status.return_value = None
    return r


def test_fetch_drivers_calls_api_path_and_returns_json(monkeypatch):
    monkeypatch.setenv("FORKLIFT_BASE_URL", "https://fk.example")
    monkeypatch.setenv("FORKLIFT_API_KEY", "gpifk__test")
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _json_response([{"id": "fk-1", "name": "Trent"}])

    monkeypatch.setattr(forklift_client.requests, "get", fake_get)

    drivers = forklift_client.fetch_drivers()

    assert drivers == [{"id": "fk-1", "name": "Trent"}]
    assert captured["url"] == "https://fk.example/api/drivers"
    assert captured["headers"]["X-API-Key"] == "gpifk__test"


def test_default_base_url_when_unset(monkeypatch):
    monkeypatch.delenv("FORKLIFT_BASE_URL", raising=False)
    monkeypatch.delenv("FORKLIFT_API_KEY", raising=False)
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _json_response({"driverLeaderboard": []})

    monkeypatch.setattr(forklift_client.requests, "get", fake_get)

    forklift_client.fetch_dashboard()
    assert captured["url"] == "https://www.gpiforklift.com/api/dashboard"


def test_http_error_is_wrapped_in_forklift_error(monkeypatch):
    monkeypatch.setenv("FORKLIFT_BASE_URL", "https://fk.example")

    def fake_get(url, **kwargs):
        r = MagicMock()
        r.raise_for_status.side_effect = RuntimeError("boom")
        return r

    monkeypatch.setattr(forklift_client.requests, "get", fake_get)
    with pytest.raises(forklift_client.ForkliftError):
        forklift_client.fetch_drivers()


def test_fetch_queue_history_uses_correct_path(monkeypatch):
    monkeypatch.setenv("FORKLIFT_BASE_URL", "https://fk.example")
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _json_response([{"id": "call-1", "status": "completed"}])

    monkeypatch.setattr(forklift_client.requests, "get", fake_get)

    rows = forklift_client.fetch_queue_history()

    assert rows == [{"id": "call-1", "status": "completed"}]
    assert captured["url"] == "https://fk.example/api/queue/history"


def test_fetch_weekly_trends_uses_correct_path(monkeypatch):
    monkeypatch.setenv("FORKLIFT_BASE_URL", "https://fk.example")
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return _json_response({"weeks": []})

    monkeypatch.setattr(forklift_client.requests, "get", fake_get)

    trends = forklift_client.fetch_weekly_trends()

    assert trends == {"weeks": []}
    assert captured["url"] == "https://fk.example/api/report/weekly-trends"
