"""FastAPI app: station status + leaderboard UI + JSON endpoint.

Routes are organised into feature-specific modules under ``routes/``.
This file just instantiates the FastAPI app, mounts each feature router,
and provides the ``main()`` entry point used by the console script.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise the Postgres pool and bootstrap schema on startup.

    If ``DATABASE_URL`` is missing or the pool can't be created,
    ``db.init_pool()`` raises with a clear message — fail fast rather than
    silently fall back to JSON storage.
    """
    db.init_pool()
    db.bootstrap_schema()
    try:
        yield
    finally:
        db.shutdown_pool()


app = FastAPI(title="Zira Station Dashboard", lifespan=lifespan)

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
