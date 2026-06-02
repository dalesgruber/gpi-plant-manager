"""Pure state-machine + window tests for auto_lunch. No DB/Odoo."""
from __future__ import annotations

from datetime import date, datetime, time

from zira_dashboard import auto_lunch as al
from zira_dashboard.schedule_store import Break
from zira_dashboard.shift_config import SITE_TZ


def _dt(h, m=0):
    return datetime(2026, 6, 2, h, m, tzinfo=SITE_TZ)


def test_lunch_window_picks_the_lunch_break():
    breaks = (Break(time(9, 0), time(9, 15), "Morning break"),
              Break(time(11, 0), time(11, 30), "Lunch"))
    w = al.lunch_window_for_day(breaks, date(2026, 6, 2))
    assert w.out_at == _dt(11, 0) and w.in_at == _dt(11, 30)


def test_lunch_window_matches_name_case_insensitively():
    breaks = (Break(time(11, 0), time(11, 30), "LUNCH"),)
    w = al.lunch_window_for_day(breaks, date(2026, 6, 2))
    assert w is not None and w.out_at == _dt(11, 0)


def test_lunch_window_none_when_no_lunch():
    breaks = (Break(time(9, 0), time(9, 15), "Morning break"),)
    assert al.lunch_window_for_day(breaks, date(2026, 6, 2)) is None


def test_flex_window_from_first_clock_in():
    w = al.flex_window(_dt(6, 0), 5.0, 30)
    assert w.out_at == _dt(11, 0) and w.in_at == _dt(11, 30)


def test_pending_clocked_in_at_lunch_triggers_auto_out():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("pending", True, w, _dt(11, 0))
    assert t.new_state == "auto_out" and t.action == "clock_out" and t.at == _dt(11, 0)


def test_pending_clocked_out_at_lunch_is_skipped():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("pending", False, w, _dt(11, 0))
    assert t.new_state == "skipped" and t.action is None


def test_pending_before_lunch_does_nothing():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("pending", True, w, _dt(10, 30))
    assert t.new_state == "pending" and t.action is None


def test_auto_out_returns_clock_in_at_lunch_end_when_out():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("auto_out", False, w, _dt(11, 30))
    assert t.new_state == "done" and t.action == "clock_in" and t.at == _dt(11, 30)


def test_auto_out_already_in_at_end_no_double_punch():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("auto_out", True, w, _dt(11, 30))
    assert t.new_state == "done" and t.action is None


def test_auto_out_mid_gap_waits():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    t = al.decide("auto_out", False, w, _dt(11, 15))
    assert t.new_state == "auto_out" and t.action is None


def test_terminal_states_are_inert():
    w = al.Window(_dt(11, 0), _dt(11, 30))
    for st in ("done", "skipped", "ended_by_employee"):
        t = al.decide(st, True, w, _dt(12, 0))
        assert t.new_state == st and t.action is None
