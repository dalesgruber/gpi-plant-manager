"""Decision context for time-off approvals: remaining balance and
same-day coverage, computed from local mirrors only (no live Odoo).

Coverage is scoped to the requester's department, derived from their
default work-center membership (work_center_default_people ->
work_centers.department), with a plant-wide fallback when no department
resolves. Balance reads the time_off_balances cache.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db, time_off_calendar


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


def _timing_label(r: dict) -> str:
    """Privacy-safe timing for one off-person ('full day', 'arrives 9:00am',
    'leaves 2:00pm', '10:00am–12:00pm'). Reuses the kiosk label engine; a
    leave with no hour bounds is a full day."""
    if r.get("hour_from") is None or r.get("hour_to") is None:
        return "full day"
    return time_off_calendar.label_for(r)


def _person_entry(r: dict, pending: bool, requester_depts: set[str]) -> dict:
    person_depts = set(r.get("depts") or ())
    return {
        "name": r["name"],
        "dept": (sorted(person_depts)[0] if person_depts else None),
        "label": _timing_label(r),
        "pending": pending,
        "same_dept": bool(person_depts & requester_depts),
    }


def coverage_breakdown(
    approved: list[dict],
    pending: list[dict],
    holiday_names: dict[date, str],
    requester_depts: set[str],
    date_from: date,
    date_to: date,
    requester_odoo_id: int,
    plant_peak_threshold: int = 3,
    max_days: int = 10,
) -> dict:
    """Per-day 'who else is off' breakdown over [date_from, date_to].

    Pure (no I/O). ``approved``/``pending`` rows carry person_odoo_id, name,
    shape, hour_from, hour_to, date_from, date_to, and a ``depts`` set. The
    requester is always excluded; a person counts once per day (approved wins
    over pending). Holidays are surfaced as a per-day flag, never added to the
    people count. Returns the peak day, that day's department count, severity,
    and a capped per-day list (only days with someone off OR a plant closure)."""
    requester_depts = set(requester_depts or ())

    def collect(day: date) -> list[dict]:
        seen: dict[int, dict] = {}
        for r in approved:
            pid = r["person_odoo_id"]
            if pid == requester_odoo_id or pid in seen:
                continue
            if r["date_from"] <= day <= r["date_to"]:
                seen[pid] = _person_entry(r, False, requester_depts)
        for r in pending:
            pid = r["person_odoo_id"]
            if pid == requester_odoo_id or pid in seen:
                continue
            if r["date_from"] <= day <= r["date_to"]:
                seen[pid] = _person_entry(r, True, requester_depts)
        return list(seen.values())

    full: list[dict] = []
    day = date_from
    while day <= date_to:
        people = collect(day)
        holiday = holiday_names.get(day)
        if people or holiday:
            people.sort(key=lambda p: (not p["same_dept"], p["name"].lower()))
            full.append({
                "date": day,
                "count": len(people),
                "dept_count": sum(1 for p in people if p["same_dept"]),
                "holiday": holiday,
                "people": people,
            })
        day = day + timedelta(days=1)

    peak_count, peak_date, peak_dept_count = 0, None, 0
    for entry in full:
        if entry["count"] > peak_count:
            peak_count = entry["count"]
            peak_date = entry["date"]
            peak_dept_count = entry["dept_count"]

    if peak_count == 0:
        severity = "clear"
    elif peak_dept_count > 0 or peak_count >= plant_peak_threshold:
        severity = "warn"
    else:
        severity = "ok"

    return {
        "severity": severity,
        "peak_count": peak_count,
        "peak_date": peak_date,
        "peak_dept_count": peak_dept_count,
        "scope": "department" if requester_depts else "plant",
        "dept_label": sorted(requester_depts)[0] if requester_depts else None,
        "has_holiday": any(e["holiday"] for e in full),
        "by_day": full[:max_days],
        "more_days": max(0, len(full) - max_days),
    }


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
