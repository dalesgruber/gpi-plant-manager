"""Per-widget visual customization (title, color, etc.) backed by Postgres
`widget_customizations` (page, widget_id) -> JSONB.

`load_all` is hot — runs on every dashboard render. Cached in-process
for 30 seconds. `save_one` / `reset_one` invalidate the cached page so
edits show up immediately."""

from __future__ import annotations

import json
import re

from ._cache import TTLCache

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

ORIENTATIONS = {"horizontal", "vertical"}
NUMBER_POSITIONS = {"widget", "bar", "inside", "hidden"}
SORTS = {"preset", "desc", "asc", "alpha"}
ALIGNS = {"left", "center", "right"}

_CACHE = TTLCache(ttl_seconds=30.0, max_entries=16)


def _decode(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def load_all(page: str) -> dict[str, dict]:
    return _CACHE.get_or_compute(page, lambda: _load_all_uncached(page))


def _load_all_uncached(page: str) -> dict[str, dict]:
    from . import db
    rows = db.query(
        "SELECT widget_id, customizations FROM widget_customizations WHERE page = %s",
        (page,),
    )
    return {r["widget_id"]: _decode(r["customizations"]) for r in rows}


def load_one(page: str, widget_id: str) -> dict:
    from . import db
    rows = db.query(
        "SELECT customizations FROM widget_customizations WHERE page = %s AND widget_id = %s",
        (page, widget_id),
    )
    return _decode(rows[0]["customizations"]) if rows else {}


def save_one(page: str, widget_id: str, config: dict) -> dict:
    from . import db
    clean: dict = {}
    title = config.get("title")
    if isinstance(title, str) and title.strip():
        clean["title"] = title.strip()[:120]
    color = config.get("color")
    if isinstance(color, str) and _HEX_RE.match(color.strip()):
        clean["color"] = color.strip().lower()
    for key, allowed in (
        ("orientation", ORIENTATIONS),
        ("number_position", NUMBER_POSITIONS),
        ("sort", SORTS),
        ("align", ALIGNS),
    ):
        v = config.get(key)
        if isinstance(v, str) and v in allowed:
            clean[key] = v
    for key in ("show_target", "show_legend"):
        if key in config:
            clean[key] = bool(config[key])

    if clean:
        db.execute(
            "INSERT INTO widget_customizations (page, widget_id, customizations) "
            "VALUES (%s, %s, %s::jsonb) "
            "ON CONFLICT (page, widget_id) DO UPDATE SET customizations = EXCLUDED.customizations",
            (page, widget_id, json.dumps(clean)),
        )
    else:
        db.execute(
            "DELETE FROM widget_customizations WHERE page = %s AND widget_id = %s",
            (page, widget_id),
        )
    _CACHE.invalidate(page)
    return clean


def reset_one(page: str, widget_id: str) -> None:
    from . import db
    db.execute(
        "DELETE FROM widget_customizations WHERE page = %s AND widget_id = %s",
        (page, widget_id),
    )
    _CACHE.invalidate(page)
