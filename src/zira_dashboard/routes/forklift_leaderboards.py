"""Dedicated forklift-driver leaderboard: GET /staffing/forklift.

Four ranked cards (overall GOAT score, most calls, on-time %, fastest
response) over the same window presets the production leaderboards use.
Defensive: any forklift store / score-config failure degrades to empty
cards rather than 500ing the page (mirrors forklift_advisor's posture).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import forklift_awards, forklift_settings
from ..deps import resolve_range, templates
from ..plant_day import today as plant_today

_log = logging.getLogger(__name__)

router = APIRouter()

FORKLIFT_MIN_CALLS = forklift_awards.DEFAULT_MIN_CALLS

_WINDOW_LABELS = {
    "today": "Today", "week": "Week", "month": "Month",
    "quarter": "Quarter", "year": "Year", "alltime": "All Time",
}


@router.get("/staffing/forklift", response_class=HTMLResponse)
def forklift_leaderboards(
    request: Request,
    window: str = Query(default="alltime"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
):
    today_d = plant_today()
    start_d, end_d, custom_range_active = resolve_range(window, start, end, today_d)

    # Resolve the score config + compute the four lists best-effort. score_config
    # only reads the score-override fields, so algo_throughput is a don't-care.
    try:
        cfg = forklift_settings.resolve(
            forklift_settings.current(), algo_throughput=0.0
        ).score_config()
        lb = forklift_awards.leaderboard(
            start_d, end_d, cfg, min_calls=FORKLIFT_MIN_CALLS
        )
    except Exception as exc:  # noqa: BLE001 - never 500 the page on a data hiccup
        _log.warning("forklift leaderboard: render context failed: %s", exc)
        lb = forklift_awards.empty_leaderboard()

    label = "Custom" if custom_range_active else _WINDOW_LABELS.get(window, window)
    return templates.TemplateResponse(
        request,
        "forklift_leaderboards.html",
        {
            "active": "forklift_leaderboards",
            "window": window,
            "window_label": label,
            "custom_range_active": custom_range_active,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "lb": lb,
            "min_calls": FORKLIFT_MIN_CALLS,
        },
    )
