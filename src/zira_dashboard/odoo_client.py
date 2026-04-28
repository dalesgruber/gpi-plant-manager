"""Odoo XML-RPC client. Read-only access to hr.employee + hr_skills.

Configuration comes from environment variables:
- ODOO_URL  — base URL, e.g. https://gruber-pallets.odoo.com (no trailing /odoo)
- ODOO_DB   — database name
- ODOO_LOGIN — username (email)
- ODOO_API_KEY — Odoo API key (Settings → Users → Account Security)

Never log or echo these values.
"""

from __future__ import annotations

import os
import xmlrpc.client
from typing import Any


class OdooConfigError(RuntimeError):
    """Required env var is missing or malformed."""


class OdooAuthError(RuntimeError):
    """Odoo accepted the request but rejected our credentials."""


_uid_cache: int | None = None
_object_proxy: xmlrpc.client.ServerProxy | None = None


def _reset_cache_for_tests() -> None:
    """Clear cached uid + object proxy; tests call this between cases."""
    global _uid_cache, _object_proxy
    _uid_cache = None
    _object_proxy = None


def _config() -> tuple[str, str, str, str]:
    url = os.environ.get("ODOO_URL", "").rstrip("/")
    db = os.environ.get("ODOO_DB", "")
    login = os.environ.get("ODOO_LOGIN", "")
    key = os.environ.get("ODOO_API_KEY", "")
    missing = [k for k, v in (
        ("ODOO_URL", url), ("ODOO_DB", db),
        ("ODOO_LOGIN", login), ("ODOO_API_KEY", key),
    ) if not v]
    if missing:
        raise OdooConfigError(f"Missing env vars: {', '.join(missing)}")
    return url, db, login, key


def authenticate() -> int:
    global _uid_cache
    if _uid_cache is not None:
        return _uid_cache
    url, db, login, key = _config()
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, login, key, {})
    if not uid:
        raise OdooAuthError("Odoo rejected credentials")
    _uid_cache = uid
    return uid


def execute(model: str, method: str, *args: Any, **kwargs: Any) -> Any:
    """Run an XML-RPC call against `model.method(*args, **kwargs)`. Caches
    the object proxy across calls."""
    global _object_proxy
    url, db, _, key = _config()
    uid = authenticate()
    if _object_proxy is None:
        _object_proxy = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return _object_proxy.execute_kw(
        db, uid, key, model, method, list(args), kwargs
    )
