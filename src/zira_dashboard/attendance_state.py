"""Reconciled timeclock attendance state, shared by the kiosk route and the
auto-lunch worker.

Extracted from routes/timeclock.py so the background worker reasons about the
same Odoo-reconciled clocked-in/out state the kiosk uses, without a
routes<-service import cycle. state_from_log/trust_local are pure (unit-testable
with no DB/Odoo); current_state wires the two local reads around them.

See docs/superpowers/specs/2026-06-01-timeclock-odoo-state-reconciliation-design.md.
"""
from __future__ import annotations

from datetime import datetime

from . import db, live_cache


def latest_punch(person_odoo_id: int) -> dict | None:
    rows = db.query(
        "SELECT action, wc_name, "
        "COALESCE(rounded_at, occurred_at) AS occurred_at, "
        "odoo_attendance_id, synced_to_odoo, synced_at "
        "FROM timeclock_punches_log WHERE person_odoo_id = %s "
        "ORDER BY occurred_at DESC, id DESC LIMIT 1",
        (person_odoo_id,),
    )
    return rows[0] if rows else None


def state_from_log(latest: dict | None) -> dict:
    if latest is None or latest["action"] in ("clock_out", "transfer_out"):
        return {"is_clocked_in": False, "current_wc": None,
                "check_in_ts": None, "open_odoo_attendance_id": None}
    return {"is_clocked_in": True, "current_wc": latest["wc_name"],
            "check_in_ts": latest["occurred_at"],
            "open_odoo_attendance_id": latest["odoo_attendance_id"]}


def trust_local(latest: dict | None, refreshed_at) -> bool:
    if latest is None:
        return False
    if not latest.get("synced_to_odoo"):
        return True
    synced_at = latest.get("synced_at")
    if synced_at is None:
        return True
    return refreshed_at <= synced_at


def current_state(person_odoo_id: int) -> dict:
    snapshot, refreshed_at = live_cache.read_open_attendance()
    latest = latest_punch(person_odoo_id)
    if snapshot is None or live_cache.is_stale(refreshed_at):
        return state_from_log(latest)
    if trust_local(latest, refreshed_at):
        return state_from_log(latest)
    entry = snapshot.get(str(person_odoo_id))
    if not entry:
        return {"is_clocked_in": False, "current_wc": None,
                "check_in_ts": None, "open_odoo_attendance_id": None}
    check_in = entry.get("check_in")
    return {"is_clocked_in": True, "current_wc": entry.get("wc_name"),
            "check_in_ts": datetime.fromisoformat(check_in) if check_in else None,
            "open_odoo_attendance_id": entry.get("att_id")}
