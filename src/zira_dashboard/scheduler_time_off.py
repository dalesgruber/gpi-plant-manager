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

# Requests in these states count as "happening" on the scheduler. 'validate'
# is approved; the rest are pending. Refused/cancelled/draft-cancel excluded.
_APPROVED = "validate"
_PENDING = ("draft", "confirm", "validate1")
_VISIBLE_STATES = (_APPROVED,) + _PENDING


def _fmt_hf(h: float) -> str:
    """Decimal-hour float -> 12-hour clock, e.g. 6.5 -> '6:30am'."""
    hh = int(h)
    mm = int(round((h - hh) * 60))
    suffix = "am" if hh < 12 else "pm"
    disp = hh if hh <= 12 else hh - 12
    if disp == 0:
        disp = 12
    return f"{disp}:{mm:02d}{suffix}"


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
        return f"arrives {_fmt_hf(ht)}"
    if shape == "early_leave":
        return f"leaves {_fmt_hf(hf)}"
    if shape == "midday_gap":
        return f"gone {_fmt_hf(hf)}–{_fmt_hf(ht)}"
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
            time_range = f"{_fmt_hf(hf)}–{_fmt_hf(ht)}"
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
        out = [
            e for e in out
            if not (0 < (e["hours"] or 0) < 8 and e["name"] in cleared)
        ]
    return out


def full_day_off_names(day: _date) -> set[str]:
    """Names of people who are off the WHOLE day (full_day shape). Partial-day
    people are intentionally excluded so they stay on the schedulable roster
    with a badge instead of disappearing."""
    return {
        r["name"] for r in _rows_for_day(day) if r["shape"] == "full_day"
    }
