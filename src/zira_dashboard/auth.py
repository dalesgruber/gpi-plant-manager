"""Authentication helpers: session JWT mint/verify + config + domain check.

Import-safe even when Microsoft env vars are missing — the OIDC client
is constructed lazily inside `oauth_client()` (added in Task 4). Tests
that only exercise JWT helpers don't need any Microsoft config.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta
from typing import Any

from authlib.jose import jwt
from authlib.jose.errors import JoseError

SESSION_COOKIE_NAME = "gpi_session"
SESSION_TTL = timedelta(days=7)
SESSION_REFRESH_AT = timedelta(days=6)
_JWT_ALG = "HS256"

ALLOWED_DOMAIN = "gruberpallets.com"


def _session_secret() -> str:
    """Read SESSION_SECRET from env. Raises at use time, not import time."""
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        raise RuntimeError(
            "SESSION_SECRET env var is not set. Generate one via "
            "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"` "
            "and add it to your environment."
        )
    return secret


def auth_disabled() -> bool:
    """True when AUTH_DISABLED=1/true/yes (local dev or staged rollout)."""
    return os.environ.get("AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")


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
    token = jwt.encode({"alg": _JWT_ALG}, payload, _session_secret())
    # authlib returns bytes; cookies want str
    return token.decode("ascii") if isinstance(token, bytes) else token


def verify_session(token: str | None) -> dict[str, Any] | None:
    """Decode + verify a session JWT. Returns the payload or None for
    any failure (missing/malformed/bad-signature/expired/secret-unset).
    Never raises."""
    if not token:
        return None
    try:
        secret = _session_secret()
    except RuntimeError:
        return None
    try:
        claims = jwt.decode(token, secret)
        claims.validate()  # checks exp/nbf if present
        return dict(claims)
    except JoseError:
        return None
    except (ValueError, TypeError):
        # authlib raises ValueError on malformed JWTs and TypeError on
        # non-str inputs that slip past the early None/empty check.
        return None


def needs_refresh(payload: dict[str, Any] | None) -> bool:
    """True when remaining lifetime is below SESSION_REFRESH_AT."""
    if not payload or "exp" not in payload:
        return False
    remaining = int(payload["exp"]) - int(time.time())
    return remaining < int(SESSION_REFRESH_AT.total_seconds())


def domain_ok(upn_or_email: str | None) -> bool:
    """Allow only single-@ identities whose domain part exactly matches
    ALLOWED_DOMAIN. Rejects multi-@ inputs that would slip past a naive
    .endswith() check."""
    if not upn_or_email or upn_or_email.count("@") != 1:
        return False
    domain = upn_or_email.split("@", 1)[1].lower()
    return domain == ALLOWED_DOMAIN.lower()
