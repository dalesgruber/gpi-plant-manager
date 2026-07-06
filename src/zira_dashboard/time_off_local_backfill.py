"""Replay locally-recorded absences into Odoo once Odoo will accept them.

A ``time_off_requests`` row with ``local_record = TRUE`` exists because Odoo
refused to validate the leave: the requested day(s) had zero working hours —
either a company holiday record covered them (e.g. the plant worked the
observed 4th of July while Odoo said "closed") or the employee's Working
Schedule was missing the weekday. The approve fallback recorded the absence
here and settled the Odoo copy as refused.

This reconciler runs on a slow warmer and, for each local record, predicts
whether Odoo would accept the leave *now* (at least one day in the span is a
covered weekday with no public holiday). Only when the prediction passes does
it touch Odoo: reset the refused copy to draft (or recreate it if HR deleted
it), then confirm + approve. On success the row hands ownership back to the
poller (``local_record = FALSE``) and the absence lives in Odoo like any
other approved leave — visible to HR. On any surprise the Odoo copy is
re-refused so it never lingers pending.

Prediction failures are silent and free (three fleet-wide reads per tick,
none when there are no local records): a Saturday absence for a Mon–Fri
calendar simply stays app-only until the underlying Odoo data changes.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from . import db, employee_notifications, odoo_client

_log = logging.getLogger(__name__)

# Rows attempted per tick. Local records are rare; the cap bounds the Odoo
# RPC fan-out if a backlog ever accumulates.
_ATTEMPT_LIMIT = 10


def _holiday_dates(start_d: date, end_d: date) -> set[date]:
    """Every calendar day touched by a company-wide holiday record.

    Day-granularity on the holiday's UTC datetime bounds is deliberately
    conservative: a holiday stored as 05:00 UTC → 04:59 UTC next day (a CT
    midnight-to-midnight day) blocks both touched dates. Worst case a valid
    replay waits for the next tick after the window clears — never the
    reverse."""
    out: set[date] = set()
    for h in odoo_client.fetch_public_holidays(start_d, end_d):
        try:
            d = date.fromisoformat(str(h["date_from"])[:10])
            last = date.fromisoformat(str(h["date_to"])[:10])
        except (KeyError, ValueError):
            continue
        while d <= last:
            out.add(d)
            d += timedelta(days=1)
    return out


def _has_working_day(row: dict[str, Any], covered_weekdays: set[int],
                     holidays: set[date]) -> bool:
    """True when at least one day of the span has working hours — Odoo's
    validation only needs ``number_of_days > 0``, not full coverage."""
    d = row["date_from"]
    while d <= row["date_to"]:
        if d.weekday() in covered_weekdays and d not in holidays:
            return True
        d += timedelta(days=1)
    return False


def _adopt(row: dict[str, Any]) -> None:
    """Hand the row back to the poller: Odoo now holds the validated leave.
    The denied-popup suppression is retired too — from here on genuine
    Odoo-side resolutions must notify normally again."""
    db.execute(
        "UPDATE time_off_requests SET local_record = FALSE, "
        "synced_to_odoo = TRUE, sync_error = NULL, "
        "last_pushed_at = now(), updated_at = now() WHERE id = %s",
        (row["id"],),
    )
    try:
        employee_notifications.unsuppress_resolution(
            row["id"], "time_off_denied")
    except Exception:  # noqa: BLE001 — cosmetic relative to the adoption
        _log.warning("unsuppress failed for request %s", row["id"],
                     exc_info=True)


def _attempt(row: dict[str, Any]) -> bool:
    """Replay one local record through Odoo. Returns True when Odoo ended
    up holding the validated leave and the row was handed back."""
    leave_id = row.get("odoo_leave_id")
    state = (odoo_client.fetch_leave_state(int(leave_id))
             if leave_id is not None else None)
    if state == "validate":
        # HR already fixed and re-approved it directly in Odoo.
        _adopt(row)
        return True
    try:
        if state is None:
            # The refused copy was deleted in Odoo — recreate it. Link the
            # new id to the row (still flagged) BEFORE the workflow runs so
            # a concurrent poll tick maps the new leave onto this row
            # instead of inserting a duplicate mirror row.
            leave_id = odoo_client.create_leave(
                row["person_odoo_id"],
                row["holiday_status_id"],
                row["date_from"],
                row["date_to"],
                hour_from=(float(row["hour_from"])
                           if row.get("hour_from") is not None else None),
                hour_to=(float(row["hour_to"])
                         if row.get("hour_to") is not None else None),
                note=row.get("note"),
            )
            db.execute(
                "UPDATE time_off_requests SET odoo_leave_id = %s, "
                "updated_at = now() WHERE id = %s",
                (leave_id, row["id"]),
            )
        elif state == "refuse":
            odoo_client.draft_leave(int(leave_id))
        final = odoo_client.approve_leave(int(leave_id))
        if final != "validate":
            raise RuntimeError(f"leave {leave_id} ended in state {final}")
    except Exception as e:  # noqa: BLE001 — re-settle and wait for next tick
        _log.info("backfill attempt failed for request %s (leave %s): %s",
                  row["id"], leave_id, e)
        try:
            if leave_id is not None:
                odoo_client.refuse_leave(int(leave_id))
        except Exception:  # noqa: BLE001
            _log.warning("could not re-refuse leave %s after failed "
                         "backfill", leave_id, exc_info=True)
        return False
    _adopt(row)
    _log.warning("backfilled local absence into Odoo: request %s -> "
                 "leave %s (person %s, %s..%s)", row["id"], leave_id,
                 row["person_odoo_id"], row["date_from"], row["date_to"])
    return True


def run_once() -> int:
    """One reconciler pass. Returns the number of rows backfilled."""
    rows = db.query(
        "SELECT id, person_odoo_id, shape, holiday_status_id, "
        "date_from, date_to, hour_from, hour_to, note, odoo_leave_id "
        "FROM time_off_requests "
        "WHERE local_record AND state = 'validate' "
        "ORDER BY date_from, id LIMIT %s",
        (_ATTEMPT_LIMIT,),
    )
    if not rows:
        return 0
    employees = odoo_client.fetch_employees()
    cal_by_emp = {
        e["id"]: odoo_client.unwrap_m2o(e.get("resource_calendar_id"))
        for e in employees
    }
    cal_ids = sorted({c for c in cal_by_emp.values() if c})
    hours = odoo_client.fetch_calendar_hours(cal_ids) if cal_ids else {}
    holidays = _holiday_dates(
        min(r["date_from"] for r in rows),
        max(r["date_to"] for r in rows),
    )
    done = 0
    for row in rows:
        cal_id = cal_by_emp.get(row["person_odoo_id"])
        covered = {int(k) for k in (hours.get(cal_id) or {})}
        if not _has_working_day(row, covered, holidays):
            continue
        if _attempt(row):
            done += 1
    return done
