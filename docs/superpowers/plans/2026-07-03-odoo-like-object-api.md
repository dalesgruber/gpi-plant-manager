# Odoo-like Object API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved Odoo-like server-to-server object API, including secure per-app API keys, object method dispatch, initial Plant Manager models, audit logging, and a Settings page for key management.

**Architecture:** Add a route-separated API under `/api/v1/object/*` that bypasses the browser session middleware and authenticates itself with bearer API keys. Use a small object registry: the API core validates methods, domains, fields, scopes, envelopes, and audit records; each model adapter maps safe public fields to existing store/database helpers. Add an `API` section to the existing Settings page for creating, revoking, and viewing keys.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, Postgres via existing `zira_dashboard.db`, pytest, existing settings CSS/JS patterns, HMAC-SHA256 with `SESSION_SECRET` for generated API key verification.

## Global Constraints

- API routes live under `/api/v1/object/*`.
- Browser session cookies do not authenticate the object API.
- Each external app has its own key; no global shared key.
- API keys are generated as high-entropy `gpi_live_...` bearer tokens and only shown once.
- API key database storage contains prefix/hash/metadata only, never the plaintext key.
- Support Odoo-like methods: `fields_get`, `search`, `search_count`, `read`, `search_read`, `create`, `write`, and guarded `unlink`.
- Expose only registered models and adapter-declared fields; never raw tables or arbitrary Python methods.
- V1 domain language is implicit AND with these operators only: `=`, `!=`, `in`, `not in`, `ilike`, `not ilike`, `>`, `>=`, `<`, `<=`.
- Read limit defaults to 100 and caps at 1000.
- `unlink` requires `object:unlink` and is disabled unless a model explicitly opts in.
- Every object API call writes an audit summary.
- Add a Settings `API` section for key creation/revocation/listing.
- Do not re-enable public FastAPI `/docs`, `/redoc`, or `/openapi.json`.
- Follow TDD: write each failing test and verify it fails before production code.

---

## File Structure

- Create `src/zira_dashboard/api_keys.py`: API key generation, HMAC hashing, verification, scope checks, DB persistence, revocation, CLI entrypoint.
- Create `src/zira_dashboard/object_api.py`: domain filtering, field selection, method dispatch, error envelopes, audit helper, object registry.
- Create `src/zira_dashboard/object_models.py`: model adapters for `plant.person`, `plant.work_center`, `plant.schedule`, and `plant.time_off_request`.
- Create `src/zira_dashboard/routes/object_api.py`: FastAPI routes for execute, model list, and field discovery.
- Modify `src/zira_dashboard/_schema.py`: add `api_keys` and `api_audit_log` tables/indexes.
- Modify `src/zira_dashboard/auth.py`: bypass `/api/v1/object/` from browser session middleware.
- Modify `src/zira_dashboard/app.py`: include the object API router.
- Modify `src/zira_dashboard/routes/settings.py`: add `api` section context and form routes for key create/revoke.
- Modify `src/zira_dashboard/templates/settings.html`: add sidebar link and API key management panel.
- Modify `src/zira_dashboard/static/settings.css`: add small API-key table/token styles.
- Create `tests/test_api_keys.py`: pure key generation/hash/scope tests.
- Create `tests/test_object_api_core.py`: pure domain, field, method, envelope tests.
- Create `tests/test_object_api_models.py`: adapter behavior using monkeypatched stores/DB.
- Create `tests/test_object_api_routes.py`: route/auth/audit behavior via TestClient.
- Create `tests/test_settings_api_keys.py`: settings panel and key form behavior.
- Modify `tests/test_auth_middleware.py`: assert object API routes bypass session redirect.

---

### Task 1: API Key Storage, Hashing, and Scope Checks

**Files:**
- Modify: `src/zira_dashboard/_schema.py`
- Create: `src/zira_dashboard/api_keys.py`
- Test: `tests/test_api_keys.py`

**Interfaces:**
- Produces: `generate_key() -> str`
- Produces: `hash_key(token: str) -> str`
- Produces: `key_prefix(token: str) -> str`
- Produces: `create_key(name: str, scopes: list[str], created_by: str | None = None, allowed_ips: list[str] | None = None) -> tuple[int, str]`
- Produces: `verify_key(token: str) -> dict | None`
- Produces: `list_keys() -> list[dict]`
- Produces: `revoke_key(key_id: int) -> bool`
- Produces: `has_scope(row: dict, scope: str, model: str | None = None) -> bool`

- [ ] **Step 1: Write failing pure tests for generated key shape, HMAC stability, secret rotation, and scope matching**

```python
# tests/test_api_keys.py
import pytest

from zira_dashboard import api_keys


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-32-bytes-of-random-data!!")


def test_generate_key_has_live_prefix_and_random_body():
    one = api_keys.generate_key()
    two = api_keys.generate_key()
    assert one.startswith("gpi_live_")
    assert two.startswith("gpi_live_")
    assert one != two
    assert len(one) >= 50


def test_hash_key_is_stable_and_secret_bound(monkeypatch):
    token = "gpi_live_example-token"
    h1 = api_keys.hash_key(token)
    h2 = api_keys.hash_key(token)
    assert h1 == h2
    assert token not in h1
    monkeypatch.setenv("SESSION_SECRET", "different-secret-32-bytes-foo-foo!!")
    assert api_keys.hash_key(token) != h1


def test_key_prefix_is_safe_short_identifier():
    assert api_keys.key_prefix("gpi_live_abcdefghijklmnopqrstuvwxyz") == "gpi_live_abcdefgh"


def test_has_scope_accepts_admin_object_and_model_specific():
    assert api_keys.has_scope({"scopes": ["admin:*"]}, "object:write", "plant.person")
    assert api_keys.has_scope({"scopes": ["object:read"]}, "object:read", "plant.person")
    assert api_keys.has_scope({"scopes": ["model:plant.person:write"]}, "object:write", "plant.person")
    assert not api_keys.has_scope({"scopes": ["object:read"]}, "object:write", "plant.person")
    assert not api_keys.has_scope({"scopes": ["model:plant.schedule:write"]}, "object:write", "plant.person")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api_keys.py -v`

Expected: FAIL during import with `ImportError: cannot import name 'api_keys'`.

- [ ] **Step 3: Add API key tables to schema**

Add this block near `device_tokens` in `src/zira_dashboard/_schema.py`:

```sql
-- Server-to-server API keys for the Odoo-like object API.
CREATE TABLE IF NOT EXISTS api_keys (
  id           SERIAL PRIMARY KEY,
  name         TEXT NOT NULL,
  key_prefix   TEXT NOT NULL,
  key_hash     TEXT NOT NULL UNIQUE,
  scopes       JSONB NOT NULL DEFAULT '[]'::jsonb,
  allowed_ips  JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_used_at TIMESTAMPTZ,
  revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS api_keys_active_idx
  ON api_keys (key_hash) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS api_audit_log (
  id              BIGSERIAL PRIMARY KEY,
  api_key_id      INTEGER REFERENCES api_keys(id),
  app_name        TEXT NOT NULL,
  actor           TEXT,
  model           TEXT,
  method          TEXT,
  request_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
  status          TEXT NOT NULL,
  error_code      TEXT,
  duration_ms     INTEGER,
  client_ip       TEXT,
  user_agent      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_audit_log_created_idx
  ON api_audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS api_audit_log_key_idx
  ON api_audit_log (api_key_id, created_at DESC);
```

- [ ] **Step 4: Implement pure API key helpers**

Create `src/zira_dashboard/api_keys.py`:

```python
"""Server-to-server API keys for the Odoo-like object API."""
from __future__ import annotations

import argparse
import hmac
import json
import secrets
from hashlib import sha256
from typing import Any

from . import auth, db

KEY_PREFIX = "gpi_live_"


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(token: str) -> str:
    secret = auth._session_secret()
    digest = hmac.new(secret.encode(), token.encode(), sha256).hexdigest()
    return "hmac_sha256:" + digest


def key_prefix(token: str) -> str:
    return token[:16]


def _normalize_scopes(scopes: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    for scope in scopes or []:
        if isinstance(scope, str) and scope.strip():
            out.append(scope.strip())
    return out or ["object:read"]


def _normalize_ips(allowed_ips: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    for ip in allowed_ips or []:
        if isinstance(ip, str) and ip.strip():
            out.append(ip.strip())
    return out


def has_scope(row: dict, scope: str, model: str | None = None) -> bool:
    scopes = set(row.get("scopes") or [])
    if "admin:*" in scopes or scope in scopes:
        return True
    if model and scope.startswith("object:"):
        action = scope.split(":", 1)[1]
        return f"model:{model}:{action}" in scopes
    return False
```

- [ ] **Step 5: Implement DB-backed helpers and CLI**

Append to `src/zira_dashboard/api_keys.py`:

```python
def create_key(
    name: str,
    scopes: list[str],
    created_by: str | None = None,
    allowed_ips: list[str] | None = None,
) -> tuple[int, str]:
    clean_name = (name or "").strip()[:120]
    if not clean_name:
        raise ValueError("name required")
    token = generate_key()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO api_keys (name, key_prefix, key_hash, scopes, allowed_ips, created_by) "
            "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s) RETURNING id",
            (
                clean_name,
                key_prefix(token),
                hash_key(token),
                json.dumps(_normalize_scopes(scopes)),
                json.dumps(_normalize_ips(allowed_ips)),
                created_by,
            ),
        )
        row = cur.fetchone()
    return int(row["id"]), token


def verify_key(token: str | None) -> dict[str, Any] | None:
    if not token or not isinstance(token, str) or not token.startswith(KEY_PREFIX):
        return None
    try:
        hashed = hash_key(token)
    except RuntimeError:
        return None
    rows = db.query(
        "SELECT id, name, key_prefix, scopes, allowed_ips, created_at, last_used_at, revoked_at "
        "FROM api_keys WHERE key_hash = %s",
        (hashed,),
    )
    if not rows:
        return None
    row = rows[0]
    if row.get("revoked_at") is not None:
        return None
    try:
        db.execute(
            "UPDATE api_keys SET last_used_at = now() "
            "WHERE id = %s AND (last_used_at IS NULL OR last_used_at < now() - interval '5 minutes')",
            (row["id"],),
        )
    except Exception:
        pass
    return dict(row)


def list_keys() -> list[dict]:
    return db.query(
        "SELECT id, name, key_prefix, scopes, allowed_ips, created_by, "
        "created_at, last_used_at, revoked_at "
        "FROM api_keys ORDER BY created_at DESC, id DESC"
    )


def revoke_key(key_id: int) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET revoked_at = now() "
            "WHERE id = %s AND revoked_at IS NULL RETURNING id",
            (key_id,),
        )
        row = cur.fetchone()
    return row is not None


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m zira_dashboard.api_keys")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create")
    create.add_argument("name")
    create.add_argument("--scope", action="append", default=["admin:*"])
    create.add_argument("--created-by", default="cli")
    sub.add_parser("list")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("id", type=int)
    args = parser.parse_args(argv)
    db.init_pool()
    db.bootstrap_schema()
    if args.cmd == "create":
        key_id, token = create_key(args.name, args.scope, args.created_by)
        print(f"id={key_id}")
        print(token)
    elif args.cmd == "list":
        for row in list_keys():
            state = "revoked" if row.get("revoked_at") else "active"
            print(f"{row['id']}\t{state}\t{row['name']}\t{row['key_prefix']}\t{','.join(row.get('scopes') or [])}")
    elif args.cmd == "revoke":
        print("revoked" if revoke_key(args.id) else "not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
```

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_api_keys.py -v`

Expected: PASS.

Commit:

```bash
git add src/zira_dashboard/_schema.py src/zira_dashboard/api_keys.py tests/test_api_keys.py
git commit -m "feat(api): add server key storage"
```

---

### Task 2: Object API Core, Domains, Envelopes, and Audit

**Files:**
- Create: `src/zira_dashboard/object_api.py`
- Test: `tests/test_object_api_core.py`

**Interfaces:**
- Consumes: `api_keys.has_scope(row, scope, model)`
- Produces: `ObjectAPIError`
- Produces: `FieldSpec`
- Produces: `ObjectModel`
- Produces: `Registry`
- Produces: `apply_domain(records, domain, fields) -> list[dict]`
- Produces: `apply_order(records, order) -> list[dict]`
- Produces: `select_fields(records, fields) -> list[dict]`
- Produces: `execute(registry, key_row, payload, client=None) -> tuple[dict, int]`
- Produces: `audit_call(...) -> None`

- [ ] **Step 1: Write failing tests for domain filtering and field validation**

```python
# tests/test_object_api_core.py
import pytest

from zira_dashboard import object_api


FIELDS = {
    "id": object_api.FieldSpec("integer", "ID", readonly=True),
    "name": object_api.FieldSpec("char", "Name"),
    "active": object_api.FieldSpec("boolean", "Active"),
    "score": object_api.FieldSpec("float", "Score"),
}


def test_apply_domain_supports_implicit_and_and_ilike():
    rows = [
        {"id": 1, "name": "Dale", "active": True, "score": 10},
        {"id": 2, "name": "Ian", "active": True, "score": 7},
        {"id": 3, "name": "Ada", "active": False, "score": 9},
    ]
    out = object_api.apply_domain(rows, [["active", "=", True], ["name", "ilike", "a"]], FIELDS)
    assert [r["id"] for r in out] == [1, 2]


def test_apply_domain_rejects_unknown_field_and_operator():
    with pytest.raises(object_api.ObjectAPIError) as e:
        object_api.apply_domain([{"id": 1}], [["secret", "=", 1]], FIELDS)
    assert e.value.code == "invalid_field"
    with pytest.raises(object_api.ObjectAPIError) as e:
        object_api.apply_domain([{"id": 1}], [["id", "like_regex", ".*"]], FIELDS)
    assert e.value.code == "invalid_domain"


def test_select_fields_rejects_private_fields():
    with pytest.raises(object_api.ObjectAPIError) as e:
        object_api.select_fields([{"id": 1}], ["id", "secret"], FIELDS)
    assert e.value.code == "invalid_field"
```

- [ ] **Step 2: Write failing tests for execute envelopes and scope checks**

```python
class DemoModel(object_api.ObjectModel):
    name = "demo.model"
    display_name = "Demo"
    fields = FIELDS
    writable_fields = {"name"}

    def _records(self):
        return [{"id": 1, "name": "Dale", "active": True, "score": 10}]

    def all_records(self, context):
        return list(self._records())

    def write_records(self, ids, values, context):
        return True


def _registry():
    reg = object_api.Registry()
    reg.register(DemoModel())
    return reg


def test_execute_search_read_returns_ok_result():
    payload = {"model": "demo.model", "method": "search_read", "args": [[["active", "=", True]]],
               "kwargs": {"fields": ["id", "name"]}}
    body, status = object_api.execute(_registry(), {"scopes": ["object:read"], "name": "Test"}, payload)
    assert status == 200
    assert body == {"ok": True, "result": [{"id": 1, "name": "Dale"}]}


def test_execute_write_requires_write_scope():
    payload = {"model": "demo.model", "method": "write", "args": [[1], {"name": "New"}]}
    body, status = object_api.execute(_registry(), {"scopes": ["object:read"], "name": "Test"}, payload)
    assert status == 403
    assert body["ok"] is False
    assert body["error"]["code"] == "access_denied"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_object_api_core.py -v`

Expected: FAIL during import with `ImportError: cannot import name 'object_api'`.

- [ ] **Step 4: Implement core classes and field/domain helpers**

Create `src/zira_dashboard/object_api.py`:

```python
"""Odoo-like object API core: safe model dispatch, domains, fields, envelopes."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from . import api_keys, db


class ObjectAPIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class FieldSpec:
    type: str
    string: str
    readonly: bool = False
    required: bool = False
    selection: list[str] | None = None

    def as_dict(self) -> dict:
        out = {
            "type": self.type,
            "string": self.string,
            "readonly": self.readonly,
            "required": self.required,
        }
        if self.selection is not None:
            out["selection"] = list(self.selection)
        return out
```

- [ ] **Step 5: Implement domain/order/field selection helpers**

Append to `src/zira_dashboard/object_api.py`:

```python
_OPS = {"=", "!=", "in", "not in", "ilike", "not ilike", ">", ">=", "<", "<="}


def _cmp(value: Any, op: str, expected: Any) -> bool:
    if op == "=":
        return value == expected
    if op == "!=":
        return value != expected
    if op == "in":
        return value in (expected or [])
    if op == "not in":
        return value not in (expected or [])
    if op == "ilike":
        return str(expected).lower() in str(value or "").lower()
    if op == "not ilike":
        return str(expected).lower() not in str(value or "").lower()
    if op in (">", ">=", "<", "<="):
        if value is None:
            return False
        if op == ">":
            return value > expected
        if op == ">=":
            return value >= expected
        if op == "<":
            return value < expected
        return value <= expected
    raise ObjectAPIError("invalid_domain", f"Unsupported operator: {op}", 400)


def apply_domain(records: list[dict], domain: list | None, fields: dict[str, FieldSpec]) -> list[dict]:
    if domain in (None, []):
        return list(records)
    if not isinstance(domain, list):
        raise ObjectAPIError("invalid_domain", "Domain must be a list", 400)
    if len(domain) > 50:
        raise ObjectAPIError("invalid_domain", "Domain has too many clauses", 400)
    clauses: list[tuple[str, str, Any]] = []
    for clause in domain:
        if not isinstance(clause, list) or len(clause) != 3:
            raise ObjectAPIError("invalid_domain", "Each domain clause must be [field, operator, value]", 400)
        field, op, expected = clause
        if field not in fields:
            raise ObjectAPIError("invalid_field", f"Unknown field: {field}", 400)
        if op not in _OPS:
            raise ObjectAPIError("invalid_domain", f"Unsupported operator: {op}", 400)
        clauses.append((field, op, expected))
    return [r for r in records if all(_cmp(r.get(f), op, expected) for f, op, expected in clauses)]


def apply_order(records: list[dict], order: str | None) -> list[dict]:
    if not order:
        return list(records)
    parts = order.split()
    field = parts[0]
    desc = len(parts) > 1 and parts[1].lower() == "desc"
    return sorted(records, key=lambda r: (r.get(field) is None, r.get(field)), reverse=desc)


def select_fields(records: list[dict], wanted: list[str] | None, fields: dict[str, FieldSpec]) -> list[dict]:
    names = wanted or list(fields.keys())
    for name in names:
        if name not in fields:
            raise ObjectAPIError("invalid_field", f"Unknown field: {name}", 400)
    return [{name: row.get(name) for name in names} for row in records]
```

- [ ] **Step 6: Implement model base, registry, dispatch, and audit**

Append to `src/zira_dashboard/object_api.py`:

```python
class ObjectModel:
    name: str
    display_name: str
    fields: dict[str, FieldSpec]
    writable_fields: set[str] = set()
    allow_unlink: bool = False
    default_order: str = "id asc"

    def fields_get(self) -> dict:
        return {name: spec.as_dict() for name, spec in self.fields.items()}

    def all_records(self, context: dict) -> list[dict]:
        raise ObjectAPIError("method_not_allowed", "search/read not implemented", 400)

    def create_record(self, values: dict, context: dict):
        raise ObjectAPIError("method_not_allowed", "create not implemented", 400)

    def write_records(self, ids: list, values: dict, context: dict) -> bool:
        raise ObjectAPIError("method_not_allowed", "write not implemented", 400)

    def unlink_records(self, ids: list, context: dict) -> bool:
        raise ObjectAPIError("method_not_allowed", "unlink not implemented", 400)


class Registry:
    def __init__(self):
        self._models: dict[str, ObjectModel] = {}

    def register(self, model: ObjectModel) -> None:
        self._models[model.name] = model

    def get(self, name: str) -> ObjectModel:
        model = self._models.get(name)
        if model is None:
            raise ObjectAPIError("model_not_found", f"Unknown model: {name}", 404)
        return model

    def list_models(self, key_row: dict | None = None) -> list[dict]:
        out = []
        for model in self._models.values():
            out.append({
                "model": model.name,
                "name": model.display_name,
                "read": True,
                "write": bool(model.writable_fields),
                "unlink": bool(model.allow_unlink),
            })
        return sorted(out, key=lambda r: r["model"])


def _ok(result: Any) -> tuple[dict, int]:
    return {"ok": True, "result": result}, 200


def _err(exc: ObjectAPIError) -> tuple[dict, int]:
    return {
        "ok": False,
        "error": {"code": exc.code, "message": exc.message, "details": exc.details},
    }, exc.status


def _ids_arg(args: list) -> list:
    if not args or not isinstance(args[0], list):
        raise ObjectAPIError("invalid_request", "Expected ids list as first arg", 400)
    return args[0]


def _values_arg(args: list, index: int) -> dict:
    if len(args) <= index or not isinstance(args[index], dict):
        raise ObjectAPIError("invalid_request", "Expected values object", 400)
    return args[index]


def _check_write_fields(model: ObjectModel, values: dict) -> None:
    for name in values.keys():
        if name not in model.fields:
            raise ObjectAPIError("invalid_field", f"Unknown field: {name}", 400)
        if name not in model.writable_fields:
            raise ObjectAPIError("invalid_field", f"Field is read-only: {name}", 400)


def _read_scope(method: str) -> str:
    return "object:read" if method in {"fields_get", "search", "search_count", "read", "search_read"} else "object:write"


def execute(registry: Registry, key_row: dict, payload: dict, client: dict | None = None) -> tuple[dict, int]:
    try:
        if not isinstance(payload, dict):
            raise ObjectAPIError("invalid_request", "JSON body must be an object", 400)
        model_name = payload.get("model")
        method = payload.get("method")
        args = payload.get("args") or []
        kwargs = payload.get("kwargs") or {}
        context = payload.get("context") or {}
        if not isinstance(model_name, str) or not isinstance(method, str):
            raise ObjectAPIError("invalid_request", "model and method are required", 400)
        if not isinstance(args, list) or not isinstance(kwargs, dict) or not isinstance(context, dict):
            raise ObjectAPIError("invalid_request", "args must be list; kwargs/context must be objects", 400)
        model = registry.get(model_name)
        scope = _read_scope(method)
        if method == "unlink":
            scope = "object:unlink"
        if not api_keys.has_scope(key_row, scope, model_name):
            raise ObjectAPIError("access_denied", f"API key does not allow {scope}", 403)
        if method == "fields_get":
            return _ok(model.fields_get())
        if method in {"search", "search_count", "search_read"}:
            domain = args[0] if args else []
            records = apply_domain(model.all_records(context), domain, model.fields)
            records = apply_order(records, kwargs.get("order") or model.default_order)
            if method == "search_count":
                return _ok(len(records))
            offset = max(0, int(kwargs.get("offset") or 0))
            limit = min(1000, max(0, int(kwargs.get("limit") or 100)))
            page = records[offset:offset + limit]
            if method == "search":
                return _ok([r.get("id") for r in page])
            return _ok(select_fields(page, kwargs.get("fields"), model.fields))
        if method == "read":
            ids = set(_ids_arg(args))
            records = [r for r in model.all_records(context) if r.get("id") in ids]
            return _ok(select_fields(records, kwargs.get("fields"), model.fields))
        if method == "create":
            values = _values_arg(args, 0)
            _check_write_fields(model, values)
            return _ok(model.create_record(values, context))
        if method == "write":
            ids = _ids_arg(args)
            values = _values_arg(args, 1)
            _check_write_fields(model, values)
            return _ok(model.write_records(ids, values, context))
        if method == "unlink":
            if not model.allow_unlink:
                raise ObjectAPIError("method_not_allowed", "unlink disabled for this model", 400)
            return _ok(model.unlink_records(_ids_arg(args), context))
        raise ObjectAPIError("method_not_allowed", f"Unknown method: {method}", 400)
    except ObjectAPIError as exc:
        return _err(exc)
    except Exception:
        return _err(ObjectAPIError("server_error", "Unexpected server error", 500))


def audit_call(
    *,
    key_row: dict | None,
    payload: dict,
    body: dict,
    status_code: int,
    started_at: float,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    try:
        err = None if body.get("ok") else (body.get("error") or {}).get("code")
        ctx = payload.get("context") if isinstance(payload, dict) else {}
        actor = ctx.get("actor") if isinstance(ctx, dict) else None
        kwargs = payload.get("kwargs") if isinstance(payload, dict) else {}
        args = payload.get("args") if isinstance(payload, dict) else []
        summary = {
            "fields": kwargs.get("fields") if isinstance(kwargs, dict) else None,
            "limit": kwargs.get("limit") if isinstance(kwargs, dict) else None,
            "offset": kwargs.get("offset") if isinstance(kwargs, dict) else None,
            "args_count": len(args) if isinstance(args, list) else None,
        }
        db.execute(
            "INSERT INTO api_audit_log "
            "(api_key_id, app_name, actor, model, method, request_summary, status, "
            "error_code, duration_ms, client_ip, user_agent) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)",
            (
                key_row.get("id") if key_row else None,
                key_row.get("name") if key_row else "unknown",
                actor,
                payload.get("model") if isinstance(payload, dict) else None,
                payload.get("method") if isinstance(payload, dict) else None,
                __import__("json").dumps(summary),
                "ok" if status_code < 400 else "error",
                err,
                int((time.perf_counter() - started_at) * 1000),
                client_ip,
                user_agent,
            ),
        )
    except Exception:
        pass
```

- [ ] **Step 7: Run tests and commit**

Run: `pytest tests/test_object_api_core.py -v`

Expected: PASS.

Commit:

```bash
git add src/zira_dashboard/object_api.py tests/test_object_api_core.py
git commit -m "feat(api): add object API core"
```

---

### Task 3: Initial Model Adapters

**Files:**
- Create: `src/zira_dashboard/object_models.py`
- Test: `tests/test_object_api_models.py`

**Interfaces:**
- Consumes: `object_api.ObjectModel`, `object_api.FieldSpec`
- Produces: `build_registry() -> object_api.Registry`
- Produces registered models: `plant.person`, `plant.work_center`, `plant.schedule`, `plant.time_off_request`

- [ ] **Step 1: Write failing tests for registry and person adapter**

```python
# tests/test_object_api_models.py
from datetime import date

from zira_dashboard import object_models


def test_registry_contains_initial_models():
    reg = object_models.build_registry()
    names = [m["model"] for m in reg.list_models()]
    assert "plant.person" in names
    assert "plant.work_center" in names
    assert "plant.schedule" in names
    assert "plant.time_off_request" in names


def test_person_model_reads_people_with_skills(monkeypatch):
    queries = []
    def fake_query(sql, params=None):
        queries.append(sql)
        return [
            {"id": 1, "odoo_id": 10, "name": "Dale", "active": True, "reserve": False,
             "excluded": False, "wage_type": "hourly", "spanish_speaker": False,
             "skills": {"Repair": 3}, "departments": ["Recycled"]},
        ]
    monkeypatch.setattr(object_models.db, "query", fake_query)
    model = object_models.PersonModel()
    assert model.all_records({})[0]["name"] == "Dale"
    assert model.all_records({})[0]["skills"] == {"Repair": 3}
```

- [ ] **Step 2: Write failing tests for schedule write and work center read**

```python
def test_work_center_model_uses_effective_settings(monkeypatch):
    loc = object_models.staffing.Location("Repair 1", "Repair", "Bay 1", "Recycled", "40721")
    monkeypatch.setattr(object_models.staffing, "LOCATIONS", (loc,))
    monkeypatch.setattr(object_models.work_centers_store, "effective", lambda l: {
        "goal_per_day": 50, "min_ops": 1, "max_ops": 2,
        "required_skills": ["Repair"], "note": "", "groups": ["A"],
        "department": "Recycled", "default_people": ["Dale"],
    })
    row = object_models.WorkCenterModel().all_records({})[0]
    assert row["id"] == "Repair 1"
    assert row["required_skills"] == ["Repair"]


def test_schedule_model_create_saves_schedule(monkeypatch):
    saved = {}
    monkeypatch.setattr(object_models.staffing, "load_schedule",
                        lambda day: object_models.staffing.Schedule(day=day, assignments={}))
    monkeypatch.setattr(object_models.staffing, "save_schedule",
                        lambda sched: saved.setdefault("schedule", sched))
    new_id = object_models.ScheduleModel().create_record({
        "day": "2026-07-06",
        "assignments": {"Repair 1": ["Dale"]},
        "notes": "note",
        "testing_day": True,
    }, {})
    assert new_id == "2026-07-06"
    assert saved["schedule"].assignments == {"Repair 1": ["Dale"]}
    assert saved["schedule"].testing_day is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_object_api_models.py -v`

Expected: FAIL during import with `ImportError: cannot import name 'object_models'`.

- [ ] **Step 4: Implement adapters**

Create `src/zira_dashboard/object_models.py`:

```python
"""Registered model adapters for the Odoo-like object API."""
from __future__ import annotations

from datetime import date
from typing import Any

from . import db, object_api, staffing, work_centers_store


class PersonModel(object_api.ObjectModel):
    name = "plant.person"
    display_name = "People"
    default_order = "name asc"
    writable_fields = {"active", "reserve", "excluded", "spanish_speaker"}
    fields = {
        "id": object_api.FieldSpec("integer", "ID", readonly=True),
        "odoo_id": object_api.FieldSpec("integer", "Odoo ID", readonly=True),
        "name": object_api.FieldSpec("char", "Name", readonly=True),
        "active": object_api.FieldSpec("boolean", "Active"),
        "reserve": object_api.FieldSpec("boolean", "Reserve"),
        "excluded": object_api.FieldSpec("boolean", "Excluded"),
        "wage_type": object_api.FieldSpec("char", "Wage Type", readonly=True),
        "spanish_speaker": object_api.FieldSpec("boolean", "Spanish Speaker"),
        "skills": object_api.FieldSpec("json", "Skills", readonly=True),
        "departments": object_api.FieldSpec("json", "Departments", readonly=True),
    }

    def all_records(self, context: dict) -> list[dict]:
        return db.query(
            "SELECT p.id, p.odoo_id, p.name, p.active, p.reserve, p.excluded, "
            "p.wage_type, p.spanish_speaker, "
            "COALESCE(jsonb_object_agg(s.name, ps.level) FILTER (WHERE s.name IS NOT NULL), '{}'::jsonb) AS skills, "
            "COALESCE(jsonb_agg(DISTINCT wc.department) FILTER (WHERE wc.department IS NOT NULL AND wc.department <> ''), '[]'::jsonb) AS departments "
            "FROM people p "
            "LEFT JOIN person_skills ps ON ps.person_id = p.id "
            "LEFT JOIN skills s ON s.id = ps.skill_id "
            "LEFT JOIN work_center_default_people wcdp ON wcdp.person_id = p.id "
            "LEFT JOIN work_centers wc ON wc.id = wcdp.wc_id "
            "GROUP BY p.id "
            "ORDER BY lower(p.name)"
        )

    def write_records(self, ids: list, values: dict, context: dict) -> bool:
        clean = {k: bool(v) for k, v in values.items() if k in self.writable_fields}
        if not ids or not clean:
            return True
        sets = ", ".join(f"{k} = %s" for k in clean.keys())
        db.execute(f"UPDATE people SET {sets}, local_dirty = TRUE WHERE id = ANY(%s)", (*clean.values(), ids))
        staffing._invalidate_roster_cache()
        return True
```

- [ ] **Step 5: Implement work center, schedule, and time-off adapters plus registry**

Append to `src/zira_dashboard/object_models.py`:

```python
class WorkCenterModel(object_api.ObjectModel):
    name = "plant.work_center"
    display_name = "Work Centers"
    default_order = "id asc"
    writable_fields = {"goal_per_day", "min_ops", "max_ops", "department", "groups", "required_skills", "default_people", "note"}
    fields = {
        "id": object_api.FieldSpec("char", "ID", readonly=True),
        "name": object_api.FieldSpec("char", "Name", readonly=True),
        "bay": object_api.FieldSpec("char", "Bay", readonly=True),
        "department": object_api.FieldSpec("char", "Department"),
        "groups": object_api.FieldSpec("json", "Groups"),
        "required_skills": object_api.FieldSpec("json", "Required Skills"),
        "default_people": object_api.FieldSpec("json", "Default People"),
        "goal_per_day": object_api.FieldSpec("integer", "Goal Per Day"),
        "min_ops": object_api.FieldSpec("integer", "Min Operators"),
        "max_ops": object_api.FieldSpec("integer", "Max Operators"),
        "note": object_api.FieldSpec("text", "Note"),
    }

    def _loc_by_id(self, value: str):
        return next((loc for loc in staffing.LOCATIONS if loc.name == value), None)

    def all_records(self, context: dict) -> list[dict]:
        rows = []
        for loc in staffing.LOCATIONS:
            eff = work_centers_store.effective(loc)
            rows.append({"id": loc.name, "name": loc.name, "bay": loc.bay, **eff})
        return rows

    def write_records(self, ids: list, values: dict, context: dict) -> bool:
        for raw_id in ids:
            loc = self._loc_by_id(str(raw_id))
            if loc is not None:
                work_centers_store.save_one(loc, values)
        return True


class ScheduleModel(object_api.ObjectModel):
    name = "plant.schedule"
    display_name = "Schedules"
    default_order = "day desc"
    writable_fields = {"day", "assignments", "notes", "work_center_notes", "testing_day", "published"}
    fields = {
        "id": object_api.FieldSpec("char", "ID", readonly=True),
        "day": object_api.FieldSpec("date", "Day", required=True),
        "published": object_api.FieldSpec("boolean", "Published"),
        "assignments": object_api.FieldSpec("json", "Assignments"),
        "notes": object_api.FieldSpec("text", "Notes"),
        "work_center_notes": object_api.FieldSpec("json", "Work Center Notes"),
        "testing_day": object_api.FieldSpec("boolean", "Testing Day"),
    }

    def _shape(self, day: date, sched: staffing.Schedule) -> dict:
        return {
            "id": day.isoformat(),
            "day": day.isoformat(),
            "published": bool(sched.published),
            "assignments": dict(sched.assignments or {}),
            "notes": sched.notes or "",
            "work_center_notes": dict(sched.wc_notes or {}),
            "testing_day": bool(sched.testing_day),
        }

    def all_records(self, context: dict) -> list[dict]:
        return [self._shape(day, sched) for day, sched in staffing.load_schedules_bulk()]

    def create_record(self, values: dict, context: dict):
        day = date.fromisoformat(str(values["day"]))
        current = staffing.load_schedule(day)
        sched = staffing.Schedule(
            day=day,
            published=bool(values.get("published", current.published)),
            assignments=dict(values.get("assignments") or current.assignments or {}),
            notes=str(values.get("notes", current.notes or "")),
            wc_notes=dict(values.get("work_center_notes") or current.wc_notes or {}),
            testing_day=bool(values.get("testing_day", current.testing_day)),
            custom_hours=current.custom_hours,
            published_snapshot=current.published_snapshot,
        )
        staffing.save_schedule(sched)
        return day.isoformat()

    def write_records(self, ids: list, values: dict, context: dict) -> bool:
        for raw_id in ids:
            day = date.fromisoformat(str(raw_id))
            current = staffing.load_schedule(day)
            merged = {
                "day": day.isoformat(),
                "published": current.published,
                "assignments": current.assignments,
                "notes": current.notes,
                "work_center_notes": current.wc_notes,
                "testing_day": current.testing_day,
            }
            merged.update(values)
            self.create_record(merged, context)
        return True


class TimeOffRequestModel(object_api.ObjectModel):
    name = "plant.time_off_request"
    display_name = "Time Off Requests"
    default_order = "start_date desc"
    fields = {
        "id": object_api.FieldSpec("integer", "ID", readonly=True),
        "person_odoo_id": object_api.FieldSpec("integer", "Person Odoo ID", readonly=True),
        "person_name": object_api.FieldSpec("char", "Person", readonly=True),
        "start_date": object_api.FieldSpec("date", "Start Date", readonly=True),
        "end_date": object_api.FieldSpec("date", "End Date", readonly=True),
        "shape": object_api.FieldSpec("char", "Shape", readonly=True),
        "hour_from": object_api.FieldSpec("float", "Hour From", readonly=True),
        "hour_to": object_api.FieldSpec("float", "Hour To", readonly=True),
        "status": object_api.FieldSpec("char", "Status", readonly=True),
        "source": object_api.FieldSpec("char", "Source", readonly=True),
    }

    def all_records(self, context: dict) -> list[dict]:
        rows = db.query(
            "SELECT r.id, r.person_odoo_id, COALESCE(p.name, '#' || r.person_odoo_id::text) AS person_name, "
            "r.date_from AS start_date, r.date_to AS end_date, r.shape, r.hour_from, r.hour_to, "
            "r.state AS status, CASE WHEN r.odoo_leave_id IS NULL THEN 'local' ELSE 'odoo' END AS source "
            "FROM time_off_requests r LEFT JOIN people p ON p.odoo_id = r.person_odoo_id "
            "ORDER BY r.date_from DESC, r.id DESC"
        )
        for row in rows:
            if hasattr(row.get("start_date"), "isoformat"):
                row["start_date"] = row["start_date"].isoformat()
            if hasattr(row.get("end_date"), "isoformat"):
                row["end_date"] = row["end_date"].isoformat()
        return rows


def build_registry() -> object_api.Registry:
    reg = object_api.Registry()
    reg.register(PersonModel())
    reg.register(WorkCenterModel())
    reg.register(ScheduleModel())
    reg.register(TimeOffRequestModel())
    return reg
```

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_object_api_models.py -v`

Expected: PASS.

Commit:

```bash
git add src/zira_dashboard/object_models.py tests/test_object_api_models.py
git commit -m "feat(api): register plant object models"
```

---

### Task 4: Object API Routes and Browser Auth Bypass

**Files:**
- Create: `src/zira_dashboard/routes/object_api.py`
- Modify: `src/zira_dashboard/auth.py`
- Modify: `src/zira_dashboard/app.py`
- Modify: `tests/test_auth_middleware.py`
- Test: `tests/test_object_api_routes.py`

**Interfaces:**
- Consumes: `api_keys.verify_key`
- Consumes: `object_models.build_registry`
- Consumes: `object_api.execute`
- Produces routes: `POST /api/v1/object/execute`, `GET /api/v1/object/models`, `GET /api/v1/object/models/{model}/fields`

- [ ] **Step 1: Add failing auth middleware test**

Append to `tests/test_auth_middleware.py`:

```python
def test_object_api_path_bypasses_session_redirect(mini_app):
    from starlette.responses import JSONResponse

    @mini_app.get("/api/v1/object/ping")
    def _api():
        return JSONResponse({"ok": True})

    c = TestClient(mini_app)
    r = c.get("/api/v1/object/ping", follow_redirects=False)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
```

- [ ] **Step 2: Write failing route tests**

```python
# tests/test_object_api_routes.py
from fastapi.testclient import TestClient

from zira_dashboard.app import app
from zira_dashboard import api_keys


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
    monkeypatch.setattr(api_keys, "verify_key", lambda token: {"id": 1, "name": "Test", "scopes": ["admin:*"], "allowed_ips": []})
    r = client.get("/api/v1/object/models", headers={"Authorization": "Bearer gpi_live_good"})
    assert r.status_code == 200
    assert any(m["model"] == "plant.person" for m in r.json()["models"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_auth_middleware.py::test_object_api_path_bypasses_session_redirect tests/test_object_api_routes.py -v`

Expected: middleware test FAILS with 302 redirect; route tests FAIL with 404.

- [ ] **Step 4: Bypass object API routes in session auth**

Modify `_BYPASS_PREFIXES` in `src/zira_dashboard/auth.py`:

```python
_BYPASS_PREFIXES = (
    "/auth/",
    "/static/",
    "/api/v1/object/",
)
```

- [ ] **Step 5: Implement object API routes**

Create `src/zira_dashboard/routes/object_api.py`:

```python
"""Server-to-server Odoo-like object API routes."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import api_keys, object_api, object_models

router = APIRouter(prefix="/api/v1/object")
_registry = object_models.build_registry()


def _auth_error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"code": code, "message": message, "details": {}}}, status_code=status)


def _bearer(request: Request) -> str | None:
    raw = request.headers.get("authorization") or ""
    if not raw.lower().startswith("bearer "):
        return None
    return raw.split(" ", 1)[1].strip()


def _key_row(request: Request) -> dict | JSONResponse:
    token = _bearer(request)
    if not token:
        return _auth_error("auth_required", "Bearer API key required", 401)
    row = api_keys.verify_key(token)
    if row is None:
        return _auth_error("invalid_api_key", "Invalid API key", 401)
    return row


@router.post("/execute")
async def execute(request: Request):
    started = time.perf_counter()
    key = _key_row(request)
    if isinstance(key, JSONResponse):
        return key
    try:
        payload = await request.json()
    except Exception:
        payload = {}
        body, status = {"ok": False, "error": {"code": "invalid_request", "message": "Invalid JSON", "details": {}}}, 400
    else:
        body, status = object_api.execute(_registry, key, payload, {
            "client_ip": request.client.host if request.client else None,
        })
    object_api.audit_call(
        key_row=key,
        payload=payload,
        body=body,
        status_code=status,
        started_at=started,
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return JSONResponse(body, status_code=status)


@router.get("/models")
async def models(request: Request):
    key = _key_row(request)
    if isinstance(key, JSONResponse):
        return key
    return JSONResponse({"ok": True, "models": _registry.list_models(key)})


@router.get("/models/{model_name}/fields")
async def model_fields(model_name: str, request: Request):
    key = _key_row(request)
    if isinstance(key, JSONResponse):
        return key
    body, status = object_api.execute(_registry, key, {"model": model_name, "method": "fields_get"})
    return JSONResponse(body, status_code=status)
```

- [ ] **Step 6: Include route in app**

Modify imports and includes in `src/zira_dashboard/app.py`:

```python
from .routes import (
    admin,
    api_layout,
    auth as auth_routes,
    changelog,
    dashboard,
    departments,
    exceptions,
    feedback,
    forklift_leaderboards,
    goat_watch,
    late_report,
    leaderboards,
    missing_wc,
    missed_punch_out,
    object_api,
    past_schedules,
    people,
    settings,
    share,
    skills,
    staffing,
    time_off,
    time_off_approvals,
    timeclock,
    timeclock_time_off,
    trophies,
    tv_displays,
    wc_dashboard,
)
```

Add before existing browser JSON routers:

```python
app.include_router(object_api.router)
```

- [ ] **Step 7: Run tests and commit**

Run:

```bash
pytest tests/test_auth_middleware.py::test_object_api_path_bypasses_session_redirect tests/test_object_api_routes.py -v
```

Expected: PASS.

Commit:

```bash
git add src/zira_dashboard/routes/object_api.py src/zira_dashboard/auth.py src/zira_dashboard/app.py tests/test_auth_middleware.py tests/test_object_api_routes.py
git commit -m "feat(api): expose object API routes"
```

---

### Task 5: Settings API Key Management Page

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py`
- Modify: `src/zira_dashboard/templates/settings.html`
- Modify: `src/zira_dashboard/static/settings.css`
- Test: `tests/test_settings_api_keys.py`

**Interfaces:**
- Consumes: `api_keys.list_keys`, `api_keys.create_key`, `api_keys.revoke_key`
- Produces: Settings section `?section=api`
- Produces: `POST /settings/api-keys`
- Produces: `POST /settings/api-keys/{key_id}/revoke`

- [ ] **Step 1: Write failing tests for section rendering and form contracts**

```python
# tests/test_settings_api_keys.py
from zira_dashboard.deps import templates


def _extract_api_section() -> str:
    import re
    from pathlib import Path
    html = Path("src/zira_dashboard/templates/settings.html").read_text()
    m = re.search(r"<section class=\"panel\" id=\"api-panel\".*?</section>", html, re.DOTALL)
    assert m, "api-panel section missing from settings.html"
    return m.group(0)


def test_api_settings_section_renders_keys_and_create_form():
    rendered = templates.env.from_string(_extract_api_section()).render(
        active_section="api",
        api_keys_rows=[{"id": 1, "name": "CRM", "key_prefix": "gpi_live_abcd",
                        "scopes": ["admin:*"], "allowed_ips": [],
                        "created_at": None, "last_used_at": None, "revoked_at": None}],
        new_api_key="gpi_live_once",
    )
    assert "API Keys" in rendered
    assert "gpi_live_once" in rendered
    assert 'action="/settings/api-keys"' in rendered
    assert 'action="/settings/api-keys/1/revoke"' in rendered
    assert 'name="scope_admin"' in rendered
```

- [ ] **Step 2: Write failing tests for route helpers**

```python
def test_api_scope_parser_defaults_to_read():
    from zira_dashboard.routes import settings
    assert settings._parse_api_key_scopes({}) == ["object:read"]
    assert settings._parse_api_key_scopes({"scope_admin": "on"}) == ["admin:*"]
    assert settings._parse_api_key_scopes({"scope_read": "on", "scope_write": "on"}) == ["object:read", "object:write"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_settings_api_keys.py -v`

Expected: FAIL because API panel and parser do not exist.

- [ ] **Step 4: Add settings parser and context**

Modify `src/zira_dashboard/routes/settings.py`:

```python
def _parse_api_key_scopes(form) -> list[str]:
    if form.get("scope_admin"):
        return ["admin:*"]
    scopes: list[str] = []
    if form.get("scope_read"):
        scopes.append("object:read")
    if form.get("scope_write"):
        scopes.append("object:write")
    if form.get("scope_unlink"):
        scopes.append("object:unlink")
    return scopes or ["object:read"]
```

Update section validation:

```python
if section not in ("work_centers", "integrations", "api", "roster_filter", "tvs", "timeclock", "time_off", "forklift", "diagnostics"):
    section = "work_centers"
```

Inside `settings_page`, add:

```python
api_keys_rows: list[dict] = []
new_api_key = request.session.pop("new_api_key", None) if hasattr(request, "session") else None
if section == "api":
    from .. import api_keys as _api_keys
    api_keys_rows = _api_keys.list_keys()
```

Add to template context:

```python
"api_keys_rows": api_keys_rows,
"new_api_key": new_api_key,
```

- [ ] **Step 5: Add settings routes**

Append to `src/zira_dashboard/routes/settings.py`:

```python
@router.post("/settings/api-keys")
async def settings_create_api_key(request: Request):
    from .. import api_keys as _api_keys
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    allowed_ips = [
        item.strip() for item in str(form.get("allowed_ips") or "").split(",")
        if item.strip()
    ]
    created_by = getattr(request.state, "user_upn", None) or "settings"
    key_id, token = await asyncio.to_thread(
        _api_keys.create_key, name, _parse_api_key_scopes(form), created_by, allowed_ips
    )
    request.session["new_api_key"] = token
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True, "id": key_id, "token": token})
    return RedirectResponse(url="/settings?saved=1&section=api", status_code=303)


@router.post("/settings/api-keys/{key_id}/revoke")
async def settings_revoke_api_key(key_id: int, request: Request):
    from .. import api_keys as _api_keys
    await asyncio.to_thread(_api_keys.revoke_key, key_id)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1&section=api", status_code=303)
```

- [ ] **Step 6: Add Settings sidebar and panel markup**

In `src/zira_dashboard/templates/settings.html`, add a sidebar link after Integrations:

```html
    <a href="?section=api"
       class="settings-nav-item {% if active_section == 'api' %}active{% endif %}">
      API
    </a>
```

Add this panel after Integrations:

```html
  <section class="panel" id="api-panel"
           {% if active_section != 'api' %}style="display:none"{% endif %}>
    <h2>API Keys</h2>
    {% if new_api_key %}
      <div class="api-token-once">
        <strong>New key:</strong>
        <code>{{ new_api_key }}</code>
      </div>
    {% endif %}
    <form method="post" action="/settings/api-keys" class="api-key-form">
      <label>Name
        <input type="text" name="name" maxlength="120" required placeholder="New CRM">
      </label>
      <label>Allowed IPs
        <input type="text" name="allowed_ips" placeholder="optional comma-separated IPs/CIDRs">
      </label>
      <div class="api-scope-grid">
        <label><input type="checkbox" name="scope_admin" checked> Admin</label>
        <label><input type="checkbox" name="scope_read"> Read</label>
        <label><input type="checkbox" name="scope_write"> Write</label>
        <label><input type="checkbox" name="scope_unlink"> Delete</label>
      </div>
      <button type="submit">Create key</button>
    </form>
    <table class="api-keys-table">
      <thead>
        <tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Last used</th><th>Status</th><th></th></tr>
      </thead>
      <tbody>
        {% for k in api_keys_rows %}
          <tr>
            <td>{{ k.name }}</td>
            <td><code>{{ k.key_prefix }}</code></td>
            <td>{{ k.scopes | join(', ') }}</td>
            <td>{{ k.last_used_at or '—' }}</td>
            <td>{% if k.revoked_at %}Revoked{% else %}Active{% endif %}</td>
            <td>
              {% if not k.revoked_at %}
                <form method="post" action="/settings/api-keys/{{ k.id }}/revoke">
                  <button type="submit">Revoke</button>
                </form>
              {% endif %}
            </td>
          </tr>
        {% else %}
          <tr><td colspan="6" class="hint">No API keys yet.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </section>
```

- [ ] **Step 7: Add small CSS**

Append to `src/zira_dashboard/static/settings.css`:

```css
.api-token-once {
  border: 1px solid #16a34a;
  background: #dcfce7;
  color: #14532d;
  border-radius: 8px;
  padding: 0.55rem 0.75rem;
  margin: 0.4rem 0 0.9rem;
}
.api-token-once code { display: block; margin-top: 0.35rem; word-break: break-all; }
.api-key-form {
  display: grid;
  grid-template-columns: minmax(12rem, 1fr) minmax(16rem, 1.5fr) auto;
  gap: 0.6rem;
  align-items: end;
  margin-bottom: 1rem;
}
.api-key-form input[type=text] {
  width: 100%;
  background: var(--panel-2);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 0.35rem 0.5rem;
  font: inherit;
}
.api-scope-grid {
  grid-column: 1 / -1;
  display: flex;
  gap: 0.8rem;
  flex-wrap: wrap;
}
.api-keys-table code { font-size: 0.8rem; }
```

- [ ] **Step 8: Run tests and commit**

Run: `pytest tests/test_settings_api_keys.py -v`

Expected: PASS.

Commit:

```bash
git add src/zira_dashboard/routes/settings.py src/zira_dashboard/templates/settings.html src/zira_dashboard/static/settings.css tests/test_settings_api_keys.py
git commit -m "feat(api): add settings key management"
```

---

### Task 6: Route Integration Tests with Real Dispatch and Audit

**Files:**
- Modify: `tests/test_object_api_routes.py`
- Modify: `src/zira_dashboard/object_api.py`
- Modify: `src/zira_dashboard/routes/object_api.py`

**Interfaces:**
- Consumes: object route from Task 4
- Produces: verified end-to-end behavior for `search_read`, `fields_get`, denied write, write success, audit logging, and no cookie auth

- [ ] **Step 1: Add failing tests for dispatch success, denied write, and audit call**

Append to `tests/test_object_api_routes.py`:

```python
def test_execute_search_read_dispatches_and_audits(monkeypatch):
    calls = []
    monkeypatch.setattr(api_keys, "verify_key",
                        lambda token: {"id": 1, "name": "CRM", "scopes": ["admin:*"], "allowed_ips": []})
    monkeypatch.setattr("zira_dashboard.object_models.PersonModel.all_records",
                        lambda self, ctx: [{"id": 1, "name": "Dale", "active": True}])
    monkeypatch.setattr("zira_dashboard.object_api.db.execute", lambda sql, params=None: calls.append((sql, params)))
    r = client.post(
        "/api/v1/object/execute",
        headers={"Authorization": "Bearer gpi_live_good"},
        json={"model": "plant.person", "method": "search_read", "args": [[["active", "=", True]]],
              "kwargs": {"fields": ["id", "name"]}, "context": {"actor": "Dale"}},
    )
    assert r.status_code == 200
    assert r.json()["result"] == [{"id": 1, "name": "Dale"}]
    assert calls and "api_audit_log" in calls[0][0]


def test_write_denied_without_scope(monkeypatch):
    monkeypatch.setattr(api_keys, "verify_key",
                        lambda token: {"id": 1, "name": "Reader", "scopes": ["object:read"], "allowed_ips": []})
    r = client.post(
        "/api/v1/object/execute",
        headers={"Authorization": "Bearer gpi_live_good"},
        json={"model": "plant.person", "method": "write", "args": [[1], {"active": False}]},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "access_denied"


def test_cookie_does_not_authenticate_object_api(fixed_secret=None):
    r = client.get("/api/v1/object/models", cookies={"gpi_session": "not-used"})
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests to verify any gaps fail**

Run: `pytest tests/test_object_api_routes.py -v`

Expected: FAIL if audit swallows monkeypatch incorrectly, model monkeypatch misses the registry instance, or cookie auth redirects.

- [ ] **Step 3: Make registry construction per-request so tests and future model additions are clean**

Replace `_registry = object_models.build_registry()` in `src/zira_dashboard/routes/object_api.py` with:

```python
def _registry():
    return object_models.build_registry()
```

Then change route calls from `_registry` to `_registry()`:

```python
body, status = object_api.execute(_registry(), key, payload, {...})
return JSONResponse({"ok": True, "models": _registry().list_models(key)})
body, status = object_api.execute(_registry(), key, {"model": model_name, "method": "fields_get"})
```

- [ ] **Step 4: Run route tests and commit**

Run: `pytest tests/test_object_api_routes.py -v`

Expected: PASS.

Commit:

```bash
git add src/zira_dashboard/object_api.py src/zira_dashboard/routes/object_api.py tests/test_object_api_routes.py
git commit -m "test(api): cover object route dispatch"
```

---

### Task 7: Security Hardening and Limits

**Files:**
- Modify: `src/zira_dashboard/routes/object_api.py`
- Modify: `src/zira_dashboard/object_api.py`
- Test: `tests/test_object_api_core.py`
- Test: `tests/test_object_api_routes.py`

**Interfaces:**
- Produces HTTPS enforcement in production for object routes.
- Produces IP allowlist check.
- Produces request body size guard.
- Produces order-field validation.

- [ ] **Step 1: Add failing tests for IP allowlist and HTTPS enforcement**

Append to `tests/test_object_api_routes.py`:

```python
def test_ip_allowlist_rejects_unlisted_client(monkeypatch):
    monkeypatch.setattr(api_keys, "verify_key",
                        lambda token: {"id": 1, "name": "Locked", "scopes": ["admin:*"], "allowed_ips": ["10.0.0.1"]})
    r = client.get("/api/v1/object/models", headers={"Authorization": "Bearer gpi_live_good"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "ip_not_allowed"


def test_https_required_in_production(monkeypatch):
    monkeypatch.setenv("REQUIRE_API_HTTPS", "1")
    monkeypatch.setattr(api_keys, "verify_key",
                        lambda token: {"id": 1, "name": "CRM", "scopes": ["admin:*"], "allowed_ips": []})
    r = client.get(
        "/api/v1/object/models",
        headers={"Authorization": "Bearer gpi_live_good", "x-forwarded-proto": "http"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "https_required"
```

- [ ] **Step 2: Add failing core test for invalid order field**

Append to `tests/test_object_api_core.py`:

```python
def test_apply_order_rejects_unknown_field():
    with pytest.raises(object_api.ObjectAPIError) as e:
        object_api.apply_order([{"id": 1}], "secret desc", FIELDS)
    assert e.value.code == "invalid_field"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_object_api_core.py::test_apply_order_rejects_unknown_field \
       tests/test_object_api_routes.py::test_ip_allowlist_rejects_unlisted_client \
       tests/test_object_api_routes.py::test_https_required_in_production -v
```

Expected: FAIL because guards are not implemented.

- [ ] **Step 4: Validate order fields in core**

Change `apply_order` signature in `src/zira_dashboard/object_api.py`:

```python
def apply_order(records: list[dict], order: str | None, fields: dict[str, FieldSpec]) -> list[dict]:
    if not order:
        return list(records)
    parts = order.split()
    field = parts[0]
    if field not in fields:
        raise ObjectAPIError("invalid_field", f"Unknown order field: {field}", 400)
    desc = len(parts) > 1 and parts[1].lower() == "desc"
    return sorted(records, key=lambda r: (r.get(field) is None, r.get(field)), reverse=desc)
```

Update execute call:

```python
records = apply_order(records, kwargs.get("order") or model.default_order, model.fields)
```

- [ ] **Step 5: Add route guards**

In `src/zira_dashboard/routes/object_api.py`, add imports:

```python
import ipaddress
import os
```

Add helpers:

```python
def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _ip_allowed(row: dict, request: Request) -> bool:
    allowed = row.get("allowed_ips") or []
    if not allowed:
        return True
    raw = _client_ip(request)
    if not raw:
        return False
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return False
    for item in allowed:
        try:
            if "/" in item:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
            elif addr == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def _https_ok(request: Request) -> bool:
    if os.environ.get("REQUIRE_API_HTTPS", "").strip().lower() not in ("1", "true", "yes"):
        return True
    return (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"
```

Inside `_key_row`, after verifying row:

```python
if not _https_ok(request):
    return _auth_error("https_required", "Object API requires HTTPS", 403)
if not _ip_allowed(row, request):
    return _auth_error("ip_not_allowed", "Client IP is not allowed for this key", 403)
```

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_object_api_core.py tests/test_object_api_routes.py -v`

Expected: PASS.

Commit:

```bash
git add src/zira_dashboard/object_api.py src/zira_dashboard/routes/object_api.py tests/test_object_api_core.py tests/test_object_api_routes.py
git commit -m "feat(api): harden object API access"
```

---

### Task 8: API Usage Documentation and Full Verification

**Files:**
- Create: `docs/object-api.md`
- Modify: `README.md`

**Interfaces:**
- Produces user-facing API examples for internal apps.

- [ ] **Step 1: Write docs**

Create `docs/object-api.md`:

```markdown
# Object API

The object API is a server-to-server API for trusted internal apps. It uses
Odoo-style model calls over JSON.

## Authentication

Create a key in Settings -> API. Send it as:

```http
Authorization: Bearer gpi_live_...
```

Keys are shown once. Store them in the calling app's server-side environment.

## Execute

```http
POST /api/v1/object/execute
Content-Type: application/json
Authorization: Bearer gpi_live_...
```

```json
{
  "model": "plant.person",
  "method": "search_read",
  "args": [[["active", "=", true]]],
  "kwargs": {"fields": ["id", "name"], "limit": 50}
}
```

## Models

- `GET /api/v1/object/models`
- `GET /api/v1/object/models/plant.person/fields`

## Methods

- `fields_get`
- `search`
- `search_count`
- `read`
- `search_read`
- `create`
- `write`
- `unlink` where explicitly enabled

## Domains

Domains are implicit AND lists:

```json
[["name", "ilike", "dale"], ["active", "=", true]]
```

Supported operators: `=`, `!=`, `in`, `not in`, `ilike`, `not ilike`, `>`,
`>=`, `<`, `<=`.
```

Add a README link under Layout or Setup:

```markdown
- `docs/object-api.md` — server-to-server Odoo-like API for internal apps.
```

- [ ] **Step 2: Run focused tests**

Run:

```bash
pytest tests/test_api_keys.py tests/test_object_api_core.py tests/test_object_api_models.py tests/test_object_api_routes.py tests/test_settings_api_keys.py tests/test_auth_middleware.py -v
```

Expected: PASS.

- [ ] **Step 3: Run lint/static check if available**

Run: `ruff check src/zira_dashboard tests`

Expected: PASS or only pre-existing warnings unrelated to files touched in this plan.

- [ ] **Step 4: Run broader tests**

Run: `pytest -v`

Expected: PASS, with known DB-gated skips when `DATABASE_URL` is unset.

- [ ] **Step 5: Commit**

```bash
git add docs/object-api.md README.md
git commit -m "docs(api): document object API usage"
```

---

### Task 9: Manual Polish Pass

**Files:**
- Modify only if verification finds issues: `src/zira_dashboard/templates/settings.html`, `src/zira_dashboard/static/settings.css`, `src/zira_dashboard/routes/settings.py`, `src/zira_dashboard/routes/object_api.py`

**Interfaces:**
- Produces: final working settings panel and object API smoke checks.

- [ ] **Step 1: Start the app locally**

Run:

```bash
AUTH_DISABLED=1 zira-dashboard
```

Expected: app starts on `http://0.0.0.0:8000` or fails clearly if local Postgres is unavailable.

- [ ] **Step 2: Smoke-test Settings API panel**

Open: `http://localhost:8000/settings?section=api`

Expected:
- API sidebar item is visible.
- Create-key form fits without overlap.
- Existing keys table is readable.
- Creating a key shows a one-time token and does not show the raw token later.
- Revoking a key updates the row state.

- [ ] **Step 3: Smoke-test API with generated key**

Run:

```bash
curl -s http://localhost:8000/api/v1/object/models \
  -H "Authorization: Bearer $GPI_API_KEY"
```

Expected: JSON with `ok: true` and the four initial models.

Run:

```bash
curl -s http://localhost:8000/api/v1/object/execute \
  -H "Authorization: Bearer $GPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"plant.person","method":"search_read","args":[[]],"kwargs":{"fields":["id","name"],"limit":5}}'
```

Expected: JSON with `ok: true` and up to five people.

- [ ] **Step 4: Fix any UI or smoke-test defects with TDD when behavior changes**

If the defect is behavioral, add or update a failing pytest first. If it is
pure CSS layout polish, patch CSS and verify visually.

- [ ] **Step 5: Final verification**

Run:

```bash
pytest tests/test_api_keys.py tests/test_object_api_core.py tests/test_object_api_models.py tests/test_object_api_routes.py tests/test_settings_api_keys.py tests/test_auth_middleware.py -v
pytest -v
ruff check src/zira_dashboard tests
```

Expected: PASS or documented pre-existing skips only.

- [ ] **Step 6: Commit polish when this task changes files**

```bash
git add src/zira_dashboard/templates/settings.html src/zira_dashboard/static/settings.css src/zira_dashboard/routes/settings.py src/zira_dashboard/routes/object_api.py tests/test_settings_api_keys.py tests/test_object_api_routes.py
git commit -m "polish(api): finish object API settings"
```
