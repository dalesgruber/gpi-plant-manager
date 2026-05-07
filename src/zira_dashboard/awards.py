"""Trophy system computation engine.

Pure functions over `production_history.daily_records` plus a
`work_centers_store` lookup. No caching beyond what daily_records
already does (postgres-backed). Override layer is in the same module
(see Task 5).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from calendar import monthrange


def _all_time_range() -> tuple[date, date]:
    """Earliest day in zira_daily_cache (or today if empty) → today."""
    from datetime import datetime, timezone
    from . import db
    today = datetime.now(timezone.utc).date()
    rows = db.query("SELECT MIN(day) AS d FROM zira_daily_cache")
    earliest = rows[0]["d"] if rows and rows[0].get("d") else today
    return (earliest, today)


def _wc_names_for_group(group_name: str) -> set[str]:
    from . import work_centers_store
    return {loc.name for loc in work_centers_store.members("group", group_name)}


def person_days_in_group(group_name: str, start: date, end: date) -> list[dict]:
    """Returns one row per (person, day) summing units/hours across the
    group's WCs. Filters days where total units == 0.

    Each row: {"name": str, "day": date, "units": float, "hours": float}.
    """
    from . import production_history
    wc_names = _wc_names_for_group(group_name)
    if not wc_names:
        return []
    raw = production_history.daily_records(start, end, None)
    agg: dict[tuple[str, date], dict] = defaultdict(lambda: {"units": 0.0, "hours": 0.0})
    for r in raw:
        if r["wc"] not in wc_names:
            continue
        key = (r["person"], r["day"])
        agg[key]["units"] += r["units"]
        agg[key]["hours"] += r["hours"]
    return [
        {"name": person, "day": day, "units": v["units"], "hours": v["hours"]}
        for (person, day), v in agg.items()
        if v["units"] > 0
    ]


def person_days_in_wc(wc_name: str, start: date, end: date) -> list[dict]:
    """Same shape as person_days_in_group but for a single WC."""
    from . import production_history
    raw = production_history.daily_records(start, end, None)
    return [
        {"name": r["person"], "day": r["day"], "units": r["units"], "hours": r["hours"]}
        for r in raw
        if r["wc"] == wc_name and r["units"] > 0
    ]


def _rank_single_day(rows: list[dict], top_n: int) -> list[dict]:
    """Order rows by units desc, then pph desc, then name asc.
    Return top N with positions 1..N attached."""
    def _key(r):
        pph = (r["units"] / r["hours"]) if r["hours"] > 0 else 0.0
        return (-r["units"], -pph, r["name"])
    ranked = sorted(rows, key=_key)[:top_n]
    out = []
    for i, r in enumerate(ranked, start=1):
        pph = round(r["units"] / r["hours"], 1) if r["hours"] > 0 else 0.0
        out.append({
            "position": i,
            "name": r["name"],
            "day": r["day"],
            "units": r["units"],
            "pph": pph,
        })
    return out


def _month_range(year: int, month: int) -> tuple[date, date]:
    last_day = monthrange(year, month)[1]
    return (date(year, month, 1), date(year, month, last_day))


def _year_range(year: int) -> tuple[date, date]:
    return (date(year, 1, 1), date(year, 12, 31))


def monthly_badges(group_name: str, year: int, month: int) -> list[dict]:
    """Top-3 person-days in the group during [year, month]."""
    start, end = _month_range(year, month)
    rows = person_days_in_group(group_name, start, end)
    return _rank_single_day(rows, top_n=3)


def annual_top_days(group_name: str, year: int) -> list[dict]:
    """Top-3 person-days in the group during [year]."""
    start, end = _year_range(year)
    rows = person_days_in_group(group_name, start, end)
    return _rank_single_day(rows, top_n=3)


def goat(group_name: str) -> dict | None:
    """All-time best person-day in the group. Earliest day wins on tie.
    Returns {name, day, units, pph} or None when no data.
    """
    start, end = _all_time_range()
    rows = person_days_in_group(group_name, start, end)
    if not rows:
        return None
    rows_sorted = sorted(rows, key=lambda r: (-r["units"], r["day"], r["name"]))
    top = rows_sorted[0]
    pph = round(top["units"] / top["hours"], 1) if top["hours"] > 0 else 0.0
    return {"name": top["name"], "day": top["day"], "units": top["units"], "pph": pph}
