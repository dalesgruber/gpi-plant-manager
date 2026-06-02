from datetime import datetime, timezone
from zira_dashboard.timeclock_windows import _segments_from_rows

UTC = timezone.utc
def t(h, m=0): return datetime(2026, 6, 2, h, m, tzinfo=UTC)


def test_clock_in_then_out_one_window():
    rows = [
        {"action": "clock_in", "wc_name": "Dismantler 4", "at": t(13)},
        {"action": "clock_out", "wc_name": None, "at": t(17)},
    ]
    assert _segments_from_rows(rows) == [("Dismantler 4", t(13), t(17))]


def test_transfer_splits_into_two_windows():
    rows = [
        {"action": "clock_in", "wc_name": "Dismantler 4", "at": t(13)},
        {"action": "transfer_out", "wc_name": "Dismantler 4", "at": t(15)},
        {"action": "transfer_in", "wc_name": "Repair 1", "at": t(15)},
        {"action": "clock_out", "wc_name": None, "at": t(17)},
    ]
    assert _segments_from_rows(rows) == [
        ("Dismantler 4", t(13), t(15)),
        ("Repair 1", t(15), t(17)),
    ]


def test_still_clocked_in_trailing_window_is_open():
    rows = [{"action": "clock_in", "wc_name": "Dismantler 4", "at": t(13)}]
    assert _segments_from_rows(rows) == [("Dismantler 4", t(13), None)]


def test_window_without_wc_dropped():
    rows = [
        {"action": "clock_in", "wc_name": None, "at": t(13)},
        {"action": "clock_out", "wc_name": None, "at": t(17)},
    ]
    assert _segments_from_rows(rows) == []
