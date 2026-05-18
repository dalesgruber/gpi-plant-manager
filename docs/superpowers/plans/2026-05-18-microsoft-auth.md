# Microsoft Entra ID Auth + Device Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock every route behind Microsoft Entra ID single-sign-on (gruberpallets.com only) while keeping shop-floor TV displays running unattended via HMAC-signed device tokens.

**Architecture:** OIDC authorization-code flow via Authlib. Session as HTTP-only JWT cookie signed with `SESSION_SECRET` (HMAC-SHA256, 7-day sliding window). Device tokens are random+HMAC-signed strings stored in Postgres, valid only on `/tv/*` paths, instantly revocable. A single `_require_auth` ASGI middleware runs between `_security_headers` and `_static_cache_headers`, with a bypass list for `/auth/*`, `/static/*`, `/healthz`, `/robots.txt`, and `/favicon.ico`. An env var `AUTH_DISABLED=1` short-circuits the middleware for local dev and for the staged production rollout.

**Tech Stack:** FastAPI + Starlette middleware, Authlib (new dependency) for OIDC, `python-jose` (new dependency) for HMAC JWT sign/verify, psycopg2 for the device-tokens table, existing `db.bootstrap_schema()` for DDL.

**Spec reference:** [docs/superpowers/specs/2026-05-18-microsoft-auth-design.md](../specs/2026-05-18-microsoft-auth-design.md)

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `requirements.txt` | modify | Add `authlib`, `python-jose[cryptography]`, `itsdangerous` |
| `pyproject.toml` | modify | Mirror new deps in `[project] dependencies` |
| `src/zira_dashboard/auth.py` | create | OIDC config, JWT cookie helpers, domain validator, `AUTH_DISABLED` reader |
| `src/zira_dashboard/device_tokens.py` | create | Mint, verify, revoke device tokens (HMAC + DB) |
| `src/zira_dashboard/routes/auth.py` | create | `/auth/login`, `/auth/callback`, `/auth/logout` |
| `src/zira_dashboard/routes/admin.py` | modify | Append `/admin/devices` CRUD endpoints |
| `src/zira_dashboard/templates/auth_login.html` | create | "Sign in with Microsoft" landing page |
| `src/zira_dashboard/templates/auth_denied.html` | create | "Not authorized" / "Sign-in unavailable" page |
| `src/zira_dashboard/templates/admin_devices.html` | create | Device-token list/create/revoke admin UI |
| `src/zira_dashboard/app.py` | modify | Register session middleware, register `_require_auth` middleware, include `routes/auth` router |
| `src/zira_dashboard/db.py` | modify | Append `device_tokens` table to `_SCHEMA_DDL` |
| `tests/test_auth_session.py` | create | Session JWT mint/verify, domain validator, AUTH_DISABLED |
| `tests/test_auth_middleware.py` | create | Middleware redirects, bypass list, AUTH_DISABLED bypass |
| `tests/test_device_tokens.py` | create | HMAC sign/verify, mint/lookup/revoke |
| `tests/test_auth_routes.py` | create | `/auth/login` redirect shape, `/auth/logout` clears cookie |
| `CHANGELOG.md` | modify | One entry per sub-phase commit |

---

## Sub-phase 2A — Auth plumbing (ships with `AUTH_DISABLED=1` in Railway so users aren't locked out)

> **Before starting Task 1:** Tell Dale to add `AUTH_DISABLED=1` to Railway as a temporary env var. He'll remove it during the cutover. If skipped, the moment Task 9 lands in main, every user gets bounced to Microsoft login — including TV displays that don't have tokens yet.

### Task 1: Add OIDC + JWT dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add to `requirements.txt`**

```
psycopg2-binary>=2.9.9
weasyprint>=60.0
requests>=2.31.0
authlib>=1.3.0
python-jose[cryptography]>=3.3.0
itsdangerous>=2.2.0
```

- [ ] **Step 2: Add to `pyproject.toml` `[project] dependencies`**

```toml
dependencies = [
    "requests>=2.31",
    "pyyaml>=6.0",
    "python-dotenv>=1.0",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
    "tzdata>=2024.1; platform_system == 'Windows'",
    "psycopg2-binary>=2.9.9",
    "playwright>=1.40",
    "authlib>=1.3.0",
    "python-jose[cryptography]>=3.3.0",
    "itsdangerous>=2.2.0",
]
```

- [ ] **Step 3: Install locally**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pip install -e ".[dev]"`
Expected: installs Authlib, python-jose, itsdangerous; no errors.

- [ ] **Step 4: Verify imports work**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -c "import authlib; import jose.jwt; import itsdangerous; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pyproject.toml
git commit -m "chore: add authlib + python-jose + itsdangerous for OIDC auth"
```

### Task 2: Session JWT helpers (TDD)

**Files:**
- Create: `src/zira_dashboard/auth.py`
- Create: `tests/test_auth_session.py`

- [ ] **Step 1: Write the failing test**

`tests/test_auth_session.py`:
```python
import time
from datetime import timedelta

import pytest

from zira_dashboard import auth


def test_session_round_trip(monkeypatch):
    monkeypatch.setattr(auth, "_session_secret", lambda: "test-secret-32-bytes-of-random-data!!")
    token = auth.mint_session(sub="oid-abc", upn="dale@gruberpallets.com", name="Dale")
    payload = auth.verify_session(token)
    assert payload["sub"] == "oid-abc"
    assert payload["upn"] == "dale@gruberpallets.com"
    assert payload["name"] == "Dale"
    assert payload["exp"] > time.time()


def test_session_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(auth, "_session_secret", lambda: "test-secret-32-bytes-of-random-data!!")
    token = auth.mint_session(sub="oid-abc", upn="x@y.z", name="X")
    monkeypatch.setattr(auth, "_session_secret", lambda: "different-secret-aaaaaaaaaaaaaaaaa")
    assert auth.verify_session(token) is None


def test_session_rejects_expired(monkeypatch):
    monkeypatch.setattr(auth, "_session_secret", lambda: "test-secret-32-bytes-of-random-data!!")
    monkeypatch.setattr(auth, "SESSION_TTL", timedelta(seconds=-1))
    token = auth.mint_session(sub="oid-abc", upn="x@y.z", name="X")
    assert auth.verify_session(token) is None


def test_session_needs_refresh_when_close_to_expiry(monkeypatch):
    monkeypatch.setattr(auth, "_session_secret", lambda: "test-secret-32-bytes-of-random-data!!")
    monkeypatch.setattr(auth, "SESSION_TTL", timedelta(days=7))
    monkeypatch.setattr(auth, "SESSION_REFRESH_AT", timedelta(days=6))
    fresh = auth.mint_session(sub="x", upn="x@y.z", name="X")
    fresh_payload = auth.verify_session(fresh)
    assert auth.needs_refresh(fresh_payload) is False
    # Manually shift the payload's exp to be within the refresh window.
    fresh_payload = {**fresh_payload, "exp": int(time.time()) + 60}
    assert auth.needs_refresh(fresh_payload) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_session.py -v`
Expected: `ModuleNotFoundError: No module named 'zira_dashboard.auth'`.

- [ ] **Step 3: Create `src/zira_dashboard/auth.py` with session helpers**

```python
"""Authentication helpers: session JWT mint/verify + config + domain check.

This module is import-safe even when Microsoft env vars are missing — the
OIDC client is constructed lazily inside `oauth_client()`. Tests that only
exercise JWT helpers don't need any Microsoft config.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Any

from jose import JWTError, jwt

SESSION_COOKIE_NAME = "gpi_session"
SESSION_TTL = timedelta(days=7)
SESSION_REFRESH_AT = timedelta(days=6)  # refresh if remaining lifetime drops below this
_JWT_ALG = "HS256"

ALLOWED_DOMAIN = "gruberpallets.com"


def _session_secret() -> str:
    """Read SESSION_SECRET from env. Raises at *use* time, not import time,
    so tests + non-auth code paths can import this module freely."""
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET env var is not set. Generate one via "
            "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"` "
            "and add it to your environment."
        )
    return secret


def auth_disabled() -> bool:
    """Returns True when AUTH_DISABLED=1 (local dev / staged rollout).
    Logs a startup warning if set in production (see app.py)."""
    return os.environ.get("AUTH_DISABLED", "").strip() in ("1", "true", "yes")


def mint_session(*, sub: str, upn: str, name: str) -> str:
    """Sign a 7-day JWT with the user's Microsoft OID + UPN + display name."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "upn": upn,
        "name": name,
        "iat": now,
        "exp": now + int(SESSION_TTL.total_seconds()),
    }
    return jwt.encode(payload, _session_secret(), algorithm=_JWT_ALG)


def verify_session(token: str | None) -> dict[str, Any] | None:
    """Decode + verify a session JWT. Returns the payload on success, or
    None if the token is missing, malformed, expired, or signed with a
    different secret. Never raises."""
    if not token:
        return None
    try:
        return jwt.decode(token, _session_secret(), algorithms=[_JWT_ALG])
    except JWTError:
        return None
    except RuntimeError:
        # SESSION_SECRET not set — equivalent to "no valid session possible".
        return None


def needs_refresh(payload: dict[str, Any] | None) -> bool:
    """Returns True when the session has less than SESSION_REFRESH_AT
    remaining lifetime. Caller is responsible for actually re-issuing
    and re-setting the cookie."""
    if not payload or "exp" not in payload:
        return False
    remaining = int(payload["exp"]) - int(time.time())
    return remaining < int(SESSION_REFRESH_AT.total_seconds())


def domain_ok(upn_or_email: str | None) -> bool:
    """Allow only @gruberpallets.com identities."""
    if not upn_or_email:
        return False
    return upn_or_email.lower().endswith(f"@{ALLOWED_DOMAIN}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_session.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/auth.py tests/test_auth_session.py
git commit -m "feat(auth): session JWT mint/verify + domain validator + AUTH_DISABLED"
```

### Task 3: Domain validator tests (TDD)

**Files:**
- Modify: `tests/test_auth_session.py`

- [ ] **Step 1: Append domain tests**

```python
@pytest.mark.parametrize("upn,expected", [
    ("dale@gruberpallets.com", True),
    ("DALE@GRUBERPALLETS.COM", True),
    ("attacker@evil.com", False),
    ("dale@gruberpallets.com.evil.com", False),
    ("@gruberpallets.com", True),  # technically passes — Entra IDP guarantees a non-empty local part
    ("", False),
    (None, False),
    ("dalegruberpallets.com", False),
])
def test_domain_ok(upn, expected):
    assert auth.domain_ok(upn) is expected
```

- [ ] **Step 2: Run test**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_session.py::test_domain_ok -v`
Expected: 8 passed (parametrized).

- [ ] **Step 3: Commit**

```bash
git add tests/test_auth_session.py
git commit -m "test(auth): parametric domain validator coverage"
```

### Task 4: OIDC client setup

**Files:**
- Modify: `src/zira_dashboard/auth.py`

- [ ] **Step 1: Append OIDC client builder**

```python
# ---------- OIDC client (lazy) ----------

_oauth_singleton: Any = None


def oauth_client():
    """Construct and memoize the Authlib OAuth client for Microsoft Entra ID.

    Lazy because the env vars may not be present at module import time
    (tests, AUTH_DISABLED=1 dev runs). Raises a clear error if called
    without the env vars set."""
    global _oauth_singleton
    if _oauth_singleton is not None:
        return _oauth_singleton

    tenant = os.environ.get("MS_TENANT_ID")
    client_id = os.environ.get("MS_CLIENT_ID")
    client_secret = os.environ.get("MS_CLIENT_SECRET")
    missing = [k for k, v in (
        ("MS_TENANT_ID", tenant), ("MS_CLIENT_ID", client_id), ("MS_CLIENT_SECRET", client_secret),
    ) if not v]
    if missing:
        raise RuntimeError(
            f"Microsoft Entra ID env vars not set: {', '.join(missing)}. "
            "See docs/superpowers/specs/2026-05-18-microsoft-auth-design.md for setup."
        )

    from authlib.integrations.starlette_client import OAuth
    oauth = OAuth()
    oauth.register(
        name="azure",
        server_metadata_url=f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration",
        client_id=client_id,
        client_secret=client_secret,
        client_kwargs={"scope": "openid profile email"},
    )
    _oauth_singleton = oauth
    return oauth


def reset_oauth_client_for_tests() -> None:
    """Reset the memoized client. Tests that monkeypatch env vars should
    call this between tests; production never calls this."""
    global _oauth_singleton
    _oauth_singleton = None
```

- [ ] **Step 2: Add a basic smoke test**

`tests/test_auth_session.py` — append:
```python
def test_oauth_client_requires_env(monkeypatch):
    monkeypatch.delenv("MS_TENANT_ID", raising=False)
    monkeypatch.delenv("MS_CLIENT_ID", raising=False)
    monkeypatch.delenv("MS_CLIENT_SECRET", raising=False)
    auth.reset_oauth_client_for_tests()
    with pytest.raises(RuntimeError, match="MS_TENANT_ID"):
        auth.oauth_client()


def test_oauth_client_memoizes(monkeypatch):
    monkeypatch.setenv("MS_TENANT_ID", "00000000-0000-0000-0000-000000000000")
    monkeypatch.setenv("MS_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("MS_CLIENT_SECRET", "fake-secret")
    auth.reset_oauth_client_for_tests()
    a = auth.oauth_client()
    b = auth.oauth_client()
    assert a is b
```

- [ ] **Step 3: Run tests**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_session.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/auth.py tests/test_auth_session.py
git commit -m "feat(auth): lazy OIDC client builder + smoke tests"
```

### Task 5: Auth route handlers (login, callback, logout)

**Files:**
- Create: `src/zira_dashboard/routes/auth.py`
- Create: `src/zira_dashboard/templates/auth_login.html`
- Create: `src/zira_dashboard/templates/auth_denied.html`

- [ ] **Step 1: Create `src/zira_dashboard/templates/auth_login.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — GPI Plant Manager</title>
<link rel="stylesheet" href="/static/dashboard.css?v={{ static_v('dashboard.css') }}">
<style>
  body { display: flex; align-items: center; justify-content: center; min-height: 100vh; background: var(--bg, #f5f7fa); }
  .auth-card { background: var(--panel, #fff); padding: 2.5rem; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,.08); text-align: center; max-width: 22rem; }
  .auth-card h1 { margin: 0 0 .5rem; font-size: 1.4rem; }
  .auth-card p  { color: var(--muted, #64748b); margin: 0 0 1.5rem; font-size: .95rem; }
  .auth-card a  { display: inline-block; background: #0078d4; color: #fff; padding: .7rem 1.4rem; border-radius: 6px; text-decoration: none; font-weight: 600; }
  .auth-card a:hover { background: #106ebe; }
</style>
</head>
<body>
<div class="auth-card">
  <h1>GPI Plant Manager</h1>
  <p>Sign in with your @gruberpallets.com Microsoft account.</p>
  <a href="/auth/login{% if next %}?next={{ next|urlencode }}{% endif %}">Sign in with Microsoft</a>
</div>
</body>
</html>
```

- [ ] **Step 2: Create `src/zira_dashboard/templates/auth_denied.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not authorized — GPI Plant Manager</title>
<link rel="stylesheet" href="/static/dashboard.css?v={{ static_v('dashboard.css') }}">
<style>
  body { display: flex; align-items: center; justify-content: center; min-height: 100vh; background: var(--bg, #f5f7fa); }
  .auth-card { background: var(--panel, #fff); padding: 2.5rem; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,.08); text-align: center; max-width: 26rem; }
  .auth-card h1 { margin: 0 0 .5rem; font-size: 1.3rem; color: #b91c1c; }
  .auth-card p  { color: var(--muted, #475569); margin: 0 0 1rem; font-size: .95rem; line-height: 1.4; }
  .auth-card a  { color: #2563eb; }
</style>
</head>
<body>
<div class="auth-card">
  <h1>{{ title or "Not authorized" }}</h1>
  <p>{{ message or "This app is restricted to GPI employees with @gruberpallets.com Microsoft accounts." }}</p>
  <p><a href="/auth/login">Try signing in again</a></p>
</div>
</body>
</html>
```

- [ ] **Step 3: Create `src/zira_dashboard/routes/auth.py`**

```python
"""Authentication routes: /auth/login, /auth/callback, /auth/logout.

Login flow:
  /auth/login  → store ?next= in signed cookie → redirect to Microsoft
  /auth/callback ← Microsoft → validate token, set session cookie, redirect to next
  /auth/logout  → clear session cookie, redirect to home

State / CSRF is handled by Authlib's built-in `state` param. We store the
`next=` redirect target in a separate short-lived signed cookie because
Microsoft only echoes back `state` (which Authlib uses), not arbitrary
extra params.
"""
from __future__ import annotations

import logging
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

from .. import auth
from ..deps import templates

router = APIRouter()
_log = logging.getLogger(__name__)

_NEXT_COOKIE = "gpi_auth_next"
_NEXT_COOKIE_MAX_AGE = 300  # 5 minutes — round-trip to Microsoft is under a minute


def _next_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(auth._session_secret(), salt="auth-next")


def _safe_next(value: str | None, default: str = "/") -> str:
    """Only allow same-origin paths to prevent open-redirect attacks."""
    if not value or not value.startswith("/") or value.startswith("//"):
        return default
    return value


@router.get("/auth/login")
async def auth_login(request: Request, next: str | None = None):
    """Kick off the OIDC flow. Stashes ?next= in a signed cookie so we
    can redirect there after Microsoft sends the user back."""
    target = _safe_next(next, "/")
    redirect_uri = str(request.url_for("auth_callback"))
    response = await auth.oauth_client().azure.authorize_redirect(request, redirect_uri)
    response.set_cookie(
        _NEXT_COOKIE,
        _next_serializer().dumps(target),
        max_age=_NEXT_COOKIE_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


@router.get("/auth/callback", name="auth_callback")
async def auth_callback(request: Request):
    """Microsoft redirects here after the user signs in. Exchange the
    code for tokens, validate the domain, set the session cookie."""
    try:
        token = await auth.oauth_client().azure.authorize_access_token(request)
    except Exception as e:  # noqa: BLE001 — Authlib raises a variety of error types
        _log.warning("OIDC callback failed: %s", e)
        return templates.TemplateResponse(
            request, "auth_denied.html",
            {
                "title": "Sign-in failed",
                "message": "Something went wrong on the Microsoft side. Try signing in again.",
            },
            status_code=400,
        )

    userinfo = token.get("userinfo") or {}
    upn = userinfo.get("preferred_username") or userinfo.get("upn") or ""
    name = userinfo.get("name") or upn
    sub = userinfo.get("sub") or userinfo.get("oid") or ""

    if not auth.domain_ok(upn):
        # Do NOT log the upn — don't accumulate a list of non-GPI accounts
        # that tried to access this app.
        return templates.TemplateResponse(
            request, "auth_denied.html",
            {
                "title": "Not authorized",
                "message": "This app is restricted to GPI employees. Sign in with your @gruberpallets.com Microsoft account.",
            },
            status_code=403,
        )

    # Recover the original ?next= from the signed cookie.
    nxt = "/"
    raw = request.cookies.get(_NEXT_COOKIE)
    if raw:
        try:
            nxt = _safe_next(_next_serializer().loads(raw), "/")
        except BadSignature:
            nxt = "/"

    session_jwt = auth.mint_session(sub=sub, upn=upn, name=name)
    response = RedirectResponse(url=nxt, status_code=302)
    response.set_cookie(
        auth.SESSION_COOKIE_NAME, session_jwt,
        max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True, secure=True, samesite="lax", path="/",
    )
    response.delete_cookie(_NEXT_COOKIE, path="/")
    return response


@router.post("/auth/logout")
@router.get("/auth/logout")
async def auth_logout():
    """Clear the session cookie and redirect home. Note: this is local
    logout only — the user's Microsoft SSO session in their browser is
    unaffected, so signing in again is one click."""
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
    return response
```

- [ ] **Step 4: Sanity-check imports**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -c "from zira_dashboard.routes import auth as r; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/auth.py src/zira_dashboard/templates/auth_login.html src/zira_dashboard/templates/auth_denied.html
git commit -m "feat(auth): /auth/login, /auth/callback, /auth/logout"
```

### Task 6: Auth middleware (without device tokens yet)

**Files:**
- Modify: `src/zira_dashboard/auth.py`
- Create: `tests/test_auth_middleware.py`

- [ ] **Step 1: Write the failing test**

`tests/test_auth_middleware.py`:
```python
import os
import pytest
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from zira_dashboard import auth


@pytest.fixture
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-32-bytes-of-random-data!!")


@pytest.fixture
def mini_app(fixed_secret):
    app = FastAPI()
    app.add_middleware(auth.RequireAuthMiddleware)

    @app.get("/recycling")
    def _r(): return PlainTextResponse("ok")

    @app.get("/healthz")
    def _h(): return PlainTextResponse("ok")

    @app.get("/static/foo.css")
    def _s(): return PlainTextResponse("ok")

    @app.get("/auth/login")
    def _l(): return PlainTextResponse("login page")

    @app.get("/robots.txt")
    def _ro(): return PlainTextResponse("ok")

    return app


def test_unauthed_redirects_to_login(mini_app):
    c = TestClient(mini_app)
    r = c.get("/recycling", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/auth/login?next=%2Frecycling")


def test_authed_with_valid_cookie_passes_through(mini_app, fixed_secret):
    token = auth.mint_session(sub="x", upn="dale@gruberpallets.com", name="Dale")
    c = TestClient(mini_app, cookies={auth.SESSION_COOKIE_NAME: token})
    r = c.get("/recycling")
    assert r.status_code == 200
    assert r.text == "ok"


def test_bypass_list(mini_app):
    c = TestClient(mini_app)
    assert c.get("/healthz").status_code == 200
    assert c.get("/robots.txt").status_code == 200
    assert c.get("/static/foo.css").status_code == 200
    assert c.get("/auth/login").status_code == 200


def test_auth_disabled_env_var_bypasses_everything(mini_app, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "1")
    c = TestClient(mini_app)
    assert c.get("/recycling").status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_middleware.py -v`
Expected: `AttributeError: module 'zira_dashboard.auth' has no attribute 'RequireAuthMiddleware'`.

- [ ] **Step 3: Append middleware to `src/zira_dashboard/auth.py`**

```python
# ---------- ASGI middleware ----------

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

# Paths (or prefixes) that bypass auth entirely. Keep this list tight —
# every entry is a potential bypass for an attacker probing for an
# unauthenticated endpoint.
_BYPASS_PREFIXES = (
    "/auth/",      # login + callback + logout
    "/static/",    # CSS/JS/images
)
_BYPASS_EXACT = frozenset({
    "/healthz",
    "/robots.txt",
    "/favicon.ico",
})


def _is_bypass_path(path: str) -> bool:
    if path in _BYPASS_EXACT:
        return True
    return any(path.startswith(p) for p in _BYPASS_PREFIXES)


class RequireAuthMiddleware(BaseHTTPMiddleware):
    """Gate every request behind a valid session cookie.

    Device-token support for /tv/* paths is added in Sub-phase 2B by
    swapping this class for a subclass that also checks the URL param.
    Bypass list and AUTH_DISABLED logic stays the same.
    """

    async def dispatch(self, request, call_next):
        if auth_disabled():
            return await call_next(request)

        path = request.url.path
        if _is_bypass_path(path):
            return await call_next(request)

        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        payload = verify_session(cookie)
        if payload is None:
            # 302 to login with current path preserved.
            from urllib.parse import urlencode
            qs = urlencode({"next": path}) if path != "/" else ""
            target = "/auth/login" + (("?" + qs) if qs else "")
            return RedirectResponse(url=target, status_code=302)

        response = await call_next(request)

        # Sliding-window refresh: if cookie is close to expiry, re-issue.
        if needs_refresh(payload):
            fresh = mint_session(sub=payload["sub"], upn=payload["upn"], name=payload["name"])
            response.set_cookie(
                SESSION_COOKIE_NAME, fresh,
                max_age=int(SESSION_TTL.total_seconds()),
                httponly=True, secure=True, samesite="lax", path="/",
            )
        return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_middleware.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/auth.py tests/test_auth_middleware.py
git commit -m "feat(auth): RequireAuthMiddleware + bypass list + sliding refresh"
```

### Task 7: Wire middleware + auth routes into the real app

**Files:**
- Modify: `src/zira_dashboard/app.py`

- [ ] **Step 1: Add session middleware import + routes/auth import near other route imports**

In `app.py`, near the other `from .routes import ...` lines, add:
```python
from .routes import auth as auth_routes
```

- [ ] **Step 2: Add `RequireAuthMiddleware` after `_security_headers`**

After line 247 (end of `_security_headers` middleware), BEFORE the `_static_cache_headers` middleware, insert:

```python
# Auth gate — every request not in the bypass list must have a valid
# session cookie (or, after Sub-phase 2B, a valid device token on /tv/*).
# AUTH_DISABLED=1 short-circuits this in local dev and during staged rollout.
from .auth import RequireAuthMiddleware, auth_disabled
app.add_middleware(RequireAuthMiddleware)
if auth_disabled():
    import logging
    logging.getLogger(__name__).warning(
        "AUTH_DISABLED is set — every route is unauthenticated. "
        "Unset this env var to enforce authentication."
    )
```

> ⚠ Note: `app.add_middleware()` adds middleware in REVERSE registration order on the request side. Existing middleware (`_security_headers`, `_static_cache_headers`) were added with `@app.middleware("http")` and run in registration order. To keep ordering predictable we register `RequireAuthMiddleware` after them via the same decorator — but BaseHTTPMiddleware doesn't compose with `@app.middleware`. Use `app.add_middleware` instead and rely on the documented order: middleware added LAST runs FIRST on requests. So in our case `_security_headers` (registered first) runs FIRST on responses (last on requests), and `RequireAuthMiddleware` registered last runs FIRST on requests, which is what we want.

- [ ] **Step 3: Register the auth router**

Near the other `app.include_router(...)` calls, add:
```python
app.include_router(auth_routes.router)
```

- [ ] **Step 4: Smoke-test the wired app**

Run:
```powershell
& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -c @'
import os
os.environ.setdefault("SESSION_SECRET", "test-secret-32-bytes-of-random-data!!")
os.environ["AUTH_DISABLED"] = "1"
from fastapi.testclient import TestClient
from zira_dashboard.app import app
c = TestClient(app)
r = c.get("/auth/login", follow_redirects=False)
print("login GET status:", r.status_code)
r2 = c.get("/healthz")
print("healthz status:", r2.status_code)
'@
```
Expected:
- `/auth/login` returns 500 (no MS env vars set) OR an Authlib redirect → either is fine for this smoke test; the import-time success is what matters.
- `/healthz` returns 200 (bypass list works).

- [ ] **Step 5: Run full test suite**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: same pass/skip count as before plus our new tests.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(auth): wire RequireAuthMiddleware + auth routes into app"
```

### Task 8: CHANGELOG entry + ship Sub-phase 2A

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Prepend a new entry under today's date**

```markdown
### {{CURRENT_TIME}}

- **Auth Sub-phase 2A: Microsoft Entra ID OIDC plumbing landed (NOT YET ENFORCED)** — login/callback/logout routes at `/auth/*`, session JWT cookie helpers, `RequireAuthMiddleware` registered. While `AUTH_DISABLED=1` is set in Railway, every route still serves anonymously — so users see no change. Next: Sub-phase 2B adds device tokens for TV displays; Sub-phase 2C is the cutover where Dale unsets `AUTH_DISABLED` and the door closes for real.
```

Replace `{{CURRENT_TIME}}` with the actual time (e.g. `### 1:42 PM`) before committing.

- [ ] **Step 2: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "chore: changelog entry for auth sub-phase 2A"
git push origin claude/hardcore-herschel-a3ee7b:main
```

- [ ] **Step 3: Verify on Railway**

Wait for Railway redeploy. Visit `https://gpiplantmanager.com/healthz` → 200. Visit `https://gpiplantmanager.com/auth/login` → should redirect you to Microsoft. Sign in. You should land back on `/` with a session cookie set (verify in DevTools).

If the login round-trip works end-to-end, Sub-phase 2A is shipped successfully.

---

## Sub-phase 2B — Device tokens for TV displays

### Task 9: Postgres schema for device_tokens

**Files:**
- Modify: `src/zira_dashboard/db.py`

- [ ] **Step 1: Append DDL to `_SCHEMA_DDL`**

At the end of `_SCHEMA_DDL` (the big string starting at line 136), before the closing `"""`, append:

```sql

-- Long-lived signed device tokens for shop-floor TV displays.
-- Bound to /tv/* paths in middleware. Revocation is instant via
-- setting `revoked_at` (no blacklist cache needed).
CREATE TABLE IF NOT EXISTS device_tokens (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    token        TEXT UNIQUE NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   TEXT NOT NULL,
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS device_tokens_active_idx
    ON device_tokens (token) WHERE revoked_at IS NULL;
```

- [ ] **Step 2: Verify locally**

Run (only if you have `DATABASE_URL` set locally; otherwise skip — Railway will run the DDL on next boot):
```bash
& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -c "from zira_dashboard import db; db.init_pool(); db.bootstrap_schema(); print('ok')"
```
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/db.py
git commit -m "feat(auth): device_tokens table schema"
```

### Task 10: Device-token mint/verify/revoke (TDD)

**Files:**
- Create: `src/zira_dashboard/device_tokens.py`
- Create: `tests/test_device_tokens.py`

- [ ] **Step 1: Write the failing test**

`tests/test_device_tokens.py`:
```python
import pytest

from zira_dashboard import device_tokens


@pytest.fixture(autouse=True)
def fixed_secret(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-secret-32-bytes-of-random-data!!")


def test_signed_round_trip():
    raw = device_tokens._random_token()
    signed = device_tokens._sign(raw)
    assert device_tokens._verify_signature(signed) == raw


def test_signed_rejects_tampering():
    raw = device_tokens._random_token()
    signed = device_tokens._sign(raw)
    tampered = signed[:-1] + ("a" if signed[-1] != "a" else "b")
    assert device_tokens._verify_signature(tampered) is None


def test_signed_rejects_wrong_format():
    assert device_tokens._verify_signature("no-dot-here") is None
    assert device_tokens._verify_signature("") is None
    assert device_tokens._verify_signature(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_device_tokens.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/zira_dashboard/device_tokens.py`**

```python
"""Long-lived signed device tokens for unattended TV displays.

A token has two parts: `<random>.<hmac-of-random>`. The random half is
stored in Postgres (so it's individually revocable); the HMAC half is
re-derived at validation time using SESSION_SECRET. If the DB column
leaks, an attacker still can't construct a valid URL without the
secret. If the secret rotates, every token invalidates at once — useful
as a panic button.
"""
from __future__ import annotations

import hmac
import secrets
from datetime import datetime, timezone
from hashlib import sha256
from typing import Optional

from . import auth, db


def _random_token() -> str:
    """43-char urlsafe-base64 of 32 random bytes."""
    return secrets.token_urlsafe(32)


def _sign(raw: str) -> str:
    """Return `<raw>.<hex-hmac>` for embedding in a URL."""
    sig = hmac.new(auth._session_secret().encode(), raw.encode(), sha256).hexdigest()
    return f"{raw}.{sig}"


def _verify_signature(signed: str | None) -> str | None:
    """Return the raw token half if the HMAC checks out, else None.
    Constant-time compare to prevent timing attacks."""
    if not signed or "." not in signed:
        return None
    raw, _, sig = signed.rpartition(".")
    if not raw or not sig:
        return None
    try:
        expected = hmac.new(auth._session_secret().encode(), raw.encode(), sha256).hexdigest()
    except RuntimeError:
        return None
    if not hmac.compare_digest(expected, sig):
        return None
    return raw


# ---------- DB-backed mint / lookup / revoke ----------

def mint(name: str, created_by: str) -> tuple[int, str]:
    """Create a new device token. Returns (id, signed_token_for_url).

    Caller is responsible for surfacing the URL to the admin and never
    storing the signed form server-side beyond this call — the raw half
    in the DB plus SESSION_SECRET is enough to validate later."""
    raw = _random_token()
    signed = _sign(raw)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO device_tokens (name, token, created_by) "
            "VALUES (%s, %s, %s) RETURNING id",
            (name.strip(), raw, created_by),
        )
        row = cur.fetchone()
    return int(row["id"]), signed


def lookup_active(signed: str | None) -> Optional[dict]:
    """Validate signature, then look up an un-revoked DB row. Returns
    the row dict or None. Bumps `last_used_at` as a side effect when a
    valid match is found."""
    raw = _verify_signature(signed)
    if raw is None:
        return None
    rows = db.query(
        "SELECT id, name, token, created_at, last_used_at, revoked_at "
        "FROM device_tokens WHERE token = %s AND revoked_at IS NULL",
        (raw,),
    )
    if not rows:
        return None
    row = rows[0]
    # Bump last_used_at — best effort; ignore failures so a flaky DB
    # write doesn't break TV display rendering.
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE device_tokens SET last_used_at = now() WHERE id = %s",
                (row["id"],),
            )
    except Exception:
        pass
    return row


def list_all() -> list[dict]:
    """All tokens, newest first, for the admin UI."""
    return db.query(
        "SELECT id, name, created_at, created_by, last_used_at, revoked_at "
        "FROM device_tokens ORDER BY created_at DESC"
    )


def revoke(token_id: int) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE device_tokens SET revoked_at = now() "
            "WHERE id = %s AND revoked_at IS NULL",
            (token_id,),
        )
```

- [ ] **Step 4: Run tests**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_device_tokens.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/device_tokens.py tests/test_device_tokens.py
git commit -m "feat(auth): device-token HMAC sign/verify (signature half)"
```

### Task 11: Extend middleware to accept device tokens on /tv/*

**Files:**
- Modify: `src/zira_dashboard/auth.py`
- Modify: `tests/test_auth_middleware.py`

- [ ] **Step 1: Update `RequireAuthMiddleware.dispatch`**

Replace the body of `dispatch` in `auth.py`:
```python
    async def dispatch(self, request, call_next):
        if auth_disabled():
            return await call_next(request)

        path = request.url.path
        if _is_bypass_path(path):
            return await call_next(request)

        cookie = request.cookies.get(SESSION_COOKIE_NAME)
        payload = verify_session(cookie)
        if payload is not None:
            response = await call_next(request)
            if needs_refresh(payload):
                fresh = mint_session(sub=payload["sub"], upn=payload["upn"], name=payload["name"])
                response.set_cookie(
                    SESSION_COOKIE_NAME, fresh,
                    max_age=int(SESSION_TTL.total_seconds()),
                    httponly=True, secure=True, samesite="lax", path="/",
                )
            return response

        # No session cookie — try a device token, but ONLY on /tv/* paths.
        if path.startswith("/tv/"):
            from . import device_tokens as _dt
            signed = request.query_params.get("device")
            if signed and _dt.lookup_active(signed) is not None:
                return await call_next(request)

        # No valid auth — redirect to login.
        from urllib.parse import urlencode
        qs = urlencode({"next": path}) if path != "/" else ""
        target = "/auth/login" + (("?" + qs) if qs else "")
        return RedirectResponse(url=target, status_code=302)
```

- [ ] **Step 2: Add device-token tests**

`tests/test_auth_middleware.py` — append:
```python
def test_tv_path_with_valid_device_token_passes(mini_app, monkeypatch):
    # Stub device_tokens.lookup_active to return a fake row for our test value.
    from zira_dashboard import device_tokens as dt
    monkeypatch.setattr(dt, "lookup_active",
        lambda signed: {"id": 1, "name": "Bay 3"} if signed == "fake.signed" else None,
    )
    # Add a /tv/ route to the mini-app so the path matches.
    from starlette.responses import PlainTextResponse
    @mini_app.get("/tv/foo")
    def _tv(): return PlainTextResponse("tv-ok")

    c = TestClient(mini_app)
    r = c.get("/tv/foo?device=fake.signed")
    assert r.status_code == 200
    assert r.text == "tv-ok"


def test_tv_path_with_invalid_device_token_redirects(mini_app, monkeypatch):
    from zira_dashboard import device_tokens as dt
    monkeypatch.setattr(dt, "lookup_active", lambda signed: None)
    from starlette.responses import PlainTextResponse
    @mini_app.get("/tv/bar")
    def _tv(): return PlainTextResponse("tv-ok")

    c = TestClient(mini_app)
    r = c.get("/tv/bar?device=garbage", follow_redirects=False)
    assert r.status_code == 302
    assert "/auth/login" in r.headers["location"]


def test_non_tv_path_with_device_token_still_redirects(mini_app, monkeypatch):
    from zira_dashboard import device_tokens as dt
    monkeypatch.setattr(dt, "lookup_active",
        lambda signed: {"id": 1, "name": "Bay 3"} if signed == "fake.signed" else None,
    )
    c = TestClient(mini_app)
    # /recycling is NOT under /tv/, so the token must NOT work.
    r = c.get("/recycling?device=fake.signed", follow_redirects=False)
    assert r.status_code == 302
```

- [ ] **Step 3: Run tests**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_middleware.py -v`
Expected: all pass (including 3 new device-token tests).

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/auth.py tests/test_auth_middleware.py
git commit -m "feat(auth): middleware accepts device tokens on /tv/* paths only"
```

### Task 12: Admin UI for device tokens

**Files:**
- Modify: `src/zira_dashboard/routes/admin.py`
- Create: `src/zira_dashboard/templates/admin_devices.html`

- [ ] **Step 1: Create `src/zira_dashboard/templates/admin_devices.html`**

```html
{% extends "_staffing_base.html" %}
{% block title %}Devices — GPI Plant Manager{% endblock %}
{% block content %}
<main class="admin-devices">
  <h1>Device tokens for TV displays</h1>
  <p class="muted">
    Each TV display gets a unique signed token. The token-bearing URL works
    only on <code>/tv/*</code> paths. Revoke any token instantly below —
    revoked tokens stop working on the next refresh.
  </p>

  <form method="post" action="/admin/devices" class="new-device-form">
    <input type="text" name="name" placeholder="e.g. Bay 3 TV" required maxlength="120">
    <button type="submit">Create device token</button>
  </form>

  {% if just_minted %}
  <div class="just-minted">
    <h2>New token created for "{{ just_minted.name }}"</h2>
    <p>Copy this URL and open it once on the TV. <strong>This is the only time the URL is shown — it cannot be retrieved later.</strong></p>
    <input type="text" value="https://{{ host }}/tv/recycling?device={{ just_minted.signed }}" readonly onclick="this.select()" class="device-url">
    <p class="muted">For other dashboards, swap <code>/tv/recycling</code> for <code>/tv/wc/&lt;slug&gt;</code> or <code>/tv/new-vs</code>.</p>
  </div>
  {% endif %}

  <table class="devices-table">
    <thead><tr><th>Name</th><th>Created</th><th>By</th><th>Last used</th><th>Status</th><th></th></tr></thead>
    <tbody>
      {% for t in tokens %}
      <tr class="{% if t.revoked_at %}revoked{% endif %}">
        <td>{{ t.name }}</td>
        <td>{{ t.created_at.strftime('%Y-%m-%d %H:%M') }}</td>
        <td>{{ t.created_by }}</td>
        <td>{{ t.last_used_at.strftime('%Y-%m-%d %H:%M') if t.last_used_at else '—' }}</td>
        <td>{{ 'revoked' if t.revoked_at else 'active' }}</td>
        <td>
          {% if not t.revoked_at %}
          <form method="post" action="/admin/devices/{{ t.id }}/revoke" style="display:inline">
            <button type="submit" onclick="return confirm('Revoke {{ t.name }}? The TV will stop loading on next refresh.')">Revoke</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="6" class="muted">No tokens yet — create one above.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</main>
<style>
  .admin-devices { max-width: 60rem; margin: 2rem auto; padding: 0 1rem; }
  .new-device-form { display: flex; gap: .5rem; margin: 1.5rem 0; }
  .new-device-form input { flex: 1; padding: .5rem .75rem; border: 1px solid var(--border, #d8dee5); border-radius: 6px; }
  .new-device-form button { padding: .5rem 1rem; background: #0078d4; color: #fff; border: 0; border-radius: 6px; cursor: pointer; }
  .just-minted { background: #ecfdf5; border: 1px solid #10b981; border-radius: 8px; padding: 1rem; margin: 1.5rem 0; }
  .just-minted h2 { margin: 0 0 .5rem; font-size: 1.1rem; color: #047857; }
  .device-url { width: 100%; padding: .5rem; font-family: monospace; font-size: .85rem; }
  .devices-table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  .devices-table th, .devices-table td { padding: .5rem .75rem; border-bottom: 1px solid var(--border, #e3e8ee); text-align: left; }
  .devices-table tr.revoked { color: var(--muted, #94a3b8); text-decoration: line-through; }
  .muted { color: var(--muted, #64748b); font-size: .9rem; }
</style>
{% endblock %}
```

- [ ] **Step 2: Append routes to `src/zira_dashboard/routes/admin.py`**

At the bottom of `routes/admin.py`, add:
```python
from fastapi import Form
from fastapi.responses import RedirectResponse
from .. import device_tokens as _dt


@router.get("/admin/devices")
def admin_devices_list(request):
    from ..deps import templates
    return templates.TemplateResponse(
        request, "admin_devices.html",
        {"tokens": _dt.list_all(), "host": request.url.netloc, "just_minted": None},
    )


@router.post("/admin/devices")
def admin_devices_create(request, name: str = Form(...)):
    from ..deps import templates
    # Pull the authed user from the request state (set by RequireAuthMiddleware
    # in a future task — for now, fall back to "admin").
    created_by = getattr(request.state, "user_upn", "admin")
    new_id, signed = _dt.mint(name=name, created_by=created_by)
    minted = next((t for t in _dt.list_all() if t["id"] == new_id), None)
    return templates.TemplateResponse(
        request, "admin_devices.html",
        {
            "tokens": _dt.list_all(),
            "host": request.url.netloc,
            "just_minted": {"name": (minted or {}).get("name", name), "signed": signed},
        },
    )


@router.post("/admin/devices/{token_id}/revoke")
def admin_devices_revoke(token_id: int):
    _dt.revoke(token_id)
    return RedirectResponse(url="/admin/devices", status_code=303)
```

- [ ] **Step 3: Sanity check imports**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -c "from zira_dashboard.routes import admin; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Run full test suite**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/ -q`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/admin.py src/zira_dashboard/templates/admin_devices.html
git commit -m "feat(auth): /admin/devices UI to mint + list + revoke device tokens"
```

### Task 13: Stash authed user on request.state so admin UI can show who minted

**Files:**
- Modify: `src/zira_dashboard/auth.py`

- [ ] **Step 1: Update `RequireAuthMiddleware.dispatch` to set `request.state.user_upn`**

In the cookie-payload-valid branch, before `call_next(request)`:
```python
        if payload is not None:
            request.state.user_upn = payload.get("upn")
            request.state.user_name = payload.get("name")
            response = await call_next(request)
```

Also in the device-token branch:
```python
        if path.startswith("/tv/"):
            from . import device_tokens as _dt
            signed = request.query_params.get("device")
            row = _dt.lookup_active(signed) if signed else None
            if row is not None:
                request.state.user_upn = f"device:{row['name']}"
                request.state.user_name = row["name"]
                return await call_next(request)
```

- [ ] **Step 2: Update the test**

`tests/test_auth_middleware.py` — append:
```python
def test_session_sets_request_state(mini_app, fixed_secret):
    token = auth.mint_session(sub="x", upn="dale@gruberpallets.com", name="Dale")

    from starlette.responses import JSONResponse
    @mini_app.get("/whoami")
    def _w(request):
        return JSONResponse({
            "upn": getattr(request.state, "user_upn", None),
            "name": getattr(request.state, "user_name", None),
        })

    c = TestClient(mini_app, cookies={auth.SESSION_COOKIE_NAME: token})
    r = c.get("/whoami")
    assert r.json() == {"upn": "dale@gruberpallets.com", "name": "Dale"}
```

- [ ] **Step 3: Run tests**

Run: `& "C:\Users\dale.gruber\Projects\zira\.venv\Scripts\python.exe" -m pytest tests/test_auth_middleware.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/auth.py tests/test_auth_middleware.py
git commit -m "feat(auth): expose authed user as request.state.user_upn / user_name"
```

### Task 14: CHANGELOG entry + ship Sub-phase 2B

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Prepend a new entry under today's date**

```markdown
### {{CURRENT_TIME}}

- **Auth Sub-phase 2B: device tokens for shop-floor TVs** — new Postgres table `device_tokens`, new admin UI at `/admin/devices` (mint, list, revoke), middleware now accepts a `?device=<signed-token>` URL param ONLY on `/tv/*` paths. Tokens are random + HMAC-signed with `SESSION_SECRET`; the random half lives in Postgres for instant revocation; the signature is re-derived at validate time so a leaked DB column alone can't forge a working URL. Still gated by `AUTH_DISABLED=1` in Railway — nothing user-visible changes until Sub-phase 2C flips the env var off.
```

- [ ] **Step 2: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "chore: changelog entry for auth sub-phase 2B"
git push origin claude/hardcore-herschel-a3ee7b:main
```

- [ ] **Step 3: Verify on Railway**

After Railway redeploys:
1. Sign in via `/auth/login` (works because Sub-phase 2A is live).
2. Visit `https://gpiplantmanager.com/admin/devices`.
3. Create a token named "Test Bay".
4. Copy the URL shown.
5. Open the URL in a private window — should display `/tv/recycling` (or whatever path you tested with).
6. Click "Revoke" on the same token; refresh the private window — should redirect to login.

If all six steps work, Sub-phase 2B is shipped.

---

## Sub-phase 2C — The Cutover (manual Dale steps; no code)

This is the point of no return. **Plan to be at your desk with the shop-floor TVs in view.** If anything goes wrong, the immediate rollback is one Railway env var.

- [ ] **Step 1: Mint a device token per TV.** From `/admin/devices`, create one row per physical TV (e.g. "Bay 3 TV", "Bay 5 TV", etc.). Copy each URL.

- [ ] **Step 2: Walk every TV.** Open the browser on each TV, paste the matching URL, confirm the dashboard loads. **Do every TV before the next step.** If you flip `AUTH_DISABLED` off and a TV doesn't have a working token yet, it'll show the Microsoft sign-in page until you fix it.

- [ ] **Step 3: In Railway → your service → Variables → delete `AUTH_DISABLED`.** Trigger a redeploy.

- [ ] **Step 4: Verify.** From your laptop (not signed in), open an incognito window → visit `https://gpiplantmanager.com/recycling` → should redirect to Microsoft. Sign in with your @gruberpallets.com account → should land on `/recycling`. Verify the TVs are still showing live data. If yes, the cutover is complete.

- [ ] **Step 5: Tell Claude the cutover is done so I can write the final CHANGELOG entry.**

### Rollback plan (if the cutover goes wrong)

If a TV breaks or you can't sign in:
1. Add `AUTH_DISABLED=1` back to Railway → redeploy. Site is open again within ~60s.
2. Fix the underlying issue (typo'd device URL, expired Microsoft client secret, missed env var, etc.).
3. Remove `AUTH_DISABLED` and try the cutover again.

---

## Self-review notes

- **Spec coverage:** Every section of the spec is covered. Goals 1-6 → Tasks 1-14. Architecture → Tasks 2-7 + 9-12. Data flows → Tasks 5, 11, 12. Cookie/token formats → Tasks 2, 10. Middleware order → Task 7. Schema → Task 9. Env vars → Task 7, plus manual Railway setup already done. Testing → Tasks 2, 3, 6, 10, 11, 13. Phasing → Sub-phases 2A/2B/2C structure. Open questions remain open (deferred decisions, explicit "proposed" in spec).
- **Placeholders:** none. Every code block has full code. `{{CURRENT_TIME}}` markers in CHANGELOG steps are explicitly flagged for the executor to fill in.
- **Type consistency:** `mint_session(sub, upn, name)` signature is consistent across auth.py and test files. `verify_session(token) → dict | None` consistent. `_verify_signature`, `_sign`, `_random_token` consistent in device_tokens.py. Middleware sets `request.state.user_upn` consistently.
- **Naming:** `RequireAuthMiddleware`, `SESSION_COOKIE_NAME`, `SESSION_TTL`, `SESSION_REFRESH_AT`, `ALLOWED_DOMAIN`, `_BYPASS_PREFIXES`, `_BYPASS_EXACT` — all consistent throughout.
