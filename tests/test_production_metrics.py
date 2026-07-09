from datetime import date

from zira_dashboard import production_metrics as pm


STD_HOURS = 7.0


def rec(day, person, wc, units, hours):
    return {
        "day": day,
        "person": person,
        "wc": wc,
        "units": float(units),
        "hours": float(hours),
        "downtime": 0.0,
    }


def test_normalized_daily_scores_ignores_under_4_hours():
    rows = pm.normalized_daily_scores(
        [rec(date(2026, 7, 1), "Alice", "Repair 1", 60, 3.99)],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert rows == []


def test_normalized_daily_scores_exactly_4_hours_qualifies():
    rows = pm.normalized_daily_scores(
        [rec(date(2026, 7, 1), "Alice", "Repair 1", 80, 4.0)],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    assert rows[0]["day"] == date(2026, 7, 1)
    assert rows[0]["units"] == 80.0
    assert rows[0]["hours"] == 4.0
    assert rows[0]["normalized_units"] == 140.0


def test_normalized_daily_scores_sums_same_day_scope_before_cutoff():
    rows = pm.normalized_daily_scores(
        [
            rec(date(2026, 7, 1), "Alice", "Repair 1", 40, 2.0),
            rec(date(2026, 7, 1), "Alice", "Repair 2", 50, 3.0),
        ],
        wc_names={"Repair 1", "Repair 2"},
        standard_full_day_hours=STD_HOURS,
    )
    assert len(rows) == 1
    assert rows[0]["units"] == 90.0
    assert rows[0]["hours"] == 5.0
    assert rows[0]["normalized_units"] == 126.0


def test_normalized_average_by_person_averages_qualified_days():
    rows = pm.normalized_average_by_person(
        [
            rec(date(2026, 7, 1), "Alice", "Repair 1", 80, 4.0),   # 140
            rec(date(2026, 7, 2), "Alice", "Repair 1", 70, 7.0),   # 70
            rec(date(2026, 7, 3), "Alice", "Repair 1", 999, 3.0),  # ignored
        ],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert rows[0]["name"] == "Alice"
    assert rows[0]["days"] == 2
    assert rows[0]["avg_units"] == 105.0
    assert rows[0]["total_units"] == 150.0
    assert rows[0]["total_hours"] == 11.0


def test_normalized_average_by_person_sorts_by_avg_then_days_then_name():
    rows = pm.normalized_average_by_person(
        [
            rec(date(2026, 7, 1), "Bob", "Repair 1", 70, 7.0),
            rec(date(2026, 7, 1), "Anne", "Repair 1", 70, 7.0),
            rec(date(2026, 7, 2), "Anne", "Repair 1", 70, 7.0),
            rec(date(2026, 7, 1), "Cara", "Repair 1", 100, 7.0),
        ],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert [r["name"] for r in rows] == ["Cara", "Anne", "Bob"]
