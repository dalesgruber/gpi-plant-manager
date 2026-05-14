"""Operator dashboard routes.

  /wc/{slug}        editor view (gridstack enabled, WC picker visible)
  /tv/wc/{slug}     TV view (chrome stripped, picker hidden)
  /operator         redirect to the first WC's /wc/{slug}

The /wc/{slug} dashboard mirrors /recycling's visual style — same CSS
classes, same widget markup — scoped to a single WC. A picker at the
top lets the user switch which WC.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import layout_store, wc_dashboard_data, work_centers_store
from ..deps import templates

router = APIRouter()


def _render_wc_dashboard(
    request: Request,
    *,
    slug: str,
    tv_mode: bool,
    tv_theme: str,
):
    """Render the Operator dashboard for one WC.

    Layout mirrors /recycling's widget set, scoped to a single WC:
      - KPI tiles row
      - Pallets banner
      - 15-min progress chart
      - Cumulative daily progress
      - Downtime stacked bar
      - GOAT race (group)
      - Monthly Ribbons (group)
    """
    from .. import staffing
    loc = wc_dashboard_data.wc_by_slug(slug)
    if loc is None:
        return JSONResponse({"error": f"no work center matches slug {slug!r}"}, status_code=404)

    today = datetime.now(timezone.utc).date()
    wc_name = loc.name
    operators = wc_dashboard_data.assigned_operators_for_wc(wc_name, today)
    operators_display = " · ".join(operators)
    groups = work_centers_store.groups(loc) or []
    wc_group = groups[0] if groups else None

    pallets = wc_dashboard_data.pallets_banner(wc_name, today)
    progress = wc_dashboard_data.fifteen_min_progress_buckets(wc_name, today)
    kpi = wc_dashboard_data.kpi_tiles(wc_name, today)
    report = wc_dashboard_data.downtime_report(wc_name, today) or {}
    # Single-row stacked working/down for this WC (mirrors /recycling shape).
    down_min = int(report.get("total_minutes", 0))
    elapsed_min = int(kpi["hours_elapsed"] * 60)
    working_min = max(0, elapsed_min - down_min)
    denom = elapsed_min if elapsed_min else 1
    downtime_row = {
        "name": wc_name,
        "working": working_min,
        "down": down_min,
        "working_pct": working_min / denom * 100.0,
        "down_pct": down_min / denom * 100.0,
    }
    goat = wc_dashboard_data.goat_race(wc_name, today) if wc_group else None
    ribbons = wc_dashboard_data.monthly_ribbons(wc_name, today.year, today.month) if wc_group else None

    layout_key = f"wc:{slug}"

    return templates.TemplateResponse(
        request,
        "wc_dashboard.html",
        {
            "slug": slug,
            "wc_name": wc_name,
            "wc_group": wc_group,
            "operators": operators,
            "operators_display": operators_display,
            "today": today.isoformat(),
            "year": today.year,
            "month": today.month,
            "wc_options": [
                {"name": l.name, "slug": wc_dashboard_data.slug_for_wc(l.name)}
                for l in staffing.LOCATIONS
            ],
            "pallets": pallets,
            "progress_buckets": progress["buckets"],
            "progress_bucket_target": progress["bucket_target"],
            "kpi": kpi,
            "downtime_row": downtime_row,
            "downtime_elapsed_minutes": elapsed_min,
            "goat_race": goat,
            "ribbons": ribbons,
            "active_dashboard_key": "wc:" + wc_name,
            "layout": layout_store.layout_map(layout_key),
            "layout_key": layout_key,
            "tv_mode": tv_mode,
            "tv_theme": tv_theme,
        },
    )


@router.get("/wc/{slug}", response_class=HTMLResponse)
def wc_dashboard(request: Request, slug: str):
    return _render_wc_dashboard(request, slug=slug, tv_mode=False, tv_theme="dark")


@router.get("/tv/wc/{slug}", response_class=HTMLResponse)
def tv_wc_dashboard(
    request: Request,
    slug: str,
    theme: str | None = Query(default=None),
):
    tv_theme = "light" if theme == "light" else "dark"
    return _render_wc_dashboard(request, slug=slug, tv_mode=True, tv_theme=tv_theme)


@router.get("/operator")
def operator_default():
    """Entry point for the Operator dashboard sub-tab.

    Redirects to the first work center's /wc/{slug} URL. Order is
    staffing.LOCATIONS order — usually alphabetical by name.
    """
    from .. import staffing
    if not staffing.LOCATIONS:
        return JSONResponse(
            {"error": "no work centers configured — set them up in Settings"},
            status_code=404,
        )
    first = staffing.LOCATIONS[0]
    return RedirectResponse(url=f"/wc/{wc_dashboard_data.slug_for_wc(first.name)}", status_code=302)
