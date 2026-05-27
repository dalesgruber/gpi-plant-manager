"""Kiosk time-off routes — gated by the same HMAC token as `routes/kiosk.py`.

Surfaces a touch-friendly time-off flow on the kiosk: landing with three
big-touch actions (Request Time Off / My Requests / Who's Out), the
request wizard, the mine list/detail, and the calendar. This module
currently only owns the landing route; the request wizard, mine, and
calendar pages get appended by subsequent tasks in the plan.

Auth is identical to `routes/kiosk.py`: every URL takes a 60s HMAC token
in the path, and an invalid/expired token bounces back to `/kiosk` so a
shared device never leaks one user's data to the next. The helpers
``_mint_token`` / ``_verify_token`` / ``_person_by_id`` live in
`routes/kiosk.py` and are reused here verbatim — duplicating them would
risk drift in the auth boundary.

The landing route also surfaces a warning banner if any of this person's
recent submissions are stuck in the sync queue (synced_to_odoo=FALSE AND
sync_error IS NOT NULL), mirroring the same UX pattern used on the kiosk
dashboard for stuck punches — so an employee whose request hasn't made
it to Odoo isn't left wondering why HR hasn't seen it.

Routes:
  GET /kiosk/time-off/{token}                              Landing with 3 buttons
  GET /kiosk/time-off/request/{token}                      Wizard step 1 — shape picker
  GET /kiosk/time-off/request/{token}/details?shape=…      Wizard step 2 — details form
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import db, odoo_client, settings_store, time_off_balances
from ..deps import templates
from .kiosk import _mint_token, _person_by_id, _verify_token

router = APIRouter()


def _pending_count(person_odoo_id: int) -> int:
    """Count of this person's requests still in-flight (not yet validated
    or refused/cancelled). Matches `_pending_time_off_count` in
    `routes/kiosk.py` — kept local because the badge math may diverge
    once the wizard exists (e.g. distinguishing "draft I haven't
    submitted" from "pending HR approval")."""
    rows = db.query(
        "SELECT COUNT(*) AS n FROM time_off_requests "
        "WHERE person_odoo_id = %s "
        "AND state IN ('draft', 'confirm', 'validate1')",
        (person_odoo_id,),
    )
    return rows[0]["n"] if rows else 0


def _all_count(person_odoo_id: int) -> int:
    """Total count of this person's requests, used as the badge on the
    My Requests action so the user sees there is history to look at even
    after everything has been approved or refused."""
    rows = db.query(
        "SELECT COUNT(*) AS n FROM time_off_requests "
        "WHERE person_odoo_id = %s",
        (person_odoo_id,),
    )
    return rows[0]["n"] if rows else 0


def _sync_error_warning(person_odoo_id: int) -> dict | None:
    """Return a warning summary if this person has requests that tried
    to sync to Odoo and failed (synced_to_odoo=FALSE AND sync_error IS
    NOT NULL). Returns None if everything synced cleanly.

    Mirrors `_sync_error_warning` in `routes/kiosk.py` — same shape so
    the template renders both with the same `k-warning` styling."""
    rows = db.query(
        "SELECT COUNT(*) AS n, MAX(sync_error) AS latest "
        "FROM time_off_requests WHERE person_odoo_id = %s "
        "AND synced_to_odoo = FALSE AND sync_error IS NOT NULL",
        (person_odoo_id,),
    )
    if not rows or not rows[0]["n"]:
        return None
    return {"count": rows[0]["n"], "latest_error": rows[0]["latest"]}


@router.get("/kiosk/time-off/{token}", response_class=HTMLResponse)
def time_off_landing(request: Request, token: str):
    """Landing page with three big-touch actions: Request Time Off,
    My Requests, Who's Out. Same HMAC gate as the rest of /kiosk — an
    invalid or expired token bounces to /kiosk so a stale URL on a shared
    device never lets the next user act as the previous one.

    Mints a fresh token before render so a user reading the screen (or
    pausing to think) doesn't time out mid-tap. The counts come from the
    local `time_off_requests` mirror so this is a few millisecond
    Postgres SELECTs, no Odoo XML-RPC on the hot path.
    """
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p:
        return RedirectResponse(url="/kiosk", status_code=303)
    fresh = _mint_token(person_id)
    # If a person has no Odoo id mapped, fall back to a sentinel that
    # matches nothing in time_off_requests rather than returning early —
    # the page still renders with zero counts and a generic landing.
    odoo_id = p.get("odoo_id") or -1
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_landing.html",
        {
            "person": p,
            "token": fresh,
            "pending_count": _pending_count(odoo_id),
            "all_count": _all_count(odoo_id),
            "sync_warning": _sync_error_warning(odoo_id),
        },
    )


@router.get("/kiosk/time-off/request/{token}", response_class=HTMLResponse)
def request_shape(request: Request, token: str):
    """Wizard step 1 — four big-touch cards that each link to step 2
    with a `shape=` query param. Same HMAC gate as the landing; invalid
    token bounces to /kiosk.

    Mints a fresh token before render so the user has the full TTL to
    pick a shape; the next page picks up that token and mints again.
    """
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p:
        return RedirectResponse(url="/kiosk", status_code=303)
    fresh = _mint_token(person_id)
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_request_shape.html",
        {"person": p, "token": fresh},
    )


# ----- Wizard step 2 — details form (Task 17) -----

_VALID_SHAPES = {"full_day", "late_arrival", "early_leave", "midday_gap"}


def _fetch_visible_leave_types(shape: str) -> list[dict]:
    """All hr.leave.type from local cache minus hidden ones, filtered to
    the unit matching the shape.

    The shape picker decides which time-off shape the user wants
    (full day vs partial day); the leave-type unit must agree — day-unit
    types only fit a full-day shape, hour-unit types only fit the three
    partial-day shapes. Half-day-unit types behave like day-unit for our
    purposes (kept in the full-day bucket).

    Reads from `leave_types_cache`, which is populated by the 10-min
    poller (Task 10/23). Empty until the poller has run at least once —
    in that case this returns an empty list and the template will render
    an empty `<select>`, which is correct: the user can't pick a type if
    none have synced from Odoo yet.
    """
    hidden = set(settings_store.get_hidden_leave_type_ids())
    rows = db.query(
        "SELECT holiday_status_id, name, request_unit, requires_allocation "
        "FROM leave_types_cache WHERE active = TRUE "
        "ORDER BY name"
    )
    out: list[dict] = []
    for r in rows:
        if r["holiday_status_id"] in hidden:
            continue
        if shape == "full_day":
            if r["request_unit"] not in ("day", "half_day"):
                continue
        else:
            if r["request_unit"] != "hour":
                continue
        out.append({
            "id": r["holiday_status_id"],
            "name": r["name"],
            "request_unit": r["request_unit"],
            "requires_allocation": r["requires_allocation"],
        })
    return out


def _refresh_and_load_balances(person_odoo_id: int) -> list[dict]:
    """Synchronous balance refresh before render (~200-500ms blocking).

    The wizard needs a fresh balance number to show in the panel and feed
    into the live-calc — a stale number would mean the JS would happily
    let the user submit something they no longer have allocation for.
    `time_off_balances.refresh_for_employee` already swallows Odoo errors
    so the worst case is a render with the previous cached balance, which
    is still better than crashing the request."""
    try:
        time_off_balances.refresh_for_employee(person_odoo_id)
    except Exception:  # noqa: BLE001 — never let a refresh error block the wizard
        pass
    return time_off_balances.get_for_employee(person_odoo_id)


def _shift_window_for(person_odoo_id: int) -> tuple[float, float]:
    """Return (hour_from, hour_to) for the employee's shift.

    Tries Odoo `resource.calendar` first (a per-employee shift); falls
    back to the company-wide default in `app_settings`
    (`time_off.default_shift_hours`). The three partial-day shapes
    (late_arrival, early_leave, midday_gap) need these bounds to
    validate the user's chosen time(s) and to drive the live-calc
    request-size math in the JS."""
    try:
        cal = odoo_client.fetch_resource_calendar(person_odoo_id)
    except Exception:  # noqa: BLE001 — fall back to default rather than crash
        cal = None
    if (cal
            and cal.get("hour_from") is not None
            and cal.get("hour_to") is not None):
        return (float(cal["hour_from"]), float(cal["hour_to"]))
    return settings_store.get_default_shift_hours()


@router.get("/kiosk/time-off/request/{token}/details",
            response_class=HTMLResponse)
def request_details(request: Request, token: str, shape: str = "full_day"):
    """Wizard step 2 — the details form.

    Branches by ``shape`` to ask for the right inputs:
      - full_day → start date + end date
      - late_arrival → date + arrival time (shift_from..shift_to)
      - early_leave → date + leave time (shift_from..shift_to)
      - midday_gap → date + leave/return times within shift

    Shows a balance panel that the client-side JS keeps up to date as
    the user changes inputs; the JS lives in `static/kiosk_time_off.js`.
    Bad token → /kiosk; bad shape → back to the shape picker (never
    render the form with an invalid shape value)."""
    person_id = _verify_token(token)
    if person_id is None:
        return RedirectResponse(url="/kiosk", status_code=303)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/kiosk", status_code=303)
    if shape not in _VALID_SHAPES:
        return RedirectResponse(
            url=f"/kiosk/time-off/request/{_mint_token(person_id)}",
            status_code=303,
        )
    from datetime import date as _date
    fresh = _mint_token(person_id)
    types = _fetch_visible_leave_types(shape)
    balances = _refresh_and_load_balances(p["odoo_id"])
    # Cast numeric Decimals to floats so the JSON-embedded JS payload
    # in the template gets plain numbers instead of "Decimal('15.00')"
    # repr (Jinja's `{{ x }}` would print the Decimal verbatim).
    balances_by_type = {
        b["holiday_status_id"]: {
            "unit": b["unit"],
            "available": float(b["available"]),
            "available_practical": float(b["available_practical"]),
            "pending": float(b["pending"]),
        }
        for b in balances
    }
    shift_from, shift_to = _shift_window_for(p["odoo_id"])
    return templates.TemplateResponse(
        request,
        "kiosk_time_off_request_details.html",
        {
            "person": p,
            "token": fresh,
            "shape": shape,
            "leave_types": types,
            "balances_by_type": balances_by_type,
            "shift_from": shift_from,
            "shift_to": shift_to,
            "today_iso": _date.today().isoformat(),
        },
    )
