"""Per-WC layout settings for the leaderboards page (sort order + manual
inactive flag). Server-side, shared across users/devices.
"""

from __future__ import annotations


def snapshot() -> dict[str, dict]:
    from . import db
    rows = db.query(
        "SELECT wc_name, sort_order, is_inactive FROM leaderboard_wc_settings"
    )
    return {r["wc_name"]: {"sort_order": r["sort_order"], "is_inactive": r["is_inactive"]} for r in rows}


def set_order(wc_names: list[str]) -> None:
    """Upsert sort_order for each name in the list, indexed left-to-right.
    WCs not in the list are untouched (their existing order survives).
    Preserves the existing is_inactive flag — only writes sort_order."""
    from . import db
    with db.cursor() as cur:
        for i, name in enumerate(wc_names):
            if not isinstance(name, str) or not name.strip():
                continue
            cur.execute(
                "INSERT INTO leaderboard_wc_settings (wc_name, sort_order) "
                "VALUES (%s, %s) "
                "ON CONFLICT (wc_name) DO UPDATE SET "
                "sort_order = EXCLUDED.sort_order, updated_at = now()",
                (name.strip(), i),
            )


def set_inactive(wc_name: str, value: bool) -> None:
    from . import db
    db.execute(
        "INSERT INTO leaderboard_wc_settings (wc_name, is_inactive) "
        "VALUES (%s, %s) "
        "ON CONFLICT (wc_name) DO UPDATE SET "
        "is_inactive = EXCLUDED.is_inactive, updated_at = now()",
        (wc_name.strip(), bool(value)),
    )
