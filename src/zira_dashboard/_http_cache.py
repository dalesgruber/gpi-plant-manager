"""HTTP Cache-Control helpers for dashboard GET handlers.

Conventions:
- Past-day pages: long cache. Data is immutable.
- Today-or-future pages: short cache. Data changes throughout the shift.
- All caches are `private` (don't cache at shared proxies — content
  may include user-specific widget customizations down the line).

Usage from a handler:
    from .._http_cache import set_cache_headers
    response = templates.TemplateResponse(...)
    set_cache_headers(response, includes_today=is_today)
    return response
"""

from __future__ import annotations

from datetime import date

# 15s is short enough that an in-progress shift feels live, long
# enough that rapid back-and-forth between pages doesn't re-fetch.
_TODAY_MAX_AGE = 15
# 1 hour. Past data is immutable in principle but capping at 1h keeps
# the browser cache from holding stale templates after a code deploy.
_PAST_MAX_AGE = 3600


def set_cache_headers(response, *, includes_today: bool) -> None:
    """Apply Cache-Control to a response based on data freshness."""
    max_age = _TODAY_MAX_AGE if includes_today else _PAST_MAX_AGE
    response.headers["Cache-Control"] = f"private, max-age={max_age}"


def includes_today(d: date, today: date) -> bool:
    return d >= today


def range_includes_today(end_d: date, today: date) -> bool:
    """For range-based pages (leaderboards): the range's end determines
    whether the data is still moving."""
    return end_d >= today
