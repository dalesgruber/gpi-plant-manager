from datetime import date
from zira_dashboard.production_history import attribute_for_day


def test_attribute_for_day_empty_schedule_returns_empty():
    out = attribute_for_day(
        assignments={},
        wc_totals={},
        elapsed_minutes=480,
    )
    assert out == {}


def test_solo_operator_gets_full_credit():
    out = attribute_for_day(
        assignments={"Repair 1": ["Christian"]},
        wc_totals={"Repair 1": (80, 12)},
        elapsed_minutes=480,
    )
    assert out == {
        "Christian": {
            "Repair 1": {
                "units": 80.0,
                "downtime": 12.0,
                "hours": 8.0,
                "days_worked": 1,
            }
        }
    }


def test_two_operators_split_evenly():
    out = attribute_for_day(
        assignments={"Trim Saw 1": ["Iban", "Porfirio"]},
        wc_totals={"Trim Saw 1": (200, 6)},
        elapsed_minutes=480,
    )
    assert out["Iban"]["Trim Saw 1"]["units"] == 100.0
    assert out["Iban"]["Trim Saw 1"]["downtime"] == 3.0
    assert out["Porfirio"]["Trim Saw 1"]["units"] == 100.0
    assert out["Porfirio"]["Trim Saw 1"]["downtime"] == 3.0
    assert out["Iban"]["Trim Saw 1"]["days_worked"] == 1
    assert out["Porfirio"]["Trim Saw 1"]["days_worked"] == 1


def test_three_operators_split_evenly():
    out = attribute_for_day(
        assignments={"Hand Build #1": ["A", "B", "C"]},
        wc_totals={"Hand Build #1": (90, 9)},
        elapsed_minutes=480,
    )
    for n in ("A", "B", "C"):
        assert out[n]["Hand Build #1"]["units"] == 30.0
        assert out[n]["Hand Build #1"]["downtime"] == 3.0


from zira_dashboard.staffing import TIME_OFF_KEY


def test_time_off_excluded():
    out = attribute_for_day(
        assignments={
            "Repair 1": ["Christian"],
            TIME_OFF_KEY: ["Iban", "Lupe"],
        },
        wc_totals={"Repair 1": (80, 12)},
        elapsed_minutes=480,
    )
    assert "Christian" in out
    assert "Iban" not in out
    assert "Lupe" not in out
