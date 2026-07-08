"""Pure-logic tests for machine_breakdown.py's exclusion-math helpers. No DB."""
from datetime import date, datetime, timezone

from zira_dashboard import machine_breakdown


def _pm(day, start, end):
    """Fake productive_minutes_in_window: 1 minute per elapsed minute, no breaks."""
    return (end - start).total_seconds() / 60.0


def test_excluded_minutes_for_windows_sums_closed_windows():
    day = date(2026, 7, 8)
    windows = [
        (datetime(2026, 7, 8, 13, 0, tzinfo=timezone.utc), datetime(2026, 7, 8, 13, 30, tzinfo=timezone.utc)),
        (datetime(2026, 7, 8, 14, 0, tzinfo=timezone.utc), datetime(2026, 7, 8, 14, 10, tzinfo=timezone.utc)),
    ]
    assert machine_breakdown.excluded_minutes_for_windows(windows, day, _pm) == 40.0


def test_excluded_minutes_for_windows_skips_open_and_zero_span():
    day = date(2026, 7, 8)
    s = datetime(2026, 7, 8, 13, 0, tzinfo=timezone.utc)
    windows = [(s, None), (s, s)]
    assert machine_breakdown.excluded_minutes_for_windows(windows, day, _pm) == 0.0


def test_excluded_minutes_overlapping_clips_to_segment_and_caps_open_at_now():
    day = date(2026, 7, 8)
    seg_start = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    seg_end = datetime(2026, 7, 8, 14, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 8, 13, 45, tzinfo=timezone.utc)
    # Breakdown window opens at 13:00, still open (None) -- caps at `now` (13:45),
    # clipped to the segment [12:00, 14:00) -- overlap is [13:00, 13:45) = 45 min.
    windows = [(datetime(2026, 7, 8, 13, 0, tzinfo=timezone.utc), None)]
    minutes = machine_breakdown.excluded_minutes_overlapping(
        windows, seg_start, seg_end, now, day, _pm
    )
    assert minutes == 45.0


def test_excluded_minutes_overlapping_no_overlap_returns_zero():
    day = date(2026, 7, 8)
    seg_start = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)
    seg_end = datetime(2026, 7, 8, 7, 0, tzinfo=timezone.utc)
    now = datetime(2026, 7, 8, 13, 45, tzinfo=timezone.utc)
    windows = [(datetime(2026, 7, 8, 13, 0, tzinfo=timezone.utc), None)]
    minutes = machine_breakdown.excluded_minutes_overlapping(
        windows, seg_start, seg_end, now, day, _pm
    )
    assert minutes == 0.0
