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
    return {}
