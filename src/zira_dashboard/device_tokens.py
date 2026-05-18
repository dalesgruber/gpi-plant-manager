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
        # SESSION_SECRET not set — no token can be valid.
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
