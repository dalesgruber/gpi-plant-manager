"""Server-persisted hidden-column list for the People Matrix.

Stored in app_settings under key 'skill_filter' as {"hidden": [...]}.
"""

from __future__ import annotations

import json


def load_hidden() -> list[str]:
    from . import db
    rows = db.query("SELECT value FROM app_settings WHERE key = 'skill_filter'")
    if not rows:
        return []
    raw = rows[0]["value"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if isinstance(raw, dict) and isinstance(raw.get("hidden"), list):
        return [str(x) for x in raw["hidden"] if isinstance(x, str)]
    return []


def save_hidden(hidden: list[str]) -> None:
    from . import db
    payload = {"hidden": sorted(set(s.strip() for s in hidden if s and s.strip()))}
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) "
        "VALUES ('skill_filter', %s::jsonb, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        (json.dumps(payload),),
    )
