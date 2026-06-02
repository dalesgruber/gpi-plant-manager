"""Auto-lunch settings: master toggle, observe-only mode, and the global flex
rule. Singleton row (id=1), cached in process and invalidated on save() —
same pattern as schedule_store.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    observe_only: bool = True
    flex_after_hours: float = 5.0
    flex_minutes: int = 30


DEFAULT = Settings()

_lock = RLock()
_cache: Settings | None = None


def _row_to_settings(row: dict) -> Settings:
    return Settings(
        enabled=bool(row.get("enabled", False)),
        observe_only=bool(row.get("observe_only", True)),
        flex_after_hours=float(row.get("flex_after_hours") or 5.0),
        flex_minutes=int(row.get("flex_minutes") or 30),
    )


def _load_from_db() -> Settings:
    from . import db
    rows = db.query(
        "SELECT enabled, observe_only, flex_after_hours, flex_minutes "
        "FROM auto_lunch_settings WHERE id = 1"
    )
    return _row_to_settings(rows[0]) if rows else DEFAULT


def current() -> Settings:
    global _cache
    with _lock:
        if _cache is None:
            _cache = _load_from_db()
        return _cache


def save(s: Settings) -> None:
    global _cache
    from . import db
    db.execute(
        "INSERT INTO auto_lunch_settings "
        "(id, enabled, observe_only, flex_after_hours, flex_minutes) "
        "VALUES (1, %s, %s, %s, %s) "
        "ON CONFLICT (id) DO UPDATE SET enabled = EXCLUDED.enabled, "
        "observe_only = EXCLUDED.observe_only, "
        "flex_after_hours = EXCLUDED.flex_after_hours, "
        "flex_minutes = EXCLUDED.flex_minutes",
        (s.enabled, s.observe_only, s.flex_after_hours, s.flex_minutes),
    )
    with _lock:
        _cache = s


def reload() -> Settings:
    global _cache
    with _lock:
        _cache = _load_from_db()
        return _cache
