"""POST /staffing/share-to-slack — render the day's scheduler in
print mode, convert to PDF, upload to the configured Slack channel.
"""

from __future__ import annotations

import os
from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from .. import slack_client
from .staffing import staffing_page

router = APIRouter()


def _format_comment(day: str) -> str:
    """Return e.g. 'Schedule for Tue 4/30' for the given YYYY-MM-DD."""
    try:
        d = date.fromisoformat(day)
    except ValueError:
        return f"Schedule for {day}"
    weekday = d.strftime("%a")  # 'Mon', 'Tue', ...
    return f"Schedule for {weekday} {d.month}/{d.day}"


def _render_pdf(html: str, base_url: str) -> bytes:
    """Render HTML to PDF bytes via WeasyPrint.

    `base_url` lets WeasyPrint resolve any relative asset URLs in the
    HTML (stylesheets, fonts) against the running server.
    """
    from weasyprint import HTML  # imported lazily — heavy dep
    return HTML(string=html, base_url=base_url).write_pdf()


@router.post("/staffing/share-to-slack")
def share_to_slack(
    request: Request,
    day: str = Query(...),
):
    """Render the day's scheduler -> PDF -> upload to Slack."""
    channel_id = os.environ.get("SLACK_CHANNEL_ID")
    if not channel_id:
        return JSONResponse(
            {"ok": False, "error": "Slack not configured (SLACK_CHANNEL_ID missing)"},
            status_code=500,
        )

    # 1. Render the scheduler page for this day by calling the existing
    #    handler as a regular function. The handler returns an
    #    HTMLResponse; we read its body for the HTML string.
    response = staffing_page(request, day=day)
    html = response.body.decode("utf-8")

    # 2. Render the HTML to PDF.
    try:
        pdf_bytes = _render_pdf(html, base_url=str(request.base_url))
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"PDF render failed: {e}"},
            status_code=500,
        )

    # 3. Upload to Slack.
    try:
        result = slack_client.upload_pdf(
            pdf_bytes,
            filename=f"schedule-{day}.pdf",
            channel_id=channel_id,
            initial_comment=_format_comment(day),
        )
    except slack_client.SlackError as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=502,
        )

    return JSONResponse({
        "ok": True,
        "channel_name": result["channel_name"],
        "permalink": result["permalink"],
    })
