"""Per-day, per-person production attribution.

Joins published schedules (who worked where) with Zira leaderboard output
(what each WC produced) into a {person → {wc → totals}} structure used by
the VS dashboard, Player Cards, and Leaderboards features. Units and
downtime at multi-person WCs are split equally across all assigned
operators.

The pure core (`attribute_for_day`, `attribute_for_range`) takes pre-fetched
data and is fully testable. The wrappers (`attribution_for`,
`attribution_range`) call Zira and load schedules.
"""

from __future__ import annotations

from datetime import date


def attribute_for_day(
    assignments: dict[str, list[str]],
    wc_totals: dict[str, tuple[int, int]],
    elapsed_minutes: int,
) -> dict[str, dict[str, dict[str, float]]]:
    """Attribute one day's WC output to the operators on each WC.

    Args:
        assignments: {wc_name: [person_name, ...]} — from the schedule's
            assignments dict, with the time-off pseudo-key already stripped.
        wc_totals: {wc_name: (units, downtime_minutes)} — from a Zira
            leaderboard call. Missing entries (WC with no meter) are
            treated as zero output.
        elapsed_minutes: shift minutes available that day; same for everyone.

    Returns:
        {person: {wc_name: {"units": float, "downtime": float, "hours": float,
                            "days_worked": int}}}
    """
    from .staffing import TIME_OFF_KEY  # local import avoids circular at module load

    out: dict[str, dict[str, dict[str, float]]] = {}
    hours = elapsed_minutes / 60.0
    for wc_name, operators in assignments.items():
        if wc_name == TIME_OFF_KEY:
            continue
        if not operators:
            continue
        units, downtime = wc_totals.get(wc_name, (0, 0))
        n = len(operators)
        per_units = units / n
        per_downtime = downtime / n
        for person in operators:
            wc_map = out.setdefault(person, {})
            wc_map[wc_name] = {
                "units": per_units,
                "downtime": per_downtime,
                "hours": hours,
                "days_worked": 1,
            }
    return out


def attribute_for_range(
    daily_attributions: list[dict[str, dict[str, dict[str, float]]]],
) -> dict[str, dict[str, dict[str, float]]]:
    """Sum a list of per-day attribution dicts (output of attribute_for_day).

    Adds the four numeric fields per (person, wc); days_worked counts the
    number of input days that contained that (person, wc) pair.
    """
    out: dict[str, dict[str, dict[str, float]]] = {}
    for daily in daily_attributions:
        for person, wc_map in daily.items():
            person_out = out.setdefault(person, {})
            for wc_name, totals in wc_map.items():
                acc = person_out.setdefault(
                    wc_name,
                    {"units": 0.0, "downtime": 0.0, "hours": 0.0, "days_worked": 0},
                )
                acc["units"] += totals["units"]
                acc["downtime"] += totals["downtime"]
                acc["hours"] += totals["hours"]
                acc["days_worked"] += totals["days_worked"]
    return out
