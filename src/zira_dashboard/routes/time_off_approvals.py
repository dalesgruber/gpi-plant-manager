"""Time-off approvals page.

The route renders from local mirrors only: pending leave requests, cached
balances, department-scoped coverage, and recent in-app decisions.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import time_off_audit, time_off_context
from ..deps import templates
from ..plant_day import today as plant_today

router = APIRouter()


def _pending_rows(today: date) -> list[dict[str, Any]]:
    """Every pending request in the mirror, ordered by start date."""
    from .. import db

    return db.query(
        "SELECT r.id, r.person_odoo_id, r.holiday_status_id, r.shape, "
        "r.date_from, r.date_to, r.hour_from, r.hour_to, r.state, "
        "COALESCE(p.name, '#' || r.person_odoo_id::text) AS person_name, "
        "COALESCE(lt.name, 'Time off') AS leave_type "
        "FROM time_off_requests r "
        "LEFT JOIN people p ON p.odoo_id = r.person_odoo_id "
        "LEFT JOIN leave_types_cache lt ON lt.holiday_status_id = r.holiday_status_id "
        "WHERE r.state IN ('draft','draft_edit','confirm','validate1') "
        "ORDER BY r.date_from, lower(COALESCE(p.name, '#' || r.person_odoo_id::text))",
        (),
    )


def _pending_payload(today: date) -> list[dict[str, Any]]:
    """Attach balance, coverage, and risk flags to each pending row."""
    rows = []
    for row in _pending_rows(today):
        balance = time_off_context.balance_for(
            row["person_odoo_id"], row["holiday_status_id"]
        )
        coverage = time_off_context.coverage_for(
            row["person_odoo_id"], row["date_from"], row["date_to"]
        )
        amount, unit = time_off_context.request_amount(row)
        over_balance = bool(
            balance
            and balance.get("unit") == unit
            and amount > float(balance.get("remaining") or 0)
        )
        rows.append({
            **row,
            "balance": balance,
            "coverage": coverage,
            "request_amount": amount,
            "request_unit": unit,
            "over_balance": over_balance,
            "past_due": row["date_to"] < today,
            "awaiting_second": row["state"] == "validate1",
        })
    return rows


@router.get("/staffing/time-off/approvals", response_class=HTMLResponse)
def time_off_approvals(request: Request):
    today = plant_today()
    return templates.TemplateResponse(
        request,
        "time_off_approvals.html",
        {
            "active": "time_off_approvals",
            "today_iso": today.isoformat(),
            "pending": _pending_payload(today),
            "recent": time_off_audit.recent_decisions(days=30),
        },
    )
