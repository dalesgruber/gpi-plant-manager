"""Per-WC and per-Group display settings for the leaderboards page
(sort order + manual inactive flag). Server-side, shared across users.
"""

from __future__ import annotations


def snapshot() -> dict[str, dict[str, dict]]:
    """Return {kind: {name: {sort_order, is_inactive}}}.

    Top-level keys are 'wc' and 'group' (always present, possibly empty).
    """
    from . import db
    rows = db.query(
        "SELECT kind, wc_name, sort_order, is_inactive "
        "FROM leaderboard_wc_settings"
    )
    out: dict[str, dict[str, dict]] = {"wc": {}, "group": {}}
    for r in rows:
        k = r["kind"] or "wc"
        out.setdefault(k, {})[r["wc_name"]] = {
            "sort_order": r["sort_order"],
            "is_inactive": r["is_inactive"],
        }
    return out


def set_order(kind: str, names: list[str]) -> None:
    """Upsert sort_order for each name within the given kind."""
    from . import db
    if kind not in ("wc", "group"):
        return
    with db.cursor() as cur:
        for i, name in enumerate(names):
            if not isinstance(name, str) or not name.strip():
                continue
            cur.execute(
                "INSERT INTO leaderboard_wc_settings (kind, wc_name, sort_order) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (kind, wc_name) DO UPDATE SET "
                "sort_order = EXCLUDED.sort_order, updated_at = now()",
                (kind, name.strip(), i),
            )


def set_inactive(kind: str, name: str, value: bool) -> None:
    from . import db
    if kind not in ("wc", "group"):
        return
    db.execute(
        "INSERT INTO leaderboard_wc_settings (kind, wc_name, is_inactive) "
        "VALUES (%s, %s, %s) "
        "ON CONFLICT (kind, wc_name) DO UPDATE SET "
        "is_inactive = EXCLUDED.is_inactive, updated_at = now()",
        (kind, name.strip(), bool(value)),
    )
