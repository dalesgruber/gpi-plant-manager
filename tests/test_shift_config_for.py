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
