"""Persistence layer for widget definitions (workshop presets).

Each definition has a type (one of the registry slugs), a visual config
JSON, and a default data scope JSON. Deletion is blocked while any
`dashboard_widgets` row references the row — caller should check
`usage_count` first and ask the user to remove placements.
"""
from __future__ import annotations

import json
from typing import Optional


def save(
    *,
    name: str,
    type: str,
    visual: dict,
    default_data: dict,
    id: Optional[int] = None,
) -> dict:
    """Insert or update a definition. Returns the saved row as a dict."""
    from . import db
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name required")
    if not isinstance(type, str) or not type.strip():
        raise ValueError("type required")
    visual = visual or {}
    default_data = default_data or {}
    if id is None:
        rows = db.query(
            "INSERT INTO widget_definitions (name, type, visual_json, default_data_json) "
            "VALUES (%s, %s, %s::jsonb, %s::jsonb) "
            "RETURNING id, name, type, visual_json, default_data_json",
            (name.strip(), type.strip(), json.dumps(visual), json.dumps(default_data)),
        )
    else:
        rows = db.query(
            "UPDATE widget_definitions SET "
            "  name = %s, type = %s, visual_json = %s::jsonb, "
            "  default_data_json = %s::jsonb, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, name, type, visual_json, default_data_json",
            (name.strip(), type.strip(), json.dumps(visual), json.dumps(default_data), id),
        )
    if not rows:
        raise LookupError(f"no widget_definitions row with id={id}")
    return _hydrate(rows[0])


def get(id: int) -> Optional[dict]:
    from . import db
    rows = db.query(
        "SELECT id, name, type, visual_json, default_data_json "
        "FROM widget_definitions WHERE id = %s",
        (id,),
    )
    return _hydrate(rows[0]) if rows else None


def list_definitions() -> list[dict]:
    """All definitions with `usage_count` precomputed via subquery."""
    from . import db
    rows = db.query(
        "SELECT wd.id, wd.name, wd.type, wd.visual_json, wd.default_data_json, "
        "  COALESCE(c.n, 0) AS usage_count "
        "FROM widget_definitions wd "
        "LEFT JOIN ("
        "  SELECT widget_def_id, COUNT(*) AS n "
        "  FROM dashboard_widgets GROUP BY widget_def_id"
        ") c ON c.widget_def_id = wd.id "
        "ORDER BY wd.type, lower(wd.name)"
    )
    out = []
    for r in rows:
        d = _hydrate(r)
        d["usage_count"] = int(r["usage_count"])
        out.append(d)
    return out


def delete(id: int) -> None:
    """Hard-delete a definition. Postgres FK ON DELETE RESTRICT raises if
    any dashboard_widgets row references it — caller is expected to have
    called `usage_count` first."""
    from . import db
    db.execute("DELETE FROM widget_definitions WHERE id = %s", (id,))


def usage_count(id: int) -> int:
    from . import db
    rows = db.query(
        "SELECT COUNT(*) AS n FROM dashboard_widgets WHERE widget_def_id = %s",
        (id,),
    )
    return int(rows[0]["n"]) if rows else 0


def _hydrate(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "type": row["type"],
        "visual": _decode(row["visual_json"]),
        "default_data": _decode(row["default_data_json"]),
    }


def _decode(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}
