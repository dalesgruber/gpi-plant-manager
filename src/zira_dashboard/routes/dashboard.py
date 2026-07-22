"""Top-level dashboard routes: GET / (redirect), /tv/ping, /api/leaderboard.

GET / redirects to /recycling so the home page lands on the Recycling
department dashboard. (The old Work Centers page at /work-centers was
folded 2026-07-22 after 30 days at 4 views/month — its per-WC states show
on the Recycling dashboard; the URL 301s there. /api/leaderboard still
serves the raw per-station day numbers.)
"""

from __future__ import annotations

from datetime import datetime, UTC

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

from ..deps import (
    _filter_stations,
    _parse_day,
    _state,
    client,
)
from ..leaderboard import cached_leaderboard as leaderboard
from ..plant_day import today as plant_today

router = APIRouter()


@router.get("/")
def home():
    return RedirectResponse(url="/recycling", status_code=307)


@router.get("/tv/ping")
def tv_ping():
    """Tiny liveness probe for tv-refresh.js.

    The plant-floor TVs probe before reloading so a backend blip never
    paints an error page over live production numbers. Probing the full
    dashboard URL downloaded the whole page just to throw it away (then
    again on reload), so the TVs probe this empty 204 instead.

    This endpoint is anonymous along with every ``/tv/*`` route, so
    unattended screens can probe the backend before refreshing the dashboard.
    """
    return Response(status_code=204)


@router.get("/work-centers", include_in_schema=False)
def work_centers_redirect():
    """The Work Centers page folded into the Recycling dashboard
    (2026-07-22); the URL 301s so old bookmarks keep working."""
    return RedirectResponse(url="/recycling", status_code=301)


@router.get("/api/leaderboard")
def api_leaderboard(
    day: str | None = Query(default=None),
    category: str | None = Query(default=None),
):
    d = _parse_day(day)
    today = plant_today()
    is_today = d == today
    stations = _filter_stations(category)
    now = datetime.now(UTC)
    results = leaderboard(client, stations, d, now_utc=now if is_today else None)
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
