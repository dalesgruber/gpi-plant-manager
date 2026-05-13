"""Persistence layer for custom dashboards + their widget placements.

`custom_dashboards` holds the dashboard meta (name, slug, scope, theme).
`dashboard_widgets` holds placements (which widget def is on which
dashboard, where, with what data overrides).

Slug derivation reuses `wc_dashboard_data.slug_for_wc`. Collision
suffix follows the same pattern as `tv_displays_store`.
"""
from __future__ import annotations

import json
from typing import Optional, Union

from .wc_dashboard_data import slug_for_wc


def _unique_slug(base: str, *, exclude_id: Optional[int] = None) -> str:
    from . import db
    candidate = base
    n = 2
    while True:
        rows = db.query(
            "SELECT id FROM custom_dashboards WHERE slug = %s",
            (candidate,),
        )
        if not rows or (exclude_id is not None and all(r["id"] == exclude_id for r in rows)):
            return candidate
        candidate = f"{base}-{n}"
        n += 1


def save_dashboard(
    *,
    name: str,
    scope_kind: str,
    scope_value: str,
    theme: str,
    id: Optional[int] = None,
) -> dict:
    from . import db
    if not isinstance(name, str) or not name.strip():
        raise ValueError("name required")
    if scope_kind not in ("wc", "group"):
        raise ValueError(f"invalid scope_kind: {scope_kind}")
    if not isinstance(scope_value, str) or not scope_value.strip():
        raise ValueError("scope_value required")
    if theme not in ("light", "dark"):
        theme = "dark"
    slug_base = slug_for_wc(name)
    if not slug_base:
        raise ValueError("name must produce a non-empty slug")
    slug = _unique_slug(slug_base, exclude_id=id)
    if id is None:
        rows = db.query(
            "INSERT INTO custom_dashboards "
            "  (name, slug, scope_kind, scope_value, theme) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, name, slug, scope_kind, scope_value, theme, sort_order",
            (name.strip(), slug, scope_kind, scope_value.strip(), theme),
        )
    else:
        rows = db.query(
            "UPDATE custom_dashboards SET "
            "  name = %s, slug = %s, scope_kind = %s, scope_value = %s, "
            "  theme = %s, updated_at = now() "
            "WHERE id = %s "
            "RETURNING id, name, slug, scope_kind, scope_value, theme, sort_order",
            (name.strip(), slug, scope_kind, scope_value.strip(), theme, id),
        )
    if not rows:
        raise LookupError(f"no custom_dashboards row with id={id}")
    return _hydrate_dashboard(rows[0])


def get_dashboard(id_or_slug: Union[int, str]) -> Optional[dict]:
    from . import db
    if isinstance(id_or_slug, int):
        rows = db.query(
            "SELECT id, name, slug, scope_kind, scope_value, theme, sort_order "
            "FROM custom_dashboards WHERE id = %s",
            (id_or_slug,),
        )
    else:
        rows = db.query(
            "SELECT id, name, slug, scope_kind, scope_value, theme, sort_order "
            "FROM custom_dashboards WHERE slug = %s",
            (id_or_slug,),
        )
    return _hydrate_dashboard(rows[0]) if rows else None


def list_dashboards() -> list[dict]:
    """All dashboards with `widget_count` precomputed via subquery."""
    from . import db
    rows = db.query(
        "SELECT d.id, d.name, d.slug, d.scope_kind, d.scope_value, d.theme, d.sort_order, "
        "  COALESCE(c.n, 0) AS widget_count "
        "FROM custom_dashboards d "
        "LEFT JOIN ("
        "  SELECT dashboard_id, COUNT(*) AS n "
        "  FROM dashboard_widgets GROUP BY dashboard_id"
        ") c ON c.dashboard_id = d.id "
        "ORDER BY d.sort_order, lower(d.name)"
    )
    out = []
    for r in rows:
        d = _hydrate_dashboard(r)
        d["widget_count"] = int(r["widget_count"])
        out.append(d)
    return out


def delete_dashboard(id: int) -> None:
    from . import db
    db.execute("DELETE FROM custom_dashboards WHERE id = %s", (id,))


def add_placement(
    *,
    dashboard_id: int,
    widget_def_id: int,
    x: int, y: int, w: int, h: int,
    data_overrides: dict,
) -> dict:
    from . import db
    data_overrides = data_overrides or {}
    rows = db.query(
        "INSERT INTO dashboard_widgets "
        "  (dashboard_id, widget_def_id, x, y, w, h, data_overrides_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb) "
        "RETURNING id, dashboard_id, widget_def_id, x, y, w, h, data_overrides_json",
        (dashboard_id, widget_def_id, x, y, w, h, json.dumps(data_overrides)),
    )
    return _hydrate_placement(rows[0])


def update_placement(
    id: int,
    *,
    x: Optional[int] = None,
    y: Optional[int] = None,
    w: Optional[int] = None,
    h: Optional[int] = None,
    data_overrides: Optional[dict] = None,
) -> None:
    """Update a placement. Only the fields you pass are touched."""
    from . import db
    sets: list[str] = []
    params: list = []
    if x is not None: sets.append("x = %s"); params.append(x)
    if y is not None: sets.append("y = %s"); params.append(y)
    if w is not None: sets.append("w = %s"); params.append(w)
    if h is not None: sets.append("h = %s"); params.append(h)
    if data_overrides is not None:
        sets.append("data_overrides_json = %s::jsonb")
        params.append(json.dumps(data_overrides))
    if not sets:
        return
    params.append(id)
    db.execute(
        f"UPDATE dashboard_widgets SET {', '.join(sets)} WHERE id = %s",
        tuple(params),
    )


def delete_placement(id: int) -> None:
    from . import db
    db.execute("DELETE FROM dashboard_widgets WHERE id = %s", (id,))


def list_placements(dashboard_id: int) -> list[dict]:
    """Placements for one dashboard, joined with their widget definition.

    Each row carries the placement (x/y/w/h, data_overrides, id) AND the
    definition (name, type, visual, default_data) so the template can
    render without a second query per widget.
    """
    from . import db
    rows = db.query(
        "SELECT dw.id, dw.dashboard_id, dw.widget_def_id, "
        "  dw.x, dw.y, dw.w, dw.h, dw.data_overrides_json, "
        "  wd.name, wd.type, wd.visual_json, wd.default_data_json "
        "FROM dashboard_widgets dw "
        "JOIN widget_definitions wd ON wd.id = dw.widget_def_id "
        "WHERE dw.dashboard_id = %s "
        "ORDER BY dw.id",
        (dashboard_id,),
    )
    out = []
    for r in rows:
        p = _hydrate_placement(r)
        p["name"] = r["name"]
        p["type"] = r["type"]
        p["visual"] = _decode(r["visual_json"])
        p["default_data"] = _decode(r["default_data_json"])
        merged = dict(p["default_data"])
        merged.update(p["data_overrides"])
        p["effective_data"] = merged
        out.append(p)
    return out


def _hydrate_dashboard(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "scope_kind": row["scope_kind"],
        "scope_value": row["scope_value"],
        "theme": row["theme"],
        "sort_order": int(row["sort_order"]),
    }


def _hydrate_placement(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "dashboard_id": int(row["dashboard_id"]),
        "widget_def_id": int(row["widget_def_id"]),
        "x": int(row["x"]),
        "y": int(row["y"]),
        "w": int(row["w"]),
        "h": int(row["h"]),
        "data_overrides": _decode(row["data_overrides_json"]),
    }


def _decode(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}
