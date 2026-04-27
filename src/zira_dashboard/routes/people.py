"""Per-person directory + player card routes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import staffing
from ..deps import client, templates

router = APIRouter()


@router.get("/staffing/people", response_class=HTMLResponse)
def staffing_people(request: Request):
    roster = staffing.load_roster()
    active_people = sorted([p for p in roster if p.active], key=lambda p: p.name.lower())
    return templates.TemplateResponse(
        request,
        "people_index.html",
        {"active": "people", "people": active_people},
    )


@router.get("/staffing/people/{name}", response_class=HTMLResponse)
def staffing_player_card(
    request: Request,
    name: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
):
    from .. import production_history
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
