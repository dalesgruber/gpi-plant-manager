"""Decision context for time-off approvals: remaining balance and
same-day coverage, computed from local mirrors only (no live Odoo).

Coverage is scoped to the requester's department, derived from their
default work-center membership (work_center_default_people ->
work_centers.department), with a plant-wide fallback when no department
resolves. Balance reads the time_off_balances cache.
"""
from __future__ import annotations

from datetime import date

from . import db


def department_for_person(person_odoo_id: int) -> set[str]:
    """Departments the person is a default member of (via their default
    work centers). Empty set when they map to no department."""
    rows = db.query(
        "SELECT DISTINCT wc.department "
        "FROM work_center_default_people wcdp "
        "JOIN work_centers wc ON wc.id = wcdp.wc_id "
        "JOIN people pe ON pe.id = wcdp.person_id "
        "WHERE pe.odoo_id = %s AND wc.department IS NOT NULL "
        "AND wc.department <> ''",
        (person_odoo_id,),
    )
    return {r["department"] for r in rows if r.get("department")}


def coverage_for(person_odoo_id: int, date_from: date, date_to: date) -> dict:
    """Count OTHER people with an approved leave overlapping [date_from,
    date_to]. Scoped to the requester's department when known, else
    plant-wide. Returns {'count': int, 'scope': 'department'|'plant'}."""
    depts = department_for_person(person_odoo_id)
    if depts:
        rows = db.query(
            "SELECT COUNT(DISTINCT r.person_odoo_id) AS n "
            "FROM time_off_requests r "
            "JOIN people pe ON pe.odoo_id = r.person_odoo_id "
            "JOIN work_center_default_people wcdp ON wcdp.person_id = pe.id "
            "JOIN work_centers wc ON wc.id = wcdp.wc_id "
            "WHERE r.state = 'validate' AND r.person_odoo_id <> %s "
            "AND r.date_to >= %s AND r.date_from <= %s "
            "AND wc.department = ANY(%s)",
            (person_odoo_id, date_from, date_to, list(depts)),
        )
        return {"count": int(rows[0]["n"] if rows else 0), "scope": "department"}
    rows = db.query(
        "SELECT COUNT(DISTINCT r.person_odoo_id) AS n "
        "FROM time_off_requests r "
        "WHERE r.state = 'validate' AND r.person_odoo_id <> %s "
        "AND r.date_to >= %s AND r.date_from <= %s",
        (person_odoo_id, date_from, date_to),
    )
    return {"count": int(rows[0]["n"] if rows else 0), "scope": "plant"}


def balance_for(person_odoo_id: int, holiday_status_id: int) -> dict | None:
    """Remaining balance for one (person, leave type) from the local cache,
    or None when no balance row exists. {'remaining': float, 'unit': str}."""
    rows = db.query(
        "SELECT available, unit FROM time_off_balances "
        "WHERE person_odoo_id = %s AND holiday_status_id = %s",
        (person_odoo_id, holiday_status_id),
    )
    if not rows:
        return None
    return {"remaining": float(rows[0]["available"]), "unit": rows[0]["unit"]}


def request_amount(row: dict) -> tuple[float, str]:
    """Approximate amount + unit a request consumes, for the balance warning.
    Hour-bounded requests -> hours; otherwise inclusive day count."""
    hf, ht = row.get("hour_from"), row.get("hour_to")
    if hf is not None and ht is not None:
        return (float(ht) - float(hf), "hours")
    days = (row["date_to"] - row["date_from"]).days + 1
    return (float(days), "days")
