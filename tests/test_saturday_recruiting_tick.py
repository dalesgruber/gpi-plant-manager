"""The Saturday recruiting closure warmer runs the persisted deadline sweep."""

import asyncio

from zira_dashboard import app as app_module
from zira_dashboard import saturday_recruiting_store as store


def test_tick_closes_due_recruitments(monkeypatch):
    seen = []
    monkeypatch.setattr(store, "close_due", lambda now: seen.append(now) or 1)

    asyncio.run(app_module._tick_saturday_recruiting())

    assert len(seen) == 1
    assert seen[0].tzinfo is not None


def test_saturday_recruiting_tick_is_registered_each_minute():
    entry = next(
        (warmer for warmer in app_module._WARMERS
         if warmer[1] is app_module._tick_saturday_recruiting),
        None,
    )
    assert entry == ("Saturday recruiting", app_module._tick_saturday_recruiting, 60)
