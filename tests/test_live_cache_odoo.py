from datetime import date
from zira_dashboard import live_cache, attendance


def test_refresh_attendance_writes_odoo_punches(monkeypatch):
    monkeypatch.setattr(
        attendance, "punches_for_day",
        lambda d: {"7": {"first_check_in": "2026-06-01T12:00:00+00:00", "currently_open": True}},
    )
    written = {}
    monkeypatch.setattr(
        live_cache, "write_attendance",
        lambda day, payload: written.update({"day": day, "payload": payload}),
    )
    live_cache.refresh_attendance(date(2026, 6, 1))
    assert written["payload"] == {"7": {"first_check_in": "2026-06-01T12:00:00+00:00", "currently_open": True}}


def test_refresh_attendance_swallows_errors(monkeypatch):
    def boom(d):
        raise RuntimeError("odoo down")
    monkeypatch.setattr(attendance, "punches_for_day", boom)
    # Must not raise — the warmer relies on this.
    live_cache.refresh_attendance(date(2026, 6, 1))
