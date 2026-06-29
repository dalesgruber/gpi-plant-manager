"""Detect Odoo work-schedule conflicts with the plant's workdays.

Read-only. Shared by the CLI diagnostic (scripts/diagnose_odoo_calendar_conflicts.py)
and the weekly monitor (calendar_conflict_monitor). Classifies each active
Odoo employee's resource.calendar against the plant's operating weekdays.
See docs/superpowers/specs/2026-06-29-calendar-conflict-monitor-design.md
"""

from __future__ import annotations

WD_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_WEEKDAYS = frozenset({0, 1, 2, 3, 4})  # Mon–Fri (0=Mon..6=Sun)


def classify_conflict(plant_weekdays, covered_weekdays, is_flexible=False, has_calendar=True) -> str:
    """Classify one employee's Odoo work schedule against the plant workdays.

    Returns "no_calendar" / "flexible" / "missing_days" / "ok". Odoo rejects a
    fixed-period leave for the first three; "ok" is fine.
    """
    if not has_calendar:
        return "no_calendar"
    if is_flexible or not covered_weekdays:
        return "flexible"
    if set(plant_weekdays) - set(covered_weekdays):
        return "missing_days"
    return "ok"


def fmt_days(days) -> str:
    return ", ".join(WD_ABBR[d] for d in sorted(days)) if days else "—"


def plant_weekdays():
    """(weekdays, note). Real plant work-week from Postgres; falls back to
    Mon–Fri (with a note) when the DB isn't reachable."""
    try:
        from . import schedule_store

        wd = schedule_store.current().work_weekdays
        if wd:
            return frozenset(wd), None
        return DEFAULT_WEEKDAYS, None
    except Exception as e:  # noqa: BLE001 -- DB optional; default the work-week
        return DEFAULT_WEEKDAYS, (
            f"Plant work-week unavailable ({type(e).__name__}); assuming Mon–Fri."
        )


def _load_roster_by_id():
    """({odoo_id: Person}, note). Optional Postgres enrichment to restrict to
    rostered people and drop reserves. (None, note) when the DB isn't
    reachable, so callers fall back to all active Odoo employees."""
    try:
        from . import staffing

        by_id = {
            int(p.employee_id): p
            for p in staffing.load_roster()
            if p.employee_id is not None
        }
        return by_id, None
    except Exception as e:  # noqa: BLE001 -- roster is optional enrichment
        return None, (
            f"Local roster unavailable ({type(e).__name__}); listing ALL active "
            "Odoo employees (reserves not filtered out)."
        )


def gather_rows(plant_set):
    """Classify active Odoo employees against `plant_set`. Returns (rows, notes).

    Odoo is the population; the local roster is optional enrichment. Each row:
    {name, odoo_id, cal_name, covered, missing, verdict}.
    """
    from . import odoo_client

    employees = odoo_client.fetch_employees()  # active only
    roster_by_id, roster_note = _load_roster_by_id()

    cal_meta = {
        int(s["id"]): (s.get("name") or "(unnamed)", bool(s.get("is_flexible")))
        for s in odoo_client.fetch_work_schedules()
    }

    emp_cal: dict[int, int | None] = {}
    for e in employees:
        cal_id = odoo_client.unwrap_m2o(e.get("resource_calendar_id"))
        valid = isinstance(cal_id, int) and not isinstance(cal_id, bool)
        emp_cal[int(e["id"])] = cal_id if valid else None

    cal_ids = {c for c in emp_cal.values() if c is not None}
    covered = {
        cid: {int(wd) for wd in days}
        for cid, days in odoo_client.fetch_calendar_hours(cal_ids).items()
    }

    plant = set(plant_set)
    rows = []
    for e in employees:
        eid = int(e["id"])
        if roster_by_id is not None:
            p = roster_by_id.get(eid)
            # Only flag people the absence flow actually applies to. The
            # Late/Absence report (late_report.report_eligible_emp_ids) covers
            # HOURLY + FIXED-schedule people only — flexible/salaried people are
            # never declared absent, so a calendar gap can't break an absence
            # sync for them. Mirror that eligibility here.
            if p is None or p.reserve:
                continue  # not rostered, or a reserve
            if p.wage_type != "hourly" or getattr(p, "is_flexible", False):
                continue  # salaried/flexible — outside the late/absence flow
        cal_id = emp_cal.get(eid)
        has_cal = cal_id is not None
        if has_cal:
            cal_name, is_flex = cal_meta.get(cal_id, ("(unknown)", False))
            cov = covered.get(cal_id, set())
        else:
            cal_name, is_flex, cov = "(no Odoo work schedule)", False, set()
        rows.append({
            "name": e.get("name") or f"(id {eid})",
            "odoo_id": eid,
            "cal_name": cal_name,
            "covered": cov,
            "missing": plant - cov,
            "verdict": classify_conflict(plant, cov, is_flexible=is_flex, has_calendar=has_cal),
        })
    return rows, [n for n in (roster_note,) if n]


def current_conflicts():
    """Conflict rows only (verdict != 'ok'), for the monitor."""
    weekdays, _note = plant_weekdays()
    rows, _notes = gather_rows(weekdays)
    return [r for r in rows if r["verdict"] != "ok"]
