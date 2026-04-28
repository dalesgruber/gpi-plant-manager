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
