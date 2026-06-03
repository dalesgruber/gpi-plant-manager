"""Characterization tests for the pure kiosk time-off wizard validators.

These pin the behavior extracted verbatim from routes/timeclock_time_off.py
(``_parse_time_to_float`` / ``_shape_to_hour_bounds`` /
``_compute_working_hours_json``) so the decomposition stays behavior-
preserving. Shift bounds used throughout: from=6.0, to=14.5.

Error cases assert only that the 3rd tuple element (the message) is truthy,
not the exact wording, so the tests pin the contract without freezing copy.
"""

from zira_dashboard import time_off_wizard as w

SHIFT_FROM = 6.0
SHIFT_TO = 14.5


# ----- parse_time_to_float -----


def test_parse_time_to_float_valid():
    assert w.parse_time_to_float("09:30") == 9.5
    assert w.parse_time_to_float("14:00") == 14.0


def test_parse_time_to_float_none_and_blank():
    assert w.parse_time_to_float(None) is None
    assert w.parse_time_to_float("") is None


def test_parse_time_to_float_malformed():
    assert w.parse_time_to_float("bad") is None
    assert w.parse_time_to_float("9") is None


# ----- shape_to_hour_bounds -----


def test_shape_full_day_ignores_times():
    # full_day returns (None, None, None) regardless of supplied times.
    assert w.shape_to_hour_bounds(
        "full_day", "09:00", "12:00", SHIFT_FROM, SHIFT_TO
    ) == (None, None, None)
    assert w.shape_to_hour_bounds(
        "full_day", "", "", SHIFT_FROM, SHIFT_TO
    ) == (None, None, None)


def test_shape_late_arrival_ok():
    assert w.shape_to_hour_bounds(
        "late_arrival", "", "09:00", SHIFT_FROM, SHIFT_TO
    ) == (6.0, 9.0, None)


def test_shape_late_arrival_missing_time_b_errors():
    res = w.shape_to_hour_bounds("late_arrival", "", "", SHIFT_FROM, SHIFT_TO)
    assert res[0] is None and res[1] is None and res[2]


def test_shape_late_arrival_at_or_before_start_errors():
    # "06:00" <= shift start (6.0) is rejected.
    res = w.shape_to_hour_bounds(
        "late_arrival", "", "06:00", SHIFT_FROM, SHIFT_TO
    )
    assert res[2]


def test_shape_late_arrival_after_end_errors():
    # "15:00" > shift end (14.5) is rejected.
    res = w.shape_to_hour_bounds(
        "late_arrival", "", "15:00", SHIFT_FROM, SHIFT_TO
    )
    assert res[2]


def test_shape_early_leave_ok():
    assert w.shape_to_hour_bounds(
        "early_leave", "13:00", "", SHIFT_FROM, SHIFT_TO
    ) == (13.0, 14.5, None)


def test_shape_early_leave_at_or_after_end_errors():
    # "14:30" >= shift end (14.5) is rejected.
    res = w.shape_to_hour_bounds(
        "early_leave", "14:30", "", SHIFT_FROM, SHIFT_TO
    )
    assert res[2]


def test_shape_midday_gap_ok():
    assert w.shape_to_hour_bounds(
        "midday_gap", "10:00", "12:00", SHIFT_FROM, SHIFT_TO
    ) == (10.0, 12.0, None)


def test_shape_midday_gap_end_not_after_start_errors():
    # time_b <= time_a is rejected.
    res = w.shape_to_hour_bounds(
        "midday_gap", "12:00", "10:00", SHIFT_FROM, SHIFT_TO
    )
    assert res[2]


def test_shape_unknown_errors():
    res = w.shape_to_hour_bounds(
        "nonsense", "10:00", "12:00", SHIFT_FROM, SHIFT_TO
    )
    assert res[0] is None and res[1] is None and res[2]


# ----- compute_working_hours_json -----


def test_working_hours_full_day_is_none():
    assert w.compute_working_hours_json(
        "full_day", None, None, SHIFT_FROM, SHIFT_TO
    ) is None


def test_working_hours_late_arrival():
    assert w.compute_working_hours_json(
        "late_arrival", 6.0, 9.0, SHIFT_FROM, SHIFT_TO
    ) == [{"from": 9.0, "to": 14.5}]


def test_working_hours_early_leave():
    assert w.compute_working_hours_json(
        "early_leave", 13.0, 14.5, SHIFT_FROM, SHIFT_TO
    ) == [{"from": 6.0, "to": 13.0}]


def test_working_hours_midday_gap():
    assert w.compute_working_hours_json(
        "midday_gap", 10.0, 12.0, SHIFT_FROM, SHIFT_TO
    ) == [{"from": 6.0, "to": 10.0}, {"from": 12.0, "to": 14.5}]
