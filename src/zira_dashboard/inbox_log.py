"""Append-only activity log for the Exception Inbox — the archive + audit trail.

One row per resolution across every inbox category. Denormalized on purpose
(snapshots person/category/outcome) so it stands alone after source rows are
deleted, mirroring time_off_audit.py. A NULL actor means the item resolved
itself (auto-resolved); a human action carries the manager's UPN + name.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db

_log = logging.getLogger(__name__)


def actor_from(request) -> tuple[str | None, str | None]:
    """(user_upn, user_name) for the current request; both None for system/auto.

    The auth middleware sets these on request.state for every authenticated
    request (see auth.py). Returns (None, None) when unset (e.g. AUTH_DISABLED).
    """
    return (
        getattr(request.state, "user_upn", None),
        getattr(request.state, "user_name", None),
    )


def record_event(
    *,
    item_kind: str,
    item_key: str,
    person_name: str | None,
    category_label: str | None,
    action: str,
    outcome: str | None = None,
    before_value: str | None = None,
    after_value: str | None = None,
    reason: str | None = None,
    actor_upn: str | None = None,
    actor_name: str | None = None,
    source: str | None = "inbox",
    reversible: bool = False,
    detail: Any | None = None,
) -> int:
    """Insert one event row and return its id (for later undo correlation)."""
    rows = db.query(
        "INSERT INTO inbox_events "
        "(item_kind, item_key, person_name, category_label, action, outcome, "
        " before_value, after_value, reason, actor_upn, actor_name, source, "
        " reversible, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
        "RETURNING id",
        (item_kind, item_key, person_name, category_label, action, outcome,
         before_value, after_value, reason, actor_upn, actor_name, source,
         reversible, json.dumps(detail) if detail is not None else None),
    )
    return int(rows[0]["id"])


def log_event_safe(**kwargs) -> int | None:
    """record_event, but never raises. Returns the new id, or None on failure.

    The underlying action (Odoo write, suppression-table row) is the source of
    truth; a failed audit write is logged and swallowed so it can't turn a
    completed action into a 500. Mirrors the best-effort posture of the
    time-off chatter post.
    """
    try:
        return record_event(**kwargs)
    except Exception as e:  # noqa: BLE001 -- audit is best-effort
        _log.warning("inbox_log.record_event failed (%s): %s",
                     kwargs.get("item_key"), e, exc_info=True)
        return None


def recent_events(days: int = 30) -> list[dict[str, Any]]:
    """Events in the last ``days`` days, newest first (archive/audit feed)."""
    return db.query(
        "SELECT id, item_kind, item_key, person_name, category_label, action, "
        "outcome, before_value, after_value, reason, actor_upn, actor_name, "
        "source, reversible, undone_at, resolved_at "
        "FROM inbox_events "
        "WHERE resolved_at >= now() - make_interval(days => %s) "
        "ORDER BY resolved_at DESC",
        (days,),
    )
