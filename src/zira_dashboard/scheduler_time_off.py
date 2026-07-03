"""Scheduler-facing time-off entries sourced from the Odoo-backed
``time_off_requests`` mirror (replacing the StratusTime feed).

Returns the same dict shape ``routes/staffing.py`` already consumes:
``{name, hours, pay_type, time_range, derived, manual_absent, pending}``.
Full-day requests use ``hours=None`` (not partial); partial shapes use the
off-window span (``hour_to - hour_from``). ``pending`` flags requests not yet
approved in Odoo (``state != 'validate'``) so the template can style them.
"""
from __future__ import annotations

from datetime import date as _date

from . import db
from .time_format import fmt_decimal_hour

# Requests in these states count as "happening" on the scheduler. 'validate'
# is approved; the rest are pending. Refused/cancelled/draft-cancel excluded.
_APPROVED = "validate"
_PENDING = ("draft", "confirm", "validate1")
_VISIBLE_STATES = (_APPROVED,) + _PENDING


def _timing_label(r: dict) -> str:
    """Privacy-safe timing for a scheduler row — the leave *type* is
    deliberately omitted (coworkers see that someone's out and when, not
    why; the type can be sensitive). Full-day rows get no timing text at
    all (the name alone in the Off-today list says it); the three partial
    shapes read:

      late_arrival -> "arrives 7:30am"        (arrival = hour_to)
      early_leave  -> "leaves 2:00pm"          (leave   = hour_from)
      midday_gap   -> "gone 10:00am–12:00pm"   (gap window)

    Mirrors routes/timeclock_time_off._label_for (the Who's Out calendar),
    minus its "full day" string.
    """
    shape = r["shape"]
    if shape == "full_day":
        return ""
    hf = float(r["hour_from"] or 0)
    ht = float(r["hour_to"] or 0)
    if shape == "late_arrival":
        return f"arrives {fmt_decimal_hour(ht)}"
    if shape == "early_leave":
        return f"leaves {fmt_decimal_hour(hf)}"
    if shape == "midday_gap":
        return f"gone {fmt_decimal_hour(hf)}–{fmt_decimal_hour(ht)}"
    return ""


def _rows_for_day(day: _date) -> list[dict]:
    return db.query(
        "SELECT p.name AS name, r.shape AS shape, "
        "r.hour_from AS hour_from, r.hour_to AS hour_to, r.state AS state, "
        "COALESCE(lt.name, 'Time Off') AS pay_type "
        "FROM time_off_requests r "
        "JOIN people p ON p.odoo_id = r.person_odoo_id "
        "LEFT JOIN leave_types_cache lt "
        "  ON lt.holiday_status_id = r.holiday_status_id "
        "WHERE r.state = ANY(%s) "
        "AND r.date_from <= %s AND r.date_to >= %s "
        "ORDER BY p.name",
        (list(_VISIBLE_STATES), day, day),
    )


def _cleared_partial_names(day: _date) -> set[str]:
    """Names whose partial for ``day`` a supervisor has 'cleared' (marked as
    actually worked). Shared name+day store with the StratusTime path's undo
    (``cleared_partials_by_name``). Never raises — a store hiccup just means
    nothing is filtered."""
    from . import late_report
    try:
        return late_report.cleared_partial_names_for_day(day)
    except Exception:  # noqa: BLE001 — degrade to "nothing cleared"
        return set()


def time_off_entries_for_day(day: _date) -> list[dict]:
    """List of scheduler time-off entries for ``day`` (approved + pending).

    Partial entries a supervisor has cleared for the day (× "they actually
    worked through it") are dropped — same name-based clear the StratusTime
    path used. Full-day absences are never affected by a partial clear.
    """
    out: list[dict] = []
    for r in _rows_for_day(day):
        is_full = r["shape"] == "full_day"
        if is_full:
            hours = None
            time_range = ""
        else:
            hf = float(r["hour_from"] or 0)
            ht = float(r["hour_to"] or 0)
            hours = round(ht - hf, 2)
            time_range = f"{fmt_decimal_hour(hf)}–{fmt_decimal_hour(ht)}"
        out.append({
            "name": r["name"],
            "hours": hours,
            "pay_type": r["pay_type"],
            "time_range": time_range,
            "timing_label": _timing_label(r),
            "derived": False,
            "manual_absent": False,
            "pending": r["state"] != _APPROVED,
        })
    cleared = _cleared_partial_names(day)
    if cleared:
        # Any positive off-span is a partial (full days carry hours=None);
        # only partials are clearable, so full-day rows never get dropped.
        out = [
            e for e in out
            if not ((e["hours"] or 0) > 0 and e["name"] in cleared)
        ]
    # Manually declared absences become full-day "Absent" entries (rendered
    # light red via the template's manual_absent -> .absent class). An absence
    # overrides any other entry for that person: drop theirs, add one Absent.
    from . import late_report
    try:
        absent = late_report.absent_names_for_day(day)
    except Exception:  # noqa: BLE001 — degrade to "no declared absences"
        absent = set()
    if absent:
        out = [e for e in out if e["name"] not in absent]
        for name in sorted(absent):
            out.append({
                "name": name,
                "hours": None,
                "pay_type": "Absent",
                "time_range": "",
                "timing_label": "Absent",
                "derived": False,
                "manual_absent": True,
                "pending": False,
            })
    return out


def full_day_off_names(day: _date) -> set[str]:
    """Names of people who are off the WHOLE day (full_day shape). Partial-day
    people are intentionally excluded so they stay on the schedulable roster
    with a badge instead of disappearing."""
    return {
        r["name"] for r in _rows_for_day(day) if r["shape"] == "full_day"
    }
