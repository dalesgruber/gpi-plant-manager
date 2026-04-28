"""Per-page widget layouts (Gridstack positions). Backed by `widget_layouts`
in Postgres — one row per page, layout stored as JSONB."""

from __future__ import annotations

import json


def _normalize(item: dict) -> dict:
    return {
        "id": str(item.get("id")),
        "x": int(item.get("x", 0) or 0),
        "y": int(item.get("y", 0) or 0),
        "w": int(item.get("w", 1) or 1),
        "h": int(item.get("h", 1) or 1),
    }


def load(page: str) -> list[dict]:
    from . import db
    rows = db.query("SELECT layout FROM widget_layouts WHERE page = %s", (page,))
    if not rows:
        return []
    raw = rows[0]["layout"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    return [_normalize(i) for i in raw if isinstance(i, dict) and i.get("id")]


def save(page: str, layout: list[dict]) -> None:
    from . import db
    items = [_normalize(i) for i in (layout or []) if isinstance(i, dict) and i.get("id")]
    db.execute(
        "INSERT INTO widget_layouts (page, layout, updated_at) "
        "VALUES (%s, %s::jsonb, now()) "
        "ON CONFLICT (page) DO UPDATE SET layout = EXCLUDED.layout, updated_at = now()",
        (page, json.dumps(items)),
    )


def layout_map(page: str) -> dict[str, dict]:
    return {item["id"]: item for item in load(page)}
