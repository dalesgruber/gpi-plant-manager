from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import production_history, production_metrics, shift_config, staffing
from ..deps import templates
from ..plant_day import today as plant_today

router = APIRouter()


def _wc_role_by_name() -> dict[str, str]:
    return {
        loc.name: loc.skill
        for loc in staffing.LOCATIONS
        if loc.skill in ("Repair", "Dismantler")
    }


def _leaderboard_payload(today: date) -> dict:
    records = production_history.normalized_daily_records(
        date(today.year - 1, 1, 1),
        today,
    )
    return production_metrics.build_recycling_leaderboard(
        records,
        today=today,
        standard_full_day_hours=shift_config.productive_minutes_per_day() / 60.0,
        wc_role_by_name=_wc_role_by_name(),
    )


def _render_recycling_leaderboard(
    request: Request,
    *,
    tv_mode: bool,
    tv_theme: str = "dark",
) -> HTMLResponse:
    today = plant_today()
    data = _leaderboard_payload(today)
    context = {
        "tv_mode": tv_mode,
        "tv_theme": tv_theme if tv_theme in ("light", "dark") else "dark",
        "data": data,
    }
    if not tv_mode:
        context["active_dashboard_key"] = "vs_recycling_leaderboard"
    return templates.TemplateResponse(request, "recycling_leaderboard_tv.html", context)


def render_recycling_leaderboard_tv(
    request: Request,
    *,
    tv_theme: str = "dark",
) -> HTMLResponse:
    return _render_recycling_leaderboard(request, tv_mode=True, tv_theme=tv_theme)


@router.get("/recycling-leaderboard", response_class=HTMLResponse)
def recycling_leaderboard(request: Request):
    return _render_recycling_leaderboard(request, tv_mode=False)


@router.get("/tv/recycling-leaderboard", response_class=HTMLResponse)
def tv_recycling_leaderboard(request: Request, theme: str | None = Query(default=None)):
    from .. import tv_displays_store

    try:
        row = tv_displays_store.by_slug("recycling-leaderboard")
    except Exception:  # noqa: BLE001 - keep the TV route usable during DB outages/local tests
        row = None
    stored_theme = row["theme"] if row is not None else "dark"
    tv_theme = "light" if theme == "light" else ("dark" if theme == "dark" else stored_theme)
    return render_recycling_leaderboard_tv(request, tv_theme=tv_theme)
