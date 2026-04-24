"""Bucket per-station samples into fixed-width time windows for progress charts."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable

from .leaderboard import StationTotal
from .settings_store import group_target
from .shift_config import SITE_TZ, breaks, shift_end, shift_start, work_weekdays


def _in_any_break(t: time) -> bool:
    return any(b.start <= t < b.end for b in breaks())


def progress_buckets(
    group: Iterable[StationTotal],
    day: date,
    now_utc: datetime,
    bucket_minutes: int = 15,
) -> list[dict]:
    """Return one dict per 15-min bucket from shift start to min(now, shift end).

    Breaks are skipped. Target is computed from the category's per-hour target
    × number of stations × bucket fraction.
    """
    group = list(group)
    if not group or day.weekday() not in work_weekdays():
        return []

    # Group target per bucket: category's current per-hour target × fraction of hour.
    category = group[0].station.category
    per_bucket_target = group_target(category) * (bucket_minutes / 60.0)

    # All samples, converted to site-local time.
    samples: list[tuple[datetime, int]] = []
    for st in group:
        for ts_utc, units in st.samples:
            samples.append((ts_utc.astimezone(SITE_TZ), units))

    start = datetime.combine(day, shift_start(), tzinfo=SITE_TZ)
    end = datetime.combine(day, shift_end(), tzinfo=SITE_TZ)
    edge = min(now_utc.astimezone(SITE_TZ), end)
    if edge <= start:
        return []

    buckets: list[dict] = []
    cursor = start
    delta = timedelta(minutes=bucket_minutes)
    while cursor < edge:
        b_start = cursor
        b_end = cursor + delta
        cursor = b_end
        if _in_any_break(b_start.time()):
            continue  # wholly inside a break period
        actual = sum(u for ts, u in samples if b_start <= ts < b_end)
        # For the current (in-progress) bucket, don't penalize — show only actual.
        in_progress = b_end > edge
        tgt = per_bucket_target if not in_progress else actual
        buckets.append(
            {
                "label": b_start.strftime("%H:%M"),
                "actual": actual,
                "target": int(round(tgt)),
                "in_progress": in_progress,
            }
        )
    return buckets
