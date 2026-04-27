"""FastAPI app: station status + leaderboard UI + JSON endpoint."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from zira_probe.client import ZiraClient

from fastapi.responses import RedirectResponse

from datetime import time as _time

from . import layout_store, schedule_store, settings_store, shift_config, staffing, widget_customizer, work_centers_store
from .leaderboard import StationTotal, leaderboard
from .progress import progress_buckets
from .shift_config import shift_elapsed_minutes
from .stations import CATEGORIES, STATIONS, Station, recycling_stations

load_dotenv()

_api_key = os.environ.get("ZIRA_API_KEY")
if not _api_key:
    raise RuntimeError("ZIRA_API_KEY missing. Set it in .env.")
_base_url = os.environ.get("ZIRA_BASE_URL", "https://api.zira.us/public/")

client = ZiraClient(api_key=_api_key, base_url=_base_url)

app = FastAPI(title="Zira Station Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

RUNNING_STALENESS = timedelta(minutes=10)


def _parse_day(day: str | None) -> date:
    if not day:
        return datetime.now(timezone.utc).date()
    return date.fromisoformat(day)


def _window_dates(window: str, today_d: date) -> tuple[date, date]:
    """Return (start, end) inclusive for one of: week|month|quarter|year."""
    if window == "month":
        return today_d.replace(day=1), today_d
    if window == "quarter":
        q_start_month = ((today_d.month - 1) // 3) * 3 + 1
        return today_d.replace(month=q_start_month, day=1), today_d
    if window == "year":
        return today_d.replace(month=1, day=1), today_d
    # default: week (Monday → today)
    monday = today_d - timedelta(days=today_d.weekday())
    return monday, today_d


def _filter_stations(category: str | None):
    if not category or category == "All":
        return list(STATIONS)
    return [s for s in STATIONS if s.category == category]


def _state(total: StationTotal, now: datetime, is_today: bool) -> str:
    if total.last_reading_at is None:
        return "Offline"
    if not is_today:
        return "—"
    if now - total.last_reading_at > RUNNING_STALENESS:
        return "Offline"
    if total.last_status == "Working":
        return "Running"
    return "Stopped"


def _relative(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "no data today"
    delta = now - dt
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    m = s // 60
    if m < 60:
        return f"{m} min ago"
    h = m // 60
    return f"{h}h {m % 60}m ago"


def _fmt_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60}m"


@app.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    day: str | None = Query(default=None),
    category: str | None = Query(default=None),
):
    d = _parse_day(day)
    today = datetime.now(timezone.utc).date()
    is_today = d == today
    stations = _filter_stations(category)
    results = leaderboard(client, stations, d)
    now = datetime.now(timezone.utc)

    enriched = []
    counts = {"Running": 0, "Stopped": 0, "Offline": 0}
    for r in results:
        state = _state(r, now, is_today)
        if state in counts:
            counts[state] += 1
        enriched.append(
            {
                "station": r.station,
                "units": r.units,
                "reading_count": r.reading_count,
                "truncated": r.truncated,
                "downtime_minutes": r.downtime_minutes,
                "downtime_display": _fmt_duration(r.downtime_minutes),
                "last_reading_at": r.last_reading_at,
                "last_relative": _relative(r.last_reading_at, now),
                "last_status": r.last_status,
                "state": state,
            }
        )

    top = max((r.units for r in results), default=0)
    category_order = {"Dismantler": 0, "Repair": 1, "Other": 2}
    by_category: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for row in enriched:
        by_category[row["station"].category].append(row)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "day": d.isoformat(),
            "today": today.isoformat(),
            "is_today": is_today,
            "category": category or "All",
            "categories": ("All",) + CATEGORIES,
            "ordered_categories": sorted(CATEGORIES, key=lambda c: category_order.get(c, 99)),
            "rows": enriched,
            "by_category": by_category,
            "counts": counts,
            "top_units": top,
            "refreshed_at": now.strftime("%H:%M:%S UTC"),
        },
    )


@app.get("/api/leaderboard")
def api_leaderboard(
    day: str | None = Query(default=None),
    category: str | None = Query(default=None),
):
    d = _parse_day(day)
    today = datetime.now(timezone.utc).date()
    is_today = d == today
    stations = _filter_stations(category)
    results = leaderboard(client, stations, d)
    now = datetime.now(timezone.utc)
    return JSONResponse(
        {
            "day": d.isoformat(),
            "category": category or "All",
            "stations": [
                {
                    "rank": i + 1,
                    "name": r.station.name,
                    "category": r.station.category,
                    "meter_id": r.station.meter_id,
                    "units": r.units,
                    "reading_count": r.reading_count,
                    "truncated": r.truncated,
                    "downtime_minutes": r.downtime_minutes,
                    "last_reading_at": r.last_reading_at.isoformat() if r.last_reading_at else None,
                    "last_status": r.last_status,
                    "state": _state(r, now, is_today),
                }
                for i, r in enumerate(results)
            ],
        }
    )


@app.get("/recycling", response_class=HTMLResponse)
def recycling(request: Request, day: str | None = Query(default=None)):
    d = _parse_day(day)
    today = datetime.now(timezone.utc).date()
    is_today = d == today
    stations = recycling_stations()
    results = leaderboard(client, stations, d)
    now = datetime.now(timezone.utc)

    total_units = sum(r.units for r in results)
    total_downtime = sum(r.downtime_minutes for r in results)
    elapsed = shift_elapsed_minutes(d, now)
    available = elapsed * len(stations)
    uptime_minutes = max(0, available - total_downtime)
    uptime_pct = (uptime_minutes / available * 100.0) if available > 0 else 0.0
    pallets_per_hour = (total_units / (elapsed / 60.0)) if elapsed > 0 else 0.0

    top_units = max((r.units for r in results), default=0)

    dismantlers = [r for r in results if r.station.category == "Dismantler"]
    dismantlers.sort(key=lambda r: r.station.name)
    repairs = [r for r in results if r.station.category == "Repair"]
    repairs.sort(key=lambda r: r.station.name)

    dism_progress = progress_buckets(dismantlers, d, now)
    repair_progress = progress_buckets(repairs, d, now)
    dism_group_target = settings_store.group_target("Dismantler")
    repair_group_target = settings_store.group_target("Repair")

    customs_all = widget_customizer.load_all("recycling")

    # Time-of-day label for the target-line marker on the bar widgets.
    now_local = now.astimezone(shift_config.SITE_TZ)
    now_label = now_local.strftime("%H:%M")

    def _sorted_bars(items: list, widget_id: str) -> list:
        s = customs_all.get(widget_id, {}).get("sort", "preset")
        if s == "desc":  return sorted(items, key=lambda x: -x["units"])
        if s == "asc":   return sorted(items, key=lambda x: x["units"])
        if s == "alpha": return sorted(items, key=lambda x: x["name"].lower())
        return items

    def _progress_color(pct_of_target: float | None) -> str | None:
        """Vivid 25-step palette: dark red → white (at 100%) → dark green.
        Saturation stays high across the scale; lightness ramps from mid to dark
        so even small deviations from target look clearly green or red (not washed-out)."""
        if pct_of_target is None:
            return None
        p = max(0.0, min(200.0, pct_of_target))
        delta = p - 100.0
        if abs(delta) < 1.0:  # within 1% of target counts as at-target → white
            return "#ffffff"
        step = min(12, max(1, round(abs(delta) / 100.0 * 12)))
        sat = 55.0 + step * 2.0          # 57% → 79%
        light = 65.0 - step * 3.5        # 61.5% → 23%
        hue = 130 if delta > 0 else 0
        return f"hsl({hue:.0f}, {sat:.0f}%, {light:.0f}%)"

    elapsed_hours = elapsed / 60.0 if elapsed else 0.0

    # Who's working each WC today, for widget labels. Drafts are accepted —
    # we want operator names to show even before the day is published.
    sched_for_labels = staffing.load_schedule(d)
    who_by_wc: dict[str, str] = {}
    for wc_name, ops in sched_for_labels.assignments.items():
        if wc_name == staffing.TIME_OFF_KEY or not ops:
            continue
        who_by_wc[wc_name] = " + ".join(ops)

    def _bars(items: list) -> list[dict]:
        out = []
        for r in items:
            station_tgt_hr = settings_store.station_target(r.station)  # pallets/hr
            expected = station_tgt_hr * elapsed_hours
            pct_of_target = (r.units / expected * 100.0) if expected > 0 else None
            out.append(
                {
                    "name": r.station.name,
                    "who": who_by_wc.get(r.station.name, r.station.name),
                    "units": r.units,
                    "pct_of_target": round(pct_of_target, 1) if pct_of_target is not None else None,
                    "expected": int(round(expected)),
                    "color": _progress_color(pct_of_target),
                    "downtime_minutes": r.downtime_minutes,
                    "state": _state(r, now, is_today),
                }
            )
        # Shared scale for bar width + target line position.
        max_u = max((r["units"] for r in out), default=0)
        max_e = max((r["expected"] for r in out), default=0)
        base = max(max_u, max_e)
        scale = (base * 1.1) if base > 0 else 1.0
        has_target_line = max_e > 0
        for r in out:
            r["pct"] = (r["units"] / scale * 100.0) if scale else 0.0
            r["target_pct"] = (r["expected"] / scale * 100.0) if (scale and has_target_line) else None
        return out

    def _downtime_rows(items: list) -> list[dict]:
        out = []
        for r in items:
            working = max(0, elapsed - r.downtime_minutes)
            total = elapsed if elapsed else 1
            out.append(
                {
                    "name": r.station.name,
                    "working": working,
                    "down": r.downtime_minutes,
                    "working_pct": working / total * 100.0,
                    "down_pct": r.downtime_minutes / total * 100.0,
                }
            )
        return out

    return templates.TemplateResponse(
        request,
        "recycling.html",
        {
            "active_vs": "recycling",
            "day": d.isoformat(),
            "today": today.isoformat(),
            "is_today": is_today,
            "total_units": total_units,
            "total_downtime_minutes": total_downtime,
            "total_downtime_display": f"{total_downtime / 60:.1f} h",
            "uptime_pct": round(uptime_pct, 1),
            "pallets_per_hour": round(pallets_per_hour, 1),
            "elapsed_minutes": elapsed,
            "dismantler_bars": _sorted_bars(_bars(dismantlers), "dismantler-bars"),
            "repair_bars": _sorted_bars(_bars(repairs), "repair-bars"),
            "downtime_rows": _downtime_rows(dismantlers + repairs),
            "dismantler_progress": dism_progress,
            "repair_progress": repair_progress,
            "dismantler_group_target": dism_group_target,
            "repair_group_target": repair_group_target,
            "layout": layout_store.layout_map("recycling"),
            "customs": customs_all,
            "now_label": now_label,
            "refreshed_at": now.strftime("%H:%M:%S UTC"),
        },
    )


@app.get("/new-vs", response_class=HTMLResponse)
def new_vs(request: Request, day: str | None = Query(default=None)):
    """Value Streams → New subtab. Shows only work centers whose Settings
    value_stream is "New" and that have a meter ID. Sparse data is the norm
    here today since most "New" stations aren't metered yet."""
    d = _parse_day(day)
    today = datetime.now(timezone.utc).date()
    is_today = d == today
    now = datetime.now(timezone.utc)

    new_locs = [
        loc for loc in staffing.LOCATIONS
        if work_centers_store.value_stream(loc) == "New" and loc.meter_id
    ]
    stations = [
        Station(
            meter_id=loc.meter_id,
            name=loc.name,
            category=loc.skill or "Other",
            cell="New",
        )
        for loc in new_locs
    ]
    results = leaderboard(client, stations, d) if stations else []

    total_units = sum(r.units for r in results)
    total_downtime = sum(r.downtime_minutes for r in results)
    elapsed = shift_elapsed_minutes(d, now)
    available = elapsed * len(stations)
    uptime_minutes = max(0, available - total_downtime)
    uptime_pct = (uptime_minutes / available * 100.0) if available > 0 else 0.0
    pallets_per_hour = (total_units / (elapsed / 60.0)) if elapsed > 0 else 0.0
    elapsed_hours = elapsed / 60.0 if elapsed else 0.0

    def _color(pct: float | None) -> str | None:
        if pct is None:
            return None
        if abs(pct - 100.0) < 1.0:
            return "#ffffff"
        delta = max(-100.0, min(100.0, pct - 100.0))
        step = min(12, max(1, round(abs(delta) / 100.0 * 12)))
        sat = 55.0 + step * 2.0
        light = 65.0 - step * 3.5
        hue = 130 if delta > 0 else 0
        return f"hsl({hue:.0f}, {sat:.0f}%, {light:.0f}%)"

    sched_for_labels = staffing.load_schedule(d)
    who_by_wc: dict[str, str] = {}
    for wc_name, ops in sched_for_labels.assignments.items():
        if wc_name == staffing.TIME_OFF_KEY or not ops:
            continue
        who_by_wc[wc_name] = " + ".join(ops)

    bars: list[dict] = []
    for r in results:
        station_tgt_hr = settings_store.station_target(r.station)
        expected = station_tgt_hr * elapsed_hours
        pct_of_target = (r.units / expected * 100.0) if expected > 0 else None
        bars.append({
            "name": r.station.name,
            "who": who_by_wc.get(r.station.name, r.station.name),
            "units": r.units,
            "expected": int(round(expected)),
            "color": _color(pct_of_target),
            "pct_of_target": round(pct_of_target, 1) if pct_of_target is not None else None,
        })
    base = max((max(b["units"], b["expected"]) for b in bars), default=0)
    scale = (base * 1.1) if base > 0 else 1.0
    for b in bars:
        b["pct"] = (b["units"] / scale * 100.0) if scale else 0.0
    bars.sort(key=lambda x: -x["units"])

    downtime_rows = []
    for r in results:
        working = max(0, elapsed - r.downtime_minutes)
        total = elapsed if elapsed else 1
        downtime_rows.append({
            "name": r.station.name,
            "working": working,
            "down": r.downtime_minutes,
            "working_pct": working / total * 100.0,
            "down_pct": r.downtime_minutes / total * 100.0,
        })

    return templates.TemplateResponse(
        request,
        "new_vs.html",
        {
            "active_vs": "new",
            "day": d.isoformat(),
            "today": today.isoformat(),
            "is_today": is_today,
            "total_units": total_units,
            "total_downtime_display": f"{total_downtime / 60:.1f} h",
            "uptime_pct": round(uptime_pct, 1),
            "pallets_per_hour": round(pallets_per_hour, 1),
            "elapsed_minutes": elapsed,
            "bars": bars,
            "downtime_rows": downtime_rows,
            "refreshed_at": now.strftime("%H:%M:%S UTC"),
        },
    )


@app.get("/staffing", response_class=HTMLResponse)
def staffing_page(
    request: Request,
    day: str | None = Query(default=None),
    publish_blocked: int = Query(default=0),
    view: str = Query(default="draft"),
):
    today = datetime.now(timezone.utc).date()
    # Default to tomorrow (Dale plans the day before).
    try:
        d = date.fromisoformat(day) if day else today + timedelta(days=1)
    except ValueError:
        d = today + timedelta(days=1)
    roster = staffing.load_roster()
    sched = staffing.load_schedule(d)
    # If this day has both a current draft and a posted snapshot, the user may want
    # to view the posted version. Swap the visible fields in from the snapshot.
    has_snapshot = bool(sched.published_snapshot) and not sched.published
    view_mode = view if view in ("draft", "posted") else "draft"
    viewing_posted = has_snapshot and view_mode == "posted"
    if viewing_posted:
        snap = sched.published_snapshot or {}
        sched.assignments = {k: list(v) for k, v in (snap.get("assignments") or {}).items()}
        sched.notes = str(snap.get("notes") or "")
        sched.wc_notes = dict(snap.get("wc_notes") or {})
        sched.testing_day = bool(snap.get("testing_day", False))
    # If the day has no saved assignments, pre-fill from per-work-center defaults.
    if not sched.assignments:
        seeded: dict[str, list[str]] = {}
        for loc in staffing.LOCATIONS:
            dp = work_centers_store.default_people(loc)
            if dp:
                seeded[loc.name] = list(dp)
        if not seeded:  # fallback for first-run: legacy CSV defaults
            seeded = staffing.default_assignments()
        sched.assignments = seeded

    active_people = [p for p in roster if p.active]
    active_by_name = {p.name: p for p in active_people}
    all_by_name = {p.name: p for p in roster}

    def options_for(required: tuple[str, ...]) -> list[dict]:
        """All active people, tagged with trained = (level >= 1 in ALL required skills).
        Untrained people are hidden client-side unless the WC's per-row Training
        checkbox is ticked. Reserves are tagged so they can be split into a
        secondary picker section (office/manager pool, only used when short)."""
        rows = []
        for p in active_people:
            levels = [p.level(s) for s in required] if required else []
            min_lvl = min(levels) if levels else 0
            trained = bool(levels) and all(l >= 1 for l in levels)
            rows.append({
                "name": p.name,
                "level": min_lvl,
                "color": staffing.skill_color(min_lvl),
                "trained": trained,
                "reserve": p.reserve,
            })
        return rows

    # Build a location-level render model and group by bay (preserving LOCATIONS order).
    bays: list[dict] = []
    current_bay: str | None = None
    for loc in staffing.LOCATIONS:
        required = tuple(work_centers_store.required_skills(loc))
        min_ops = work_centers_store.min_ops(loc)
        max_ops = work_centers_store.max_ops(loc)
        assigned_names = sched.assignments.get(loc.name, [])
        assigned = []
        for n in assigned_names:
            p = all_by_name.get(n)
            # Color by the lowest level across required skills.
            lvl = min((p.level(s) for s in required), default=0) if p else 0
            assigned.append({"name": n, "level": lvl, "color": staffing.skill_color(lvl)})
        pool = options_for(required)
        # Reserves go last so the template can split them into the bottom group.
        pool.sort(key=lambda r: (r["reserve"], -r["level"], r["name"].lower()))
        assigned_set = {a["name"] for a in assigned}
        # Ensure currently-assigned people appear in pool even if below the filter.
        # (Assigned names are already in the pool since options_for returns everyone,
        # but inactive/deleted people might have been assigned historically.)
        for a in assigned:
            if not any(r["name"] == a["name"] for r in pool):
                pool.append({"name": a["name"], "level": a["level"], "color": a["color"], "trained": a["level"] >= 1, "reserve": False})
        pool.sort(key=lambda r: (r["reserve"], -r["level"], r["name"].lower()))
        # Headcount status
        count = len(assigned)
        hc_status = "ok"
        if count == 0:
            hc_status = "empty"
        elif count < min_ops:
            hc_status = "under"
        elif max_ops is not None and count > max_ops:
            hc_status = "over"
        # Default people for this WC (editable inline in the scheduler).
        defaults_list = work_centers_store.default_people(loc)
        default_set = set(defaults_list)
        # Auto-open the picker's reserves group when a reserve is currently chosen there.
        has_selected_reserve = any(r["reserve"] and r["name"] in assigned_set for r in pool)
        has_default_reserve = any(r["reserve"] and r["name"] in default_set for r in pool)
        row = {
            "loc": loc,
            "assigned": assigned,
            "pool": pool,
            "assigned_set": assigned_set,
            "min_ops": min_ops,
            "max_ops": max_ops,
            "max_ops_label": ("∞" if max_ops is None else str(max_ops)),
            "required_skills": list(required),
            "default_people": defaults_list,
            "default_set": default_set,
            "has_selected_reserve": has_selected_reserve,
            "has_default_reserve": has_default_reserve,
            "hc_status": hc_status,
            "hc_badge": (
                "needs " + str(min_ops) if hc_status == "under"
                else ("max " + str(max_ops) if hc_status == "over" else "")
            ),
            "wc_note": (sched.wc_notes or {}).get(loc.name, ""),
        }
        if loc.bay != current_bay:
            bays.append({"name": loc.bay, "subtitle": staffing.BAY_SUBTITLES.get(loc.bay, ""), "rows": [row]})
            current_bay = loc.bay
        else:
            bays[-1]["rows"].append(row)

    # Only populate block reasons if we just came back from a failed publish attempt.
    publish_block_reasons = []
    if publish_blocked:
        for bay in bays:
            for r in bay["rows"]:
                if r["hc_status"] == "under" and r["min_ops"] >= 2:
                    publish_block_reasons.append(
                        f"{r['loc'].name} requires {r['min_ops']} operators — currently {len(r['assigned'])}."
                    )

    # Per-WC default operators (set in Settings → Work Centers). Exposed as a
    # plain dict for the scheduler's "Reset to defaults" button.
    defaults_by_loc = {
        loc.name: list(work_centers_store.default_people(loc))
        for loc in staffing.LOCATIONS
    }

    # Time Off list (stored under TIME_OFF_KEY in assignments).
    time_off_names = sched.assignments.get(staffing.TIME_OFF_KEY, [])
    time_off_set = set(time_off_names)
    time_off_pool = [
        {
            "name": p.name,
            "selected": p.name in time_off_set,
        }
        for p in active_people
    ]
    time_off_pool.sort(key=lambda r: r["name"].lower())

    # Unscheduled = active non-reserve people with no station and not on time off.
    # Reserves (office staff / managers) live in their own list regardless of state.
    assigned_today = {
        n
        for key, names in sched.assignments.items()
        if key != staffing.TIME_OFF_KEY
        for n in names
    }
    unassigned = [
        p.name
        for p in active_people
        if not p.reserve and p.name not in assigned_today and p.name not in time_off_set
    ]
    reserves = [p.name for p in active_people if p.reserve]

    return templates.TemplateResponse(
        request,
        "staffing.html",
        {
            "active": "plant",
            "day": d.isoformat(),
            "day_short": d.strftime("%m/%d/%y"),
            "tomorrow": (today + timedelta(days=1)).isoformat(),
            "today": today.isoformat(),
            "published": sched.published,
            "bays": bays,
            "notes": sched.notes or "",
            "testing_day": bool(sched.testing_day),
            "publish_block_reasons": publish_block_reasons,
            "time_off_names": sorted(time_off_names),
            "time_off_pool": time_off_pool,
            "unassigned": sorted(unassigned),
            "reserves": sorted(reserves),
            "defaults_by_loc": defaults_by_loc,
            "skill_labels": staffing.SKILL_LABELS,
            "has_snapshot": has_snapshot,
            "viewing_posted": viewing_posted,
            "view_mode": view_mode,
        },
    )


@app.post("/staffing")
async def staffing_save(
    request: Request,
    day: str = Query(...),
    auto: int = Query(default=0),
):
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return RedirectResponse("/staffing", status_code=303)
    form = await request.form()
    assignments: dict[str, list[str]] = {}
    for loc in staffing.LOCATIONS:
        picked = form.getlist(f"loc__{loc.name}")
        clean = [n.strip() for n in picked if n and n.strip()]
        if clean:
            assignments[loc.name] = clean
        # Default-people per WC: only persist when the JS marks this WC dirty
        # (i.e., the user actually touched its Defaults picker). Otherwise
        # scheduled-only autosaves would re-write defaults from form state on
        # every keystroke, risking accidental clears.
        if form.get(f"defaults_dirty__{loc.name}") == "1":
            picked_defaults = form.getlist(f"default__{loc.name}")
            clean_defaults = [n.strip() for n in picked_defaults if n and n.strip()]
            work_centers_store.save_one(loc, {"default_people": clean_defaults})
    time_off_picked = form.getlist(f"loc__{staffing.TIME_OFF_KEY}")
    time_off_clean = [n.strip() for n in time_off_picked if n and n.strip()]
    if time_off_clean:
        assignments[staffing.TIME_OFF_KEY] = time_off_clean

    action = (form.get("action") or "save").strip().lower()
    override = (form.get("override") or "").strip() == "1"
    notes = (form.get("notes") or "").strip()[:2000]
    wc_notes: dict[str, str] = {}
    for loc in staffing.LOCATIONS:
        v = (form.get(f"wc_note__{loc.name}") or "").strip()[:500]
        if v:
            wc_notes[loc.name] = v
    testing_day = (form.get("testing_day") or "").strip() in ("1", "on", "true")

    # Publish-only block: only when action=publish, not overridden, and any min-≥2 work center is partially staffed.
    publish_block: list[str] = []
    if action == "publish" and not override:
        for loc in staffing.LOCATIONS:
            min_required = work_centers_store.min_ops(loc)
            if min_required < 2:
                continue
            count = len(assignments.get(loc.name, []))
            if 0 < count < min_required:
                publish_block.append(
                    f"{loc.name} requires {min_required} operators — currently {count}."
                )

    existing = staffing.load_schedule(d)

    # Discard-draft action: restore the posted snapshot, clear it, and re-publish.
    if action == "discard_draft" and existing.published_snapshot:
        snap = existing.published_snapshot
        restored = staffing.Schedule(
            day=d,
            published=True,
            assignments={k: list(v) for k, v in (snap.get("assignments") or {}).items()},
            notes=str(snap.get("notes") or ""),
            wc_notes=dict(snap.get("wc_notes") or {}),
            testing_day=bool(snap.get("testing_day", False)),
            published_snapshot=None,
        )
        staffing.save_schedule(restored)
        if (request.headers.get("accept") or "").startswith("application/json"):
            return JSONResponse({"ok": True, "published": True, "discarded": True})
        return RedirectResponse(f"/staffing?day={d.isoformat()}", status_code=303)

    # Determine published state. If publish is blocked, save as draft with existing published state.
    if publish_block:
        published = existing.published
    elif action == "publish":
        published = True
    elif action == "unpublish":
        published = False
    else:
        published = existing.published

    # If the existing day was posted and we're now saving an edit (not publishing),
    # capture a one-time snapshot of the posted version so the user can toggle back.
    published_snapshot = existing.published_snapshot
    if action == "publish" and not publish_block:
        # Re-publish clears any prior snapshot.
        published_snapshot = None
    elif existing.published and action != "publish" and published_snapshot is None:
        # First edit of a posted day: snapshot before overwriting, flip to draft.
        published_snapshot = staffing.snapshot_of(existing)
        published = False
    staffing.save_schedule(staffing.Schedule(
        day=d,
        published=published,
        assignments=assignments,
        notes=notes,
        wc_notes=wc_notes,
        testing_day=testing_day,
        published_snapshot=published_snapshot,
    ))

    # Auto-save (fetch with ?auto=1) → JSON, no redirect.
    if auto or (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True, "published": published, "testing_day": testing_day})

    # If publish was blocked, bounce back to the same day with a flag so the UI can show the alert.
    if publish_block:
        return RedirectResponse(f"/staffing?day={d.isoformat()}&publish_blocked=1", status_code=303)

    # Successful publish: advance to next working day and pre-fill with defaults.
    if action == "publish" and published:
        next_day = _next_working_day(d)
        next_sched = staffing.load_schedule(next_day)
        if not next_sched.assignments:
            defaults: dict[str, list[str]] = {}
            for loc in staffing.LOCATIONS:
                dp = work_centers_store.default_people(loc)
                if dp:
                    defaults[loc.name] = list(dp)
            if defaults:
                staffing.save_schedule(staffing.Schedule(day=next_day, published=False, assignments=defaults))
        return RedirectResponse(f"/staffing?day={next_day.isoformat()}", status_code=303)

    return RedirectResponse(f"/staffing?day={d.isoformat()}", status_code=303)


def _next_working_day(d: date) -> date:
    """Return the next date after `d` that is a work-day per the shift schedule."""
    wd = schedule_store.current().work_weekdays or frozenset({0, 1, 2, 3, 4})
    nxt = d + timedelta(days=1)
    for _ in range(14):
        if nxt.weekday() in wd:
            return nxt
        nxt += timedelta(days=1)
    return d + timedelta(days=1)


# ---------------- Staffing sub-pages ----------------

def _iter_saved_schedule_files():
    """Yield (date, Schedule) for every saved schedule file, sorted newest first."""
    d = staffing.SCHEDULES_DIR
    if not d.exists():
        return
    files = sorted(d.glob("*.json"), reverse=True)
    for p in files:
        stem = p.stem
        try:
            day = date.fromisoformat(stem)
        except ValueError:
            continue
        sched = staffing.load_schedule(day)
        yield day, sched


def _time_off_by_day() -> dict[date, list[str]]:
    """Flatten all saved schedules → {date: [people off]}."""
    out: dict[date, list[str]] = {}
    for day, sched in _iter_saved_schedule_files():
        names = sched.assignments.get(staffing.TIME_OFF_KEY, []) or []
        if names:
            out[day] = list(names)
    return out


@app.get("/staffing/time-off", response_class=HTMLResponse)
def staffing_time_off(
    request: Request,
    scale: str = Query(default="month"),
    date_: str | None = Query(default=None, alias="date"),
):
    scale = scale if scale in {"day", "week", "month", "quarter", "year"} else "month"
    today = datetime.now(timezone.utc).date()
    try:
        cursor = date.fromisoformat(date_) if date_ else today
    except ValueError:
        cursor = today
    off_map = _time_off_by_day()

    import calendar as _cal
    ctx: dict = {
        "active": "time_off",
        "scale": scale,
        "cursor_iso": cursor.isoformat(),
        "today_iso": today.isoformat(),
    }

    def _month_cells(year: int, month: int):
        weeks = _cal.Calendar(firstweekday=0).monthdatescalendar(year, month)
        out = []
        for week in weeks:
            w = []
            for d in week:
                w.append({
                    "num": d.day,
                    "outside": d.month != month,
                    "is_today": d == today,
                    "weekend": d.weekday() >= 5,
                    "names": off_map.get(d, []),
                    "count": len(off_map.get(d, [])),
                })
            out.append(w)
        return out

    if scale == "day":
        ctx["heading"] = cursor.strftime("%A · %B %d, %Y").replace(" 0", " ")
        ctx["cursor_label"] = ctx["heading"]
        ctx["day_names"] = off_map.get(cursor, [])
        ctx["prev_date"] = (cursor - timedelta(days=1)).isoformat()
        ctx["next_date"] = (cursor + timedelta(days=1)).isoformat()
    elif scale == "week":
        # Week starting Monday.
        monday = cursor - timedelta(days=cursor.weekday())
        days = []
        for i in range(7):
            d = monday + timedelta(days=i)
            days.append({
                "label": d.strftime("%a"),
                "num": d.day,
                "iso": d.isoformat(),
                "is_today": d == today,
                "names": off_map.get(d, []),
            })
        ctx["heading"] = f"Week of {monday.isoformat()}"
        ctx["week_days"] = days
        ctx["prev_date"] = (monday - timedelta(days=7)).isoformat()
        ctx["next_date"] = (monday + timedelta(days=7)).isoformat()
    elif scale == "month":
        ctx["heading"] = cursor.strftime("%B %Y")
        ctx["month_weeks"] = _month_cells(cursor.year, cursor.month)
        # prev / next month
        prev_m = (cursor.replace(day=1) - timedelta(days=1)).replace(day=1)
        # next month
        if cursor.month == 12:
            next_m = cursor.replace(year=cursor.year + 1, month=1, day=1)
        else:
            next_m = cursor.replace(month=cursor.month + 1, day=1)
        ctx["prev_date"] = prev_m.isoformat()
        ctx["next_date"] = next_m.isoformat()
    elif scale == "quarter":
        q_start_month = ((cursor.month - 1) // 3) * 3 + 1
        months = []
        for i in range(3):
            m = q_start_month + i
            y = cursor.year
            months.append({"label": date(y, m, 1).strftime("%B %Y"), "weeks": _month_cells(y, m)})
        ctx["heading"] = f"Q{(q_start_month - 1) // 3 + 1} {cursor.year}"
        ctx["quarter_months"] = months
        ctx["prev_date"] = (date(cursor.year, q_start_month, 1) - timedelta(days=1)).isoformat()
        end = date(cursor.year + (1 if q_start_month + 3 > 12 else 0), ((q_start_month + 2) % 12) + 1, 1)
        ctx["next_date"] = end.isoformat()
    else:  # year
        months = [{"label": date(cursor.year, m, 1).strftime("%b"), "weeks": _month_cells(cursor.year, m)} for m in range(1, 13)]
        ctx["heading"] = str(cursor.year)
        ctx["year_months"] = months
        ctx["prev_date"] = date(cursor.year - 1, cursor.month, 1).isoformat()
        ctx["next_date"] = date(cursor.year + 1, cursor.month, 1).isoformat()

    return templates.TemplateResponse(request, "time_off.html", ctx)


@app.get("/staffing/skills", response_class=HTMLResponse)
def staffing_skills(request: Request):
    roster = staffing.load_roster()
    roster.sort(key=lambda p: (not p.active, p.name.lower()))
    active_count = sum(1 for p in roster if p.active)
    return templates.TemplateResponse(
        request,
        "skills.html",
        {
            "active": "skills",
            "people": roster,
            "skills": list(staffing.SKILLS),
            "active_count": active_count,
            "inactive_count": len(roster) - active_count,
        },
    )


@app.get("/staffing/people", response_class=HTMLResponse)
def staffing_people(request: Request):
    roster = staffing.load_roster()
    active_people = sorted([p for p in roster if p.active], key=lambda p: p.name.lower())
    return templates.TemplateResponse(
        request,
        "people_index.html",
        {"active": "people", "people": active_people},
    )


@app.get("/staffing/people/{name}", response_class=HTMLResponse)
def staffing_player_card(
    request: Request,
    name: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
):
    from . import production_history
    today = datetime.now(timezone.utc).date()
    end_d = date.fromisoformat(end) if end else today
    start_d = date.fromisoformat(start) if start else (end_d - timedelta(days=29))
    range_out = production_history.attribution_range(start_d, end_d, client)
    person = range_out.get(name, {})
    rows = sorted(
        ({"wc": wc, **t} for wc, t in person.items()),
        key=lambda r: -r["units"],
    )
    total_units    = sum(r["units"] for r in rows)
    total_downtime = sum(r["downtime"] for r in rows)
    total_days     = sum(r["days_worked"] for r in rows)
    roster = {p.name: p for p in staffing.load_roster()}
    p = roster.get(name)
    skills = []
    if p:
        skills = sorted(
            ((s, lvl) for s, lvl in p.skills.items() if lvl >= 1),
            key=lambda kv: -kv[1],
        )
    return templates.TemplateResponse(
        request,
        "player_card.html",
        {
            "active": "people",
            "name": name,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "today": today.isoformat(),
            "rows": rows,
            "total_units": round(total_units, 1),
            "total_downtime": round(total_downtime, 1),
            "total_days": total_days,
            "skills": skills,
        },
    )


@app.get("/staffing/leaderboards", response_class=HTMLResponse)
def staffing_leaderboards(
    request: Request,
    window: str = Query(default="week"),
    metric: str = Query(default="pct"),
):
    from . import production_history
    today_d = datetime.now(timezone.utc).date()
    start_d, end_d = _window_dates(window, today_d)
    range_out = production_history.attribution_range(start_d, end_d, client)

    # Group WCs by their `skill` category and compute per-WC daily expected units.
    cats: dict[str, list[staffing.Location]] = {}
    for loc in staffing.LOCATIONS:
        cats.setdefault(loc.skill, []).append(loc)
    expected_per_day_by_wc: dict[str, int] = {}
    for loc in staffing.LOCATIONS:
        target_per_hr = settings_store.station_target(
            Station(meter_id=loc.meter_id, name=loc.name, category=loc.skill, cell=loc.bay)
        )
        expected_per_day_by_wc[loc.name] = int(round(target_per_hr * 8))  # 8 productive hrs

    sections = []
    for skill_name, locs in cats.items():
        wc_names = [loc.name for loc in locs]
        rows = production_history.rank_by_category(
            range_out,
            category_wcs=wc_names,
            expected_units_per_day_by_wc=expected_per_day_by_wc,
            min_days=3,
        )
        if metric == "units":
            rows = sorted(rows, key=lambda r: -r["units"])
        sections.append({"category": skill_name, "rows": rows})
    sections.sort(key=lambda s: s["category"].lower())

    return templates.TemplateResponse(
        request,
        "leaderboards.html",
        {
            "active": "leaderboards",
            "sections": sections,
            "window": window,
            "metric": metric,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
        },
    )


@app.post("/staffing/skills")
async def staffing_skills_save(request: Request):
    form = await request.form()
    roster = staffing.load_roster()
    for person in roster:
        name = person.name
        if form.get(f"active_present__{name}"):
            person.active = form.get(f"active__{name}") in ("on", "1", "true")
        if form.get(f"reserve_present__{name}"):
            person.reserve = form.get(f"reserve__{name}") in ("on", "1", "true")
        for s in staffing.SKILLS:
            v = form.get(f"skill__{name}__{s}")
            if v is not None:
                try:
                    lvl = int(v)
                    if 0 <= lvl <= 3:
                        person.skills[s] = lvl
                except (TypeError, ValueError):
                    pass
    staffing.save_roster(roster)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/staffing/skills", status_code=303)


@app.post("/staffing/people/add")
async def staffing_person_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()[:80]
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    roster = staffing.load_roster()
    if any(p.name.lower() == name.lower() for p in roster):
        return JSONResponse({"ok": False, "error": f"'{name}' already exists"}, status_code=409)
    skills = {s: 0 for s in staffing.SKILLS}
    roster.append(staffing.Person(name=name, active=True, skills=skills))
    staffing.save_roster(roster)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True, "name": name})
    return RedirectResponse(url="/staffing/skills", status_code=303)


@app.post("/staffing/people/delete")
async def staffing_person_delete(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    roster = staffing.load_roster()
    before = len(roster)
    roster = [p for p in roster if p.name != name]
    if len(roster) == before:
        return JSONResponse({"ok": False, "error": f"'{name}' not found"}, status_code=404)
    staffing.save_roster(roster)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True, "removed": name})
    return RedirectResponse(url="/staffing/skills", status_code=303)


ADMIN_PASSWORD = "4840"


@app.post("/staffing/past/unpublish")
async def staffing_past_unpublish(request: Request):
    form = await request.form()
    day_raw = (form.get("day") or "").strip()
    try:
        d = date.fromisoformat(day_raw)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad day"}, status_code=400)
    sched = staffing.load_schedule(d)
    # Snapshot the posted version so the scheduler can toggle between draft + posted.
    if sched.published and not sched.published_snapshot:
        sched.published_snapshot = staffing.snapshot_of(sched)
    sched.published = False
    staffing.save_schedule(sched)
    return JSONResponse({"ok": True, "day": d.isoformat()})


@app.post("/staffing/past/delete")
async def staffing_past_delete(request: Request):
    form = await request.form()
    day_raw = (form.get("day") or "").strip()
    pw = (form.get("admin_password") or "").strip()
    if pw != ADMIN_PASSWORD:
        return JSONResponse({"ok": False, "error": "Wrong admin password."}, status_code=401)
    try:
        d = date.fromisoformat(day_raw)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad day"}, status_code=400)
    path = staffing.SCHEDULES_DIR / f"{d.isoformat()}.json"
    if path.exists():
        path.unlink()
    return JSONResponse({"ok": True, "day": d.isoformat()})


@app.get("/staffing/past", response_class=HTMLResponse)
def staffing_past(
    request: Request,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    person: str | None = Query(default=None),
    wc: str | None = Query(default=None),
    published: str | None = Query(default=None),
):
    def _parse(s):
        try: return date.fromisoformat(s) if s else None
        except ValueError: return None
    d_from = _parse(from_)
    d_to = _parse(to)
    pub_filter = published if published in ("0", "1") else ""

    rows = []
    all_people: set[str] = set()
    all_wcs: set[str] = set()

    for day, sched in _iter_saved_schedule_files():
        # Collect for filter dropdowns (all days)
        for loc_name, names in sched.assignments.items():
            if loc_name == staffing.TIME_OFF_KEY:
                continue
            all_wcs.add(loc_name)
            for n in names or []:
                all_people.add(n)

        if d_from and day < d_from: continue
        if d_to and day > d_to: continue
        if pub_filter == "1" and not sched.published: continue
        if pub_filter == "0" and sched.published: continue

        # Apply person + wc filters to produce filtered_assignments
        filtered = []
        person_matches = (not person)
        wc_matches = (not wc)
        for loc_name, names in sched.assignments.items():
            if loc_name == staffing.TIME_OFF_KEY: continue
            if wc and loc_name != wc: continue
            if person and person not in (names or []): continue
            filtered.append((loc_name, names or []))
            if person and person in (names or []): person_matches = True
            if wc and loc_name == wc: wc_matches = True

        if person and not person_matches: continue
        if wc and not wc_matches: continue

        people_count = sum(len(ns) for k, ns in sched.assignments.items() if k != staffing.TIME_OFF_KEY)
        wc_count = sum(1 for k, ns in sched.assignments.items() if k != staffing.TIME_OFF_KEY and ns)

        wc_notes_map = sched.wc_notes or {}
        filtered_with_notes = [(name, ppl, wc_notes_map.get(name, "")) for name, ppl in filtered]
        rows.append({
            "day": day.isoformat(),
            "weekday": day.strftime("%A"),
            "published": sched.published,
            "people_count": people_count,
            "wc_count": wc_count,
            "filtered_assignments": filtered_with_notes,
            "notes": sched.notes or "",
            "testing_day": bool(getattr(sched, "testing_day", False)),
        })

    return templates.TemplateResponse(
        request,
        "past_schedules.html",
        {
            "active": "past",
            "rows": rows,
            "all_people": sorted(all_people, key=str.lower),
            "all_wcs": sorted(all_wcs, key=str.lower),
            "filters": {
                "from": from_ or "",
                "to": to or "",
                "person": person or "",
                "wc": wc or "",
                "published": pub_filter,
            },
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = Query(default=0)):
    productive_min = shift_config.productive_minutes_per_day()

    # Active roster (objects, not just names) so we can compute per-WC skill
    # levels and reserve flags for the Default People picker.
    roster = staffing.load_roster()
    active_people_objs = [p for p in roster if p.active]
    active_people = sorted((p.name for p in active_people_objs), key=str.lower)

    # Per-work-center rows.
    wc_rows = []
    for loc in staffing.LOCATIONS:
        eff = work_centers_store.effective(loc)
        max_ops = eff["max_ops"]
        required_skills = eff["required_skills"]
        # Pool for the Default People picker, color-coded by min skill level
        # across the WC's required skills (mirrors the scheduler's logic).
        default_pool: list[dict] = []
        for p in active_people_objs:
            if required_skills:
                lvl = min((p.level(s) for s in required_skills), default=0)
            else:
                lvl = 0
            default_pool.append({"name": p.name, "level": lvl, "reserve": p.reserve})
        default_pool.sort(key=lambda r: (r["reserve"], -r["level"], r["name"].lower()))
        wc_rows.append(
            {
                "key": loc.meter_id or f"name:{loc.name}",
                "name": loc.name,
                "bay": loc.bay,
                "department": loc.department,
                "required_skills": required_skills,
                "min_ops": eff["min_ops"],
                "max_ops": max_ops if max_ops is not None else "",
                "goal": eff["goal_per_day"],
                "note": eff["note"],
                "groups": eff["groups"],
                "value_stream": eff["value_stream"],
                "default_people": eff["default_people"],
                "default_pool": default_pool,
            }
        )

    def _group_summary(kind: str) -> list[dict]:
        rows = []
        for name in work_centers_store.all_group_names(kind):
            members = work_centers_store.members(kind, name)
            auto = work_centers_store.group_goal_auto(kind, name)
            override = work_centers_store.group_goal_override(kind, name)
            rows.append(
                {
                    "name": name,
                    "count": len(members),
                    "auto": auto,
                    "override": "" if override is None else override,
                    "effective": work_centers_store.group_goal(kind, name),
                }
            )
        return rows

    group_rows = _group_summary("group")
    vs_rows = _group_summary("value_stream")
    sched = schedule_store.current()
    schedule_ctx = {
        "shift_start": f"{sched.shift_start.hour:02d}:{sched.shift_start.minute:02d}",
        "shift_end":   f"{sched.shift_end.hour:02d}:{sched.shift_end.minute:02d}",
        "work_weekdays": sorted(sched.work_weekdays),
        "weekday_names": schedule_store.WEEKDAY_NAMES,
        "breaks": [
            {
                "start": f"{b.start.hour:02d}:{b.start.minute:02d}",
                "end":   f"{b.end.hour:02d}:{b.end.minute:02d}",
                "name": b.name,
            }
            for b in sched.breaks
        ],
    }
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "wc_rows": wc_rows,
            "skills_all": list(staffing.SKILLS),
            "value_streams": list(work_centers_store.VALUE_STREAMS),
            "groups_all": work_centers_store.registered_groups(),
            "group_rows": group_rows,
            "vs_rows": vs_rows,
            "active_people": active_people,
            "saved": bool(saved),
            "productive_minutes": productive_min,
            "schedule": schedule_ctx,
        },
    )


def _parse_hhmm(raw: str | None) -> _time | None:
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        hh, mm = raw.split(":")
        return _time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return None


@app.post("/settings/schedule")
async def settings_save_schedule(request: Request):
    form = await request.form()
    current = schedule_store.current()
    shift_s = _parse_hhmm(form.get("shift_start")) or current.shift_start
    shift_e = _parse_hhmm(form.get("shift_end")) or current.shift_end
    if shift_e <= shift_s:
        shift_e = current.shift_end
    weekday_set = set()
    for i in range(7):
        if form.get(f"weekday_{i}"):
            weekday_set.add(i)
    if not weekday_set:
        weekday_set = set(current.work_weekdays)
    # Collect breaks from indexed form fields (start_N, end_N, name_N).
    breaks_new: list[schedule_store.Break] = []
    idx = 0
    while True:
        bs = _parse_hhmm(form.get(f"break_start_{idx}"))
        be = _parse_hhmm(form.get(f"break_end_{idx}"))
        bn = (form.get(f"break_name_{idx}") or "").strip() or "Break"
        if bs is None and be is None and not form.get(f"break_name_{idx}"):
            # No form fields at this index → stop scanning.
            if idx > 50:
                break
            idx += 1
            if idx > 50:
                break
            continue
        if bs and be and be > bs:
            breaks_new.append(schedule_store.Break(bs, be, bn[:40]))
        idx += 1
        if idx > 50:
            break
    breaks_new.sort(key=lambda b: b.start)
    schedule_store.save(schedule_store.Schedule(
        shift_start=shift_s,
        shift_end=shift_e,
        work_weekdays=frozenset(weekday_set),
        breaks=tuple(breaks_new),
    ))
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1#schedule", status_code=303)


def _loc_by_key(key: str):
    for loc in staffing.LOCATIONS:
        if (loc.meter_id or f"name:{loc.name}") == key:
            return loc
    return None


@app.post("/settings/work_centers")
async def settings_save_work_centers(request: Request):
    """Bulk save: group registry edits, WC rows, group/VS overrides."""
    form = await request.form()

    # 1. Group registry (delete, rename, add) — do first so WC save sees updated names.
    for name in list(work_centers_store.registered_groups()):
        if form.get(f"group_delete__{name}"):
            work_centers_store.delete_group(name)
    for name in list(work_centers_store.registered_groups()):
        new_name = (form.get(f"group_rename__{name}") or "").strip()
        if new_name and new_name != name:
            work_centers_store.rename_group(name, new_name)
    new_group = (form.get("group_new") or "").strip()
    if new_group:
        work_centers_store.add_group(new_group)

    # 2. Work-center rows.
    for loc in staffing.LOCATIONS:
        key = loc.meter_id or f"name:{loc.name}"
        prefix = f"wc__{key}__"
        updates: dict = {}
        for field in ("goal_per_day", "min_ops", "max_ops", "note", "value_stream"):
            name = prefix + field
            if name in form:
                updates[field] = form.get(name) or ""
        # Multi-valued: required_skills (checkbox list).
        picked_skills = form.getlist(prefix + "required_skills")
        if picked_skills:
            updates["required_skills"] = picked_skills
        # Single-value Group select (stored internally as a 1-element list in `groups`).
        group_field = prefix + "group"
        if group_field in form:
            v = (form.get(group_field) or "").strip()
            updates["groups"] = [v] if v else []
        # Multi-select Default People checkbox list.
        dp_present = prefix + "default_people_present"
        if dp_present in form:
            updates["default_people"] = form.getlist(prefix + "default_people")
        if updates:
            work_centers_store.save_one(loc, updates)

    # 3. Group + VS overrides.
    for kind in work_centers_store.GROUP_KINDS:
        for name in work_centers_store.all_group_names(kind):
            field = f"group_override__{kind}__{name}"
            if field in form:
                work_centers_store.save_group_override(kind, name, form.get(field) or "")
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@app.post("/settings")
async def settings_save(request: Request):
    """Save the Per-Group overrides. Work-center rows post to /settings/work_center/{key}."""
    form = await request.form()
    group_targets: dict[str, int] = {}
    # Keep the legacy station_targets dict empty; goals are now stored in work_centers.json.
    station_targets: dict[str, int] = {}
    for s in STATIONS:
        raw = (form.get(f"station_{s.meter_id}") or "").strip()
        if raw:
            try:
                station_targets[s.meter_id] = max(0, int(raw))
            except ValueError:
                pass
    for c in CATEGORIES:
        raw = (form.get(f"group_{c}") or "").strip()
        if raw:
            try:
                group_targets[c] = max(0, int(raw))
            except ValueError:
                pass
    settings_store.save(station_targets, group_targets)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/settings?saved=1", status_code=303)


@app.get("/api/layout/{page}")
def get_layout(page: str):
    return JSONResponse({"page": page, "items": layout_store.load(page)})


@app.post("/api/layout/{page}")
async def save_layout(page: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(data, list):
        return JSONResponse({"ok": False, "error": "expected list"}, status_code=400)
    layout_store.save(page, data)
    return JSONResponse({"ok": True, "count": len(data)})


@app.get("/api/widget/{page}/{widget_id}")
def get_widget(page: str, widget_id: str):
    return JSONResponse({"page": page, "id": widget_id, "config": widget_customizer.load_one(page, widget_id)})


@app.post("/api/widget/{page}/{widget_id}")
async def save_widget(page: str, widget_id: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "expected object"}, status_code=400)
    saved = widget_customizer.save_one(page, widget_id, data)
    return JSONResponse({"ok": True, "config": saved})


@app.delete("/api/widget/{page}/{widget_id}")
def reset_widget(page: str, widget_id: str):
    widget_customizer.reset_one(page, widget_id)
    return JSONResponse({"ok": True})


@app.get("/healthz")
def healthz():
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run(
        "zira_dashboard.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
