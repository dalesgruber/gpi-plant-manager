"""Tests for the StratusTime client module.

Covers the auth-strategy selection logic. Real network calls are mocked.
"""
from unittest.mock import patch, MagicMock

import pytest

from zira_dashboard import stratustime_client as stc


@pytest.fixture
def env_creds(monkeypatch):
    monkeypatch.setenv("STRATUSTIME_SHARED_KEY", "test-key-uuid")
    monkeypatch.setenv("STRATUSTIME_WS_PASSWORD", "test-password")


def test_health_check_unconfigured_when_no_env_vars(monkeypatch):
    monkeypatch.delenv("STRATUSTIME_SHARED_KEY", raising=False)
    monkeypatch.delenv("STRATUSTIME_WS_PASSWORD", raising=False)
    result = stc.health_check()
    assert result["configured"] is False
    assert result["ok"] is False


def test_health_check_partial_config(monkeypatch):
    monkeypatch.setenv("STRATUSTIME_SHARED_KEY", "k")
    monkeypatch.delenv("STRATUSTIME_WS_PASSWORD", raising=False)
    result = stc.health_check()
    assert result["configured"] is False
    assert result["ok"] is False


def test_health_check_first_scheme_succeeds(env_creds):
    with patch.object(stc, "_try_request", return_value=(200, '{"Employees": []}')):
        result = stc.health_check()
    assert result["ok"] is True
    assert result["scheme"] == "basic"
    assert result["status"] == 200


def test_health_check_falls_through_to_second_scheme(env_creds):
    responses = iter([(401, "unauthorized"), (200, '{"Employees": []}'), (0, "")])
    with patch.object(stc, "_try_request", side_effect=lambda *a, **k: next(responses)):
        result = stc.health_check()
    assert result["ok"] is True
    assert result["scheme"] == "header-pair"
    assert len(result["attempts"]) == 2


def test_health_check_all_schemes_fail(env_creds):
    with patch.object(stc, "_try_request", return_value=(401, "Unauthorized")):
        result = stc.health_check()
    assert result["ok"] is False
    assert result["scheme"] is None
    assert len(result["attempts"]) == 3


def test_list_employees_returns_empty_when_unhealthy(env_creds):
    with patch.object(stc, "_try_request", return_value=(401, "nope")):
        result = stc.list_employees()
    assert result == []


def test_list_employees_unwraps_dict_response(env_creds):
    payload = '{"Employees": [{"id": 1}, {"id": 2}]}'
    with patch.object(stc, "_try_request", return_value=(200, payload)):
        result = stc.list_employees()
    assert result == [{"id": 1}, {"id": 2}]


def test_list_employees_handles_list_response(env_creds):
    payload = '[{"id": 1}, {"id": 2}]'
    with patch.object(stc, "_try_request", return_value=(200, payload)):
        result = stc.list_employees()
    assert result == [{"id": 1}, {"id": 2}]
