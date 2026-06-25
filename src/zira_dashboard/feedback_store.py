"""Persistence for user-submitted feedback (index linking submitter → Odoo task)."""

from __future__ import annotations

from . import db


def _clamp_limit(limit, default: int = 100) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 500))


def insert(
    message: str,
    submitter: str | None = None,
    page_url: str | None = None,
    task_type: str | None = None,
    odoo_task_id: int | None = None,
) -> int:
    """Insert one feedback row; return its new id."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO feedback (submitter, page_url, task_type, odoo_task_id, message) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (submitter, page_url, task_type, odoo_task_id, message),
        )
        return cur.fetchone()["id"]


def for_submitter(submitter: str | None, limit: int = 100) -> list[dict]:
    """Return one submitter's feedback rows, newest first."""
    return db.query(
        "SELECT id, created_at, submitter, page_url, task_type, odoo_task_id, message "
        "FROM feedback WHERE submitter = %s ORDER BY id DESC LIMIT %s",
        (submitter, _clamp_limit(limit)),
    )
