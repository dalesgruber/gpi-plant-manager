"""Per-skill production leaderboards: GET /staffing/leaderboards."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import settings_store, staffing
from ..deps import _window_dates, client, templates
from ..stations import Station

router = APIRouter()


@router.get("/staffing/leaderboards", response_class=HTMLResponse)
def staffing_leaderboards(
    request: Request,
    window: str = Query(default="week"),
    metric: str = Query(default="pct"),
):
    from .. import production_history
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
            min_days=1,  # TEMP: lowered from 3 for early testing — restore to 3 once there's enough history
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
