from __future__ import annotations

from collections import defaultdict
from datetime import date


def normalized_daily_scores(
    records: list[dict],
    *,
    wc_names: set[str],
    standard_full_day_hours: float,
    min_hours: float = 4.0,
) -> list[dict]:
    """One normalized score per (person, day) inside a WC scope.

    Records are summed by person/day before the cutoff so split time across
    multiple WCs in the same scope counts fairly.
    """
    if standard_full_day_hours <= 0:
        return []
    scoped = [r for r in records if r.get("wc") in wc_names]
    by_person_day: dict[tuple[str, date], dict] = defaultdict(
        lambda: {"units": 0.0, "hours": 0.0}
    )
    for r in scoped:
        person = str(r["person"])
        day = r["day"]
        bucket = by_person_day[(person, day)]
        bucket["units"] += float(r.get("units") or 0.0)
        bucket["hours"] += float(r.get("hours") or 0.0)

    out: list[dict] = []
    for (person, day), totals in by_person_day.items():
        hours = totals["hours"]
        if hours < min_hours or hours <= 0:
            continue
        normalized = totals["units"] / hours * standard_full_day_hours
        out.append(
            {
                "name": person,
                "day": day,
                "units": totals["units"],
                "hours": hours,
                "normalized_units": normalized,
            }
        )
    out.sort(key=lambda r: (r["day"], r["name"].lower()))
    return out


def normalized_average_by_person(
    records: list[dict],
    *,
    wc_names: set[str],
    standard_full_day_hours: float,
    min_hours: float = 4.0,
) -> list[dict]:
    """Average normalized pallets/day by person for one WC/group/role scope."""
    scores = normalized_daily_scores(
        records,
        wc_names=wc_names,
        standard_full_day_hours=standard_full_day_hours,
        min_hours=min_hours,
    )
    by_person: dict[str, dict] = defaultdict(
        lambda: {
            "total_normalized_units": 0.0,
            "total_units": 0.0,
            "total_hours": 0.0,
            "days": 0,
        }
    )
    for s in scores:
        bucket = by_person[s["name"]]
        bucket["total_normalized_units"] += s["normalized_units"]
        bucket["total_units"] += s["units"]
        bucket["total_hours"] += s["hours"]
        bucket["days"] += 1

    out: list[dict] = []
    for name, totals in by_person.items():
        days = totals["days"]
        if days <= 0:
            continue
        out.append(
            {
                "name": name,
                "avg_units": totals["total_normalized_units"] / days,
                "days": days,
                "total_units": totals["total_units"],
                "total_hours": totals["total_hours"],
            }
        )
    out.sort(key=lambda r: (-r["avg_units"], -r["days"], r["name"].lower()))
    return out
