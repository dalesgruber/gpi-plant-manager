"""FastAPI app: station status + leaderboard UI + JSON endpoint.

Routes are organised into feature-specific modules under ``routes/``.
This file just instantiates the FastAPI app, mounts each feature router,
and provides the ``main()`` entry point used by the console script.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from .routes import (
    api_layout,
    dashboard,
    leaderboards,
    past_schedules,
    people,
    settings,
    skills,
    staffing,
    time_off,
    value_streams,
)

app = FastAPI(title="Zira Station Dashboard")

# Mount each feature router. URL paths are owned by the routers themselves.
app.include_router(dashboard.router)
app.include_router(value_streams.router)
app.include_router(staffing.router)
app.include_router(skills.router)
app.include_router(people.router)
app.include_router(leaderboards.router)
app.include_router(past_schedules.router)
app.include_router(time_off.router)
app.include_router(settings.router)
app.include_router(api_layout.router)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "zira_dashboard.app:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
