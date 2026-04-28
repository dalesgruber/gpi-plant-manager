from datetime import date, time
from zira_dashboard import shift_config, staffing


def test_shift_start_for_default_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.shift_start_for(date(2026, 4, 28)) == shift_config.shift_start()


def test_shift_start_for_uses_override(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "09:30", "end": "15:00", "breaks": []},
        ))
    assert shift_config.shift_start_for(date(2026, 4, 28)) == time(9, 30)


def test_shift_end_for_default_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.shift_end_for(date(2026, 4, 28)) == shift_config.shift_end()


def test_shift_end_for_uses_override(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "07:00", "end": "11:30", "breaks": []},
        ))
    assert shift_config.shift_end_for(date(2026, 4, 28)) == time(11, 30)


from zira_dashboard.schedule_store import Break


def test_breaks_for_default_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.breaks_for(date(2026, 4, 28)) == shift_config.breaks()


def test_breaks_for_uses_override(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={
                "start": "07:00", "end": "15:00",
                "breaks": [
                    {"start": "10:00", "end": "10:30", "name": "Meeting"},
                    {"start": "12:00", "end": "12:30", "name": "Lunch"},
                ],
            },
        ))
    out = shift_config.breaks_for(date(2026, 4, 28))
    assert out == (
        Break(time(10, 0), time(10, 30), "Meeting"),
        Break(time(12, 0), time(12, 30), "Lunch"),
    )


def test_breaks_for_empty_list_means_no_breaks(monkeypatch):
    """Empty list override means 'no breaks today' — not 'fall back to global'."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "07:00", "end": "11:00", "breaks": []},
        ))
    assert shift_config.breaks_for(date(2026, 4, 28)) == ()


def test_productive_minutes_for_default_matches_global(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.productive_minutes_for(date(2026, 4, 28)) == shift_config.productive_minutes_per_day()


def test_productive_minutes_for_half_day_with_one_break(monkeypatch):
    """09:00 → 13:00 = 240 min, minus a 30-min break = 210 min."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={
                "start": "09:00", "end": "13:00",
                "breaks": [{"start": "11:00", "end": "11:30", "name": "Lunch"}],
            },
        ))
    assert shift_config.productive_minutes_for(date(2026, 4, 28)) == 210


from datetime import datetime
from zira_dashboard.shift_config import SITE_TZ


def test_in_shift_on_respects_custom_hours(monkeypatch):
    """09:30 → 13:00 override; 09:00 should be out, 10:00 should be in."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "09:30", "end": "13:00", "breaks": []},
        ))
    monkeypatch.setattr(shift_config, "work_weekdays", lambda: frozenset(range(7)))
    early = datetime(2026, 4, 28, 9, 0, tzinfo=SITE_TZ)
    inside = datetime(2026, 4, 28, 10, 0, tzinfo=SITE_TZ)
    assert shift_config.in_shift_on(early) is False
    assert shift_config.in_shift_on(inside) is True


def test_shift_elapsed_minutes_respects_custom_hours(monkeypatch):
    """Custom 09:00 → 13:00 with a 30-min break at 11:00. As of 12:00,
    elapsed = 9-11 (120 min) + 11:30-12:00 (30 min) = 150 min."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={
                "start": "09:00", "end": "13:00",
                "breaks": [{"start": "11:00", "end": "11:30", "name": "Lunch"}],
            },
        ))
    monkeypatch.setattr(shift_config, "work_weekdays", lambda: frozenset(range(7)))
    d = date(2026, 4, 28)
    now = datetime(2026, 4, 28, 12, 0, tzinfo=SITE_TZ)
    assert shift_config.shift_elapsed_minutes(d, now) == 150
