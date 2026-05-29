"""Kiosk time displays must render in company time (America/Chicago),
not the server's local timezone.

Regression for: kiosk times showing 5 hours ahead because the display
helpers used `dt.astimezone()` (no arg → server local tz, which is UTC on
the Railway container) instead of `dt.astimezone(SITE_TZ)`.

The fixture forces the process-local tz to UTC so the buggy no-arg path
would render UTC, making it distinguishable from the Central-time fix
regardless of where the test runs.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import pytest


@pytest.fixture
def utc_system_tz():
    """Pin the process's local timezone to UTC for the duration of a test."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() unavailable on this platform")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def test_fmt_time_renders_central_during_dst(utc_system_tz):
    from zira_dashboard.routes.kiosk import _fmt_time

    # Noon UTC on 2026-05-29 = 7:00 AM Central (CDT, UTC-5).
    dt = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
    assert _fmt_time(dt) == "7:00 AM"


def test_fmt_time_renders_central_during_standard_time(utc_system_tz):
    from zira_dashboard.routes.kiosk import _fmt_time

    # Noon UTC on 2026-01-15 = 6:00 AM Central (CST, UTC-6).
    # Proves the fix uses a DST-aware zone, not a fixed -5 offset.
    dt = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    assert _fmt_time(dt) == "6:00 AM"


def test_fmt_short_dt_renders_central(utc_system_tz):
    from zira_dashboard.routes.kiosk import _fmt_short_dt

    dt = datetime(2026, 5, 29, 12, 0, tzinfo=timezone.utc)
    assert _fmt_short_dt(dt) == "5/29 7:00 AM"
