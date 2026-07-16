"""Manager API for optional Saturday-work recruiting."""

from __future__ import annotations

import logging
from datetime import date, time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import (
    saturday_recruiting as sr,
    saturday_recruiting_store as store,
    schedule_store,
    shift_config,
    staffing,
)
from ..plant_day import now as plant_now
from . import staffing as staffing_routes


log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/staffing/saturday-recruiting")


def _actor(request: Request) -> str | None:
    return getattr(request.state, "user_upn", None)


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception as exc:  # JSON decoder types vary by Starlette version.
        raise HTTPException(status_code=422, detail="Request body must be valid JSON") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")
    return body


def _values(body: dict) -> tuple[date, time, time, dict[int, int]]:
    try:
        day = date.fromisoformat(str(body["day"]))
        shift_start = time.fromisoformat(str(body["shift_start"]))
        shift_end = time.fromisoformat(str(body["shift_end"]))
        raw_counts = body["requested_counts"]
        if not isinstance(raw_counts, dict):
            raise TypeError("requested_counts")
        counts = {int(key): int(value) for key, value in raw_counts.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid Saturday recruiting request") from exc
    return day, shift_start, shift_end, counts


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/activate")
async def activate(request: Request):
    body = await _body(request)
    day, shift_start, shift_end, counts = _values(body)
    if day.weekday() != 5:
        raise HTTPException(status_code=422, detail="Saturday recruiting requires a Saturday")
    try:
        deadline = sr.response_deadline(
            day,
            schedule_store.current().work_weekdays,
            shift_config.configured_shift_start_for,
        )
        bundle = store.activate(
            day=day,
            shift_start=shift_start,
            shift_end=shift_end,
            response_deadline=deadline,
            requested_counts=counts,
            actor=_actor(request),
            now=plant_now(),
        )
    except store.SaturdayRecruitingError as exc:
        raise _conflict(exc) from exc
    except HTTPException:
        raise
    except Exception:
        log.exception("Could not activate Saturday recruiting for %s", day)
        raise HTTPException(status_code=500, detail="Could not update Saturday recruiting") from None
    staffing_routes._bust_after_mutation()
    return JSONResponse({"ok": True, "recruitment": store.serialize_bundle(bundle)})


@router.post("/openings")
async def openings(request: Request):
    body = await _body(request)
    day, shift_start, shift_end, counts = _values(body)
    if day.weekday() != 5:
        raise HTTPException(status_code=422, detail="Saturday recruiting requires a Saturday")
    try:
        bundle = store.update_openings(
            day=day,
            shift_start=shift_start,
            shift_end=shift_end,
            requested_counts=counts,
            actor=_actor(request),
            now=plant_now(),
        )
    except store.SaturdayRecruitingError as exc:
        raise _conflict(exc) from exc
    except Exception:
        log.exception("Could not update Saturday recruiting openings for %s", day)
        raise HTTPException(status_code=500, detail="Could not update Saturday recruiting") from None
    staffing_routes._bust_after_mutation()
    return JSONResponse({"ok": True, "recruitment": store.serialize_bundle(bundle)})


@router.post("/commitments/{person_id}/cancel")
async def cancel_commitment(person_id: int, request: Request):
    body = await _body(request)
    try:
        day = date.fromisoformat(str(body["day"]))
        reason = str(body["reason"])
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="A Saturday date and cancellation reason are required") from exc
    if not reason.strip():
        raise HTTPException(status_code=422, detail="A cancellation reason is required")
    try:
        result = store.cancel_by_manager(day, person_id, _actor(request), reason, plant_now())
    except store.SaturdayRecruitingError as exc:
        raise _conflict(exc) from exc
    except Exception:
        log.exception("Could not cancel Saturday commitment %s for %s", person_id, day)
        raise HTTPException(status_code=500, detail="Could not update Saturday recruiting") from None
    staffing_routes._bust_after_mutation()
    return JSONResponse({"ok": True, "recruitment": store.serialize_bundle(result.bundle)})


@router.post("/cancel")
async def cancel(request: Request):
    body = await _body(request)
    try:
        day = date.fromisoformat(str(body["day"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="A Saturday date is required") from exc
    if day.weekday() != 5:
        raise HTTPException(status_code=422, detail="Saturday recruiting requires a Saturday")
    try:
        targets = store.cancel_recruitment(day, _actor(request), plant_now())
    except store.SaturdayRecruitingError as exc:
        raise _conflict(exc) from exc
    except Exception:
        log.exception("Could not cancel Saturday recruiting for %s", day)
        raise HTTPException(status_code=500, detail="Could not update Saturday recruiting") from None
    staffing.invalidate_schedule_cache(day)
    staffing_routes._bust_after_mutation()
    return JSONResponse({
        "ok": True,
        "committed_people": [
            {"person_id": item.person_id, "person_name": item.person_name}
            for item in targets
        ],
    })
