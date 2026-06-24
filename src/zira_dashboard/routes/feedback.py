"""User feedback submission and read-only admin list."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .. import feedback_store
from ..deps import templates

router = APIRouter()


class FeedbackIn(BaseModel):
    message: str
    category: str | None = None
    page_url: str | None = None


def _optional_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _safe_page_url(value: str | None) -> str | None:
    value = _optional_text(value)
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        return value
    if not parsed.scheme and value.startswith("/") and not value.startswith("//"):
        return value
    return None


@router.post("/feedback")
def submit_feedback(payload: FeedbackIn, request: Request) -> JSONResponse:
    message = (payload.message or "").strip()
    if not message:
        return JSONResponse(
            {"ok": False, "error": "Message is required."},
            status_code=400,
        )
    submitter = getattr(request.state, "user_upn", None)
    new_id = feedback_store.insert(
        message=message,
        submitter=submitter,
        page_url=_safe_page_url(payload.page_url),
        category=_optional_text(payload.category),
    )
    return JSONResponse({"ok": True, "id": new_id})


@router.get("/admin/feedback", response_class=HTMLResponse)
def admin_feedback(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin_feedback.html",
        {"items": feedback_store.recent(), "active": "admin"},
    )
