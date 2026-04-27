"""Time-off calendar route: GET /staffing/time-off."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import staffing
from ..deps import _iter_saved_schedule_files, templates

router = APIRouter()


def _time_off_by_day() -> dict[date, list[str]]:
    """Flatten all saved schedules → {date: [people off]}."""
    out: dict[date, list[str]] = {}
    for day, sched in _iter_saved_schedule_files():
        names = sched.assignments.get(staffing.TIME_OFF_KEY, []) or []
        if names:
            out[day] = list(names)
    return out


@router.get("/staffing/time-off", response_class=HTMLResponse)
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
