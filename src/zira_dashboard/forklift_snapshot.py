"""Orchestrate one day's forklift snapshot: fetch -> ingest -> store.

Called by the background warmer (and usable from a backfill script). The
`client` arg is accepted for symmetry with precompute_day but unused — the
forklift_client functions read config from env per-call.
"""
from __future__ import annotations

from datetime import date

from . import app_settings, forklift_client, forklift_ingest, forklift_store


def snapshot_today(client, day: date) -> dict:
    dashboard = forklift_client.fetch_dashboard()
    history = forklift_client.fetch_queue_history()
    drivers = forklift_client.fetch_drivers()

    calls_row = forklift_ingest.build_calls_daily(day, dashboard, history)
    driver_rows = forklift_ingest.build_driver_daily(day, dashboard)

    forklift_store.upsert_calls_daily(calls_row)
    n = forklift_store.upsert_driver_daily(driver_rows)

    backups = [d.get("name") for d in (drivers or [])
               if d.get("isOverloadResponder") and d.get("name")]
    app_settings.set_setting("forklift_overload_responders", backups)

    return {"day": day.isoformat(), "calls": calls_row["total_calls"], "drivers": n}
