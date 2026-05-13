"""Per-WC dashboard data-prep helpers.

Pure functions over the existing `cached_leaderboard`, `awards`, and
`work_centers_store` modules. Each helper takes a WC name (or slug) +
a date and returns a widget-ready dict the template can iterate.

The single-page dashboard at /wc/{slug} (editor) and /tv/wc/{slug}
(TV) compose these helpers into one render. No FastAPI / template
imports here — keep this module testable without standing up the app.
"""
from __future__ import annotations

import re


def slug_for_wc(name: str) -> str:
    """URL-safe slug derived from a work-center name.

    Lowercase, alphanumerics + hyphens; everything else collapses to
    a single hyphen. Used as the dashboard layout key (`wc:{slug}`)
    and in URLs (`/wc/{slug}`).

    Examples:
      'Repair 1'       -> 'repair-1'
      'Hand Build #1'  -> 'hand-build-1'
      'Trim Saw 12'    -> 'trim-saw-12'
    """
    s = (name or "").strip().lower()
    # Replace every run of non-alphanumeric chars with a single hyphen.
    s = re.sub(r"[^a-z0-9]+", "-", s)
    # Strip leading + trailing hyphens.
    return s.strip("-")


from datetime import date, datetime, timezone


def _load_wc(name: str):
    """Return the Location for `name`, or None.

    Indirection so tests can monkeypatch this single function. Note
    that the canonical work-center list lives in `staffing.LOCATIONS`
    (work_centers_store re-uses it but doesn't expose `all_locations`).
    """
    from . import staffing
    for loc in staffing.LOCATIONS:
        if loc.name == name:
            return loc
    return None


def wc_by_slug(slug: str):
    """Reverse lookup: slug -> Location. Returns None if no match.

    Linear scan since the WC list is tens of items, not thousands.
    """
    from . import staffing
    target = (slug or "").strip().lower()
    if not target:
        return None
    for loc in staffing.LOCATIONS:
        if slug_for_wc(loc.name) == target:
            return loc
    return None


def assigned_operators_for_wc(wc_name: str, day: date) -> list[str]:
    """Return the names assigned to this specific WC in the published
    schedule for `day`. Empty list if unassigned. Only this WC — not
    the whole group.
    """
    from . import staffing
    try:
        sched = staffing.load_schedule(day)
    except Exception:
        return []
    return list(sched.assignments.get(wc_name, []) or [])


def _shift_elapsed_fraction(day: date) -> float:
    """Fraction of today's shift that has elapsed, 0.0..1.0.

    For days other than today, returns 1.0 (full shift counted). For
    today before shift-start, returns 0.0.
    """
    from . import shift_config
    today_utc = datetime.now(timezone.utc).date()
    if day < today_utc:
        return 1.0
    if day > today_utc:
        return 0.0
    elapsed = shift_config.shift_elapsed_minutes(day, datetime.now(timezone.utc))
    total = shift_config.productive_minutes_for(day) or 1
    return max(0.0, min(1.0, elapsed / total))


def _units_today_for_wc(wc_name: str, day: date) -> int:
    """Today's pallet count for one WC. Reads from the cached Zira
    leaderboard (shared with /recycling), so this is a fast lookup.
    Returns 0 if the WC has no meter or no data yet.
    """
    from .deps import client
    from .leaderboard import cached_leaderboard
    from .stations import Station
    loc = _load_wc(wc_name)
    if loc is None or not loc.meter_id:
        return 0
    stations = [Station(meter_id=loc.meter_id, name=loc.name, category=loc.skill, cell=loc.bay)]
    try:
        results = cached_leaderboard(client, stations, day)
    except Exception:
        return 0
    for r in results:
        if r.station.name == wc_name:
            return int(r.units)
    return 0


def pallets_banner(wc_name: str, day: date) -> dict:
    """Pallets-banner widget data. Today's units for THIS WC against
    the prorated daily target.

    Returns: {units_today, target_today, target_full_day, pct_of_target}.
    """
    from . import work_centers_store
    loc = _load_wc(wc_name)
    if loc is None:
        return {"units_today": 0, "target_today": 0, "target_full_day": 0, "pct_of_target": None}
    full = int(work_centers_store.goal_per_day(loc) or 0)
    frac = _shift_elapsed_fraction(day)
    target_today = int(round(full * frac))
    units = _units_today_for_wc(wc_name, day)
    pct = (units / target_today * 100.0) if target_today > 0 else None
    return {
        "units_today": units,
        "target_today": target_today,
        "target_full_day": full,
        "pct_of_target": pct,
    }


def monthly_ribbons(wc_name: str, year: int, month: int) -> dict:
    """Top-3 person-days in this WC's group for the given month."""
    from . import awards, work_centers_store
    loc = _load_wc(wc_name)
    if loc is None:
        return {"group": None, "entries": []}
    grp_list = work_centers_store.groups(loc) or []
    if not grp_list:
        return {"group": None, "entries": []}
    group = grp_list[0]
    entries = awards.monthly_badges(group, year, month) or []
    return {"group": group, "entries": entries}


def goat_race(wc_name: str, day: date) -> dict:
    """GOAT-race widget. Compares today's pace at this WC against the
    WC's group's all-time GOAT day, prorated by elapsed shift fraction.

    status: 'AHEAD' / 'ON_PACE' / 'BEHIND' / None (if no GOAT yet).
    """
    from . import awards, work_centers_store
    loc = _load_wc(wc_name)
    if loc is None:
        return {"group": None, "goat": None, "units_today": 0, "goat_pace_today": 0, "status": None}
    grp_list = work_centers_store.groups(loc) or []
    group = grp_list[0] if grp_list else None
    goat = awards.goat(group) if group else None
    units = _units_today_for_wc(wc_name, day)
    if goat is None:
        return {"group": group, "goat": None, "units_today": units, "goat_pace_today": 0, "status": None}
    frac = _shift_elapsed_fraction(day)
    goat_pace_today = float(goat.get("units", 0)) * frac
    # Status thresholds — within ±5 % of pace is "ON_PACE", otherwise
    # AHEAD / BEHIND.
    if goat_pace_today <= 0:
        status = None
    else:
        delta_pct = (units - goat_pace_today) / goat_pace_today * 100.0
        if delta_pct > 5:
            status = "AHEAD"
        elif delta_pct < -5:
            status = "BEHIND"
        else:
            status = "ON_PACE"
    return {
        "group": group,
        "goat": goat,
        "units_today": units,
        "goat_pace_today": goat_pace_today,
        "status": status,
    }
