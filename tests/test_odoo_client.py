import pytest
from unittest.mock import patch, MagicMock

from zira_dashboard import odoo_client


def test_authenticate_raises_when_env_vars_missing(monkeypatch):
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_LOGIN", "ODOO_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(odoo_client.OdooConfigError):
        odoo_client.authenticate()


def test_authenticate_returns_uid_on_success(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "https://example.odoo.com")
    monkeypatch.setenv("ODOO_DB", "Production")
    monkeypatch.setenv("ODOO_LOGIN", "dale@example.com")
    monkeypatch.setenv("ODOO_API_KEY", "secret-key")
    fake_common = MagicMock()
    fake_common.authenticate.return_value = 42
    with patch("xmlrpc.client.ServerProxy", return_value=fake_common) as proxy:
        odoo_client._reset_cache_for_tests()
        uid = odoo_client.authenticate()
    assert uid == 42
    proxy.assert_called_with("https://example.odoo.com/xmlrpc/2/common")
    fake_common.authenticate.assert_called_with("Production", "dale@example.com", "secret-key", {})


def test_authenticate_raises_on_failure(monkeypatch):
    monkeypatch.setenv("ODOO_URL", "https://example.odoo.com")
    monkeypatch.setenv("ODOO_DB", "Production")
    monkeypatch.setenv("ODOO_LOGIN", "dale@example.com")
    monkeypatch.setenv("ODOO_API_KEY", "wrong")
    fake_common = MagicMock()
    fake_common.authenticate.return_value = False
    with patch("xmlrpc.client.ServerProxy", return_value=fake_common):
        odoo_client._reset_cache_for_tests()
        with pytest.raises(odoo_client.OdooAuthError):
            odoo_client.authenticate()
