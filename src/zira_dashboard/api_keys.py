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
    return token[:17]


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
            print(
                f"{row['id']}\t{state}\t{row['name']}\t"
                f"{row['key_prefix']}\t{','.join(row.get('scopes') or [])}"
            )
    elif args.cmd == "revoke":
        print("revoked" if revoke_key(args.id) else "not found")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
