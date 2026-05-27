"""Per-(person, leave_type) balance cache, sourced from Odoo.

The kiosk wizard needs to show an employee's remaining PTO / sick /
custom-hours balance before they pick dates. A blocking XML-RPC round
trip to Odoo on every wizard open works for one screen and one user,
but the same number is also used by the in-flight calc, the My Requests
screen, and (eventually) the admin calendar — so we cache it locally
in the ``time_off_balances`` table and read from there everywhere.

Three refresh triggers keep the cache fresh (see design spec):

1. **On kiosk wizard open** — ``refresh_for_employee(person_odoo_id)``
   runs synchronously before the wizard renders. ~200-500ms blocking
   call to Odoo is acceptable for one screen, one user, one click.
2. **After every poll cycle** — when the leave poller detects a state
   change for person X, it calls ``invalidate(person_odoo_id)`` so the
   next read re-fetches instead of serving a stale balance.
3. **Periodic safety net** — every 10 minutes the background sweep
   calls ``refresh_stale(older_than_seconds=600)`` to catch anyone
   whose balance changed in Odoo without a corresponding poller event
   (e.g. an admin updating an allocation directly).

A manual "Refresh now" button on the Time Off landing page also calls
``refresh_for_employee`` for an immediate forced refresh.

Errors are swallowed
--------------------
``refresh_for_employee`` catches every Odoo exception and logs it. The
caller still gets to render the wizard with whatever balance is in cache
from a prior refresh — better than crashing the request just because
Odoo blipped.
"""

from __future__ import annotations

import logging

from . import db, odoo_client

_log = logging.getLogger(__name__)

# Default age threshold for the periodic safety-net sweep, in seconds.
# Mirrors the poller cadence in the design spec (10 minutes).
_STALE_THRESHOLD_SECONDS = 600


def refresh_for_employee(person_odoo_id: int) -> int:
    """Fetch all balances for one employee from Odoo and upsert into cache.

    Returns the count of balance rows written. Swallows Odoo exceptions
    (logged at INFO) — caller still gets to render the wizard with
    whatever's in cache from a prior refresh.
    """
    try:
        balances = odoo_client.fetch_balances_for(person_odoo_id)
    except Exception as e:  # noqa: BLE001 — record and continue, never crash caller
        _log.info(
            "Balance refresh for employee %s failed: %s",
            person_odoo_id, e,
        )
        return 0
    count = 0
    for b in balances:
        db.execute(
            "INSERT INTO time_off_balances "
            "(person_odoo_id, holiday_status_id, unit, allocated_total, "
            "taken, pending, available, available_practical, last_pulled_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (person_odoo_id, holiday_status_id) DO UPDATE SET "
            "unit = EXCLUDED.unit, "
            "allocated_total = EXCLUDED.allocated_total, "
            "taken = EXCLUDED.taken, "
            "pending = EXCLUDED.pending, "
            "available = EXCLUDED.available, "
            "available_practical = EXCLUDED.available_practical, "
            "last_pulled_at = now()",
            (person_odoo_id, b["holiday_status_id"], b["unit"],
             b["allocated_total"], b["taken"], b["pending"],
             b["available"], b["available_practical"]),
        )
        count += 1
    return count


def invalidate(person_odoo_id: int) -> None:
    """Drop cached balances for this person so the next read re-fetches.

    Called by the leave poller after detecting a state change — we'd
    rather serve a slow fresh number than a fast stale one.
    """
    db.execute(
        "DELETE FROM time_off_balances WHERE person_odoo_id = %s",
        (person_odoo_id,),
    )


def refresh_stale(older_than_seconds: int = _STALE_THRESHOLD_SECONDS) -> int:
    """Refresh any person whose cache is older than ``older_than_seconds``.

    Used by the periodic safety-net sweep. Returns the total count of
    balance rows refreshed across all stale employees.
    """
    rows = db.query(
        "SELECT DISTINCT person_odoo_id FROM time_off_balances "
        "WHERE last_pulled_at < now() - (%s || ' seconds')::interval",
        (str(older_than_seconds),),
    )
    refreshed = 0
    for r in rows:
        refreshed += refresh_for_employee(r["person_odoo_id"])
    return refreshed


def get_for_employee(person_odoo_id: int) -> list[dict]:
    """Read cached balances for one employee, sorted by holiday_status_id.

    Caller is responsible for triggering ``refresh_for_employee`` first
    if the cache is empty or stale — this function is a pure read.
    """
    return db.query(
        "SELECT holiday_status_id, unit, allocated_total, taken, pending, "
        "available, available_practical, last_pulled_at "
        "FROM time_off_balances WHERE person_odoo_id = %s "
        "ORDER BY holiday_status_id",
        (person_odoo_id,),
    )
