"""Tests for the pure rounding function. No DB needed — apply_rounding
takes the schedule times and settings as parameters."""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from zira_dashboard.rounding import RoundingSettings, apply_rounding

SITE_TZ = ZoneInfo("America/Chicago")


def _local(year, month, day, hour, minute):
    """Build a UTC datetime that represents the given site-local wall time."""
    return datetime(year, month, day, hour, minute, tzinfo=SITE_TZ).astimezone(timezone.utc)


SHIFT_START = time(7, 0)
SHIFT_END = time(15, 30)


def test_clock_in_within_before_window_rounds_to_start():
    """6:50 AM clock_in with in_before=20 rounds UP to 7:00 AM."""
    occurred = _local(2026, 5, 27, 6, 50)
    settings = RoundingSettings(in_before_min=20, in_after_min=0, out_before_min=0, out_after_min=0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 7, 0)


def test_clock_in_outside_before_window_unchanged():
    """6:38 AM (22 min before) with in_before=20 stays as 6:38."""
    occurred = _local(2026, 5, 27, 6, 38)
    settings = RoundingSettings(20, 0, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_clock_in_within_after_window_rounds_to_start():
    """7:05 AM with in_after=10 rounds DOWN to 7:00."""
    occurred = _local(2026, 5, 27, 7, 5)
    settings = RoundingSettings(0, 10, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 7, 0)


def test_clock_in_outside_after_window_unchanged():
    """9:00 AM (came in 2hr late) with in_after=10 stays as 9:00."""
    occurred = _local(2026, 5, 27, 9, 0)
    settings = RoundingSettings(0, 10, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_clock_out_within_after_window_rounds_to_end():
    """3:35 PM clock_out with out_after=20 rounds DOWN to 3:30 PM."""
    occurred = _local(2026, 5, 27, 15, 35)
    settings = RoundingSettings(0, 0, 0, 20)
    rounded = apply_rounding("clock_out", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 15, 30)


def test_clock_out_within_before_window_rounds_to_end():
    """3:25 PM clock_out with out_before=10 rounds UP to 3:30 PM."""
    occurred = _local(2026, 5, 27, 15, 25)
    settings = RoundingSettings(0, 0, 10, 0)
    rounded = apply_rounding("clock_out", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 15, 30)


def test_clock_out_early_leave_unchanged():
    """1:00 PM clock_out, outside window, stays."""
    occurred = _local(2026, 5, 27, 13, 0)
    settings = RoundingSettings(0, 0, 60, 60)
    rounded = apply_rounding("clock_out", occurred, SHIFT_START, SHIFT_END, settings)
    # 13:00 is 2.5h before 15:30, way outside the 60-min before window
    assert rounded == occurred


def test_transfer_in_never_rounded():
    """transfer_in at 6:50 AM with in_before=20 stays at 6:50 (transfers are never rounded)."""
    occurred = _local(2026, 5, 27, 6, 50)
    settings = RoundingSettings(20, 20, 20, 20)
    rounded = apply_rounding("transfer_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_transfer_out_never_rounded():
    """transfer_out at 3:35 PM with out_after=20 stays at 3:35."""
    occurred = _local(2026, 5, 27, 15, 35)
    settings = RoundingSettings(20, 20, 20, 20)
    rounded = apply_rounding("transfer_out", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == occurred


def test_zero_window_disables_rounding():
    """All settings = 0 → every clock_in/clock_out returns occurred_at unchanged."""
    settings = RoundingSettings(0, 0, 0, 0)
    for hh, mm in [(6, 50), (7, 0), (7, 5), (15, 25), (15, 30), (15, 35)]:
        occurred = _local(2026, 5, 27, hh, mm)
        for action in ("clock_in", "clock_out"):
            assert apply_rounding(action, occurred, SHIFT_START, SHIFT_END, settings) == occurred


def test_boundary_at_exact_window_edge_inclusive():
    """6:40 AM with in_before=20 (exactly 20 min before 7:00) rounds — bound is inclusive."""
    occurred = _local(2026, 5, 27, 6, 40)
    settings = RoundingSettings(20, 0, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 5, 27, 7, 0)


def test_custom_shift_times_round_to_those():
    """Saturday OT with custom 8:00 AM start: 7:50 AM rounds to 8:00, not the default 7:00."""
    occurred = _local(2026, 5, 30, 7, 50)  # Saturday
    custom_start = time(8, 0)
    custom_end = time(12, 0)
    settings = RoundingSettings(20, 0, 0, 0)
    rounded = apply_rounding("clock_in", occurred, custom_start, custom_end, settings)
    assert rounded == _local(2026, 5, 30, 8, 0)


def test_naive_datetime_raises():
    """occurred_at without tzinfo raises ValueError — the function is
    documented as requiring aware datetimes; silent treatment as local
    system time would hide integration bugs."""
    naive = datetime(2026, 5, 27, 6, 50)  # no tzinfo
    settings = RoundingSettings(20, 0, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        apply_rounding("clock_in", naive, SHIFT_START, SHIFT_END, settings)


def test_rounds_correctly_across_dst_spring_forward():
    """Locks in correct rounding behavior across the spring-forward
    DST transition. On 2026-03-08, US Central time jumps from CST to
    CDT at 2:00 AM. A 6:50 AM punch is comfortably past the transition;
    we verify it rounds to 7:00 AM CDT, NOT to 7:00 AM CST (which would
    be 8:00 AM CDT and an hour wrong).
    """
    occurred = _local(2026, 3, 8, 6, 50)  # CDT, 6:50 wall clock
    settings = RoundingSettings(20, 0, 0, 0)
    rounded = apply_rounding("clock_in", occurred, SHIFT_START, SHIFT_END, settings)
    assert rounded == _local(2026, 3, 8, 7, 0)  # CDT, 7:00 wall clock
