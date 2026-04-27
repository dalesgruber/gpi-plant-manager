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


def test_unmetered_wc_credits_day_but_zero_units():
    # Hand Build has no meter_id, so no entry in wc_totals.
    out = attribute_for_day(
        assignments={"Hand Build #1": ["Lupe", "Carlos"]},
        wc_totals={},  # empty — no Zira data for this WC
        elapsed_minutes=480,
    )
    assert out["Lupe"]["Hand Build #1"]["units"] == 0.0
    assert out["Lupe"]["Hand Build #1"]["downtime"] == 0.0
    assert out["Lupe"]["Hand Build #1"]["days_worked"] == 1
    assert out["Carlos"]["Hand Build #1"]["days_worked"] == 1


from zira_dashboard.production_history import attribute_for_range


def test_range_sums_units_and_days():
    day1 = {
        "Christian": {"Repair 1": {"units": 80.0, "downtime": 12.0, "hours": 8.0, "days_worked": 1}},
    }
    day2 = {
        "Christian": {"Repair 1": {"units": 95.0, "downtime": 5.0,  "hours": 8.0, "days_worked": 1}},
    }
    day3 = {
        "Christian": {"Repair 4": {"units": 70.0, "downtime": 0.0, "hours": 8.0, "days_worked": 1}},
        "Adrian":    {"Repair 1": {"units": 75.0, "downtime": 8.0, "hours": 8.0, "days_worked": 1}},
    }
    out = attribute_for_range([day1, day2, day3])
    assert out["Christian"]["Repair 1"]["units"] == 175.0
    assert out["Christian"]["Repair 1"]["days_worked"] == 2
    assert out["Christian"]["Repair 4"]["days_worked"] == 1
    assert out["Adrian"]["Repair 1"]["days_worked"] == 1
    assert out["Adrian"]["Repair 1"]["units"] == 75.0


def test_attribution_for_returns_empty_for_unpublished_day(monkeypatch):
    """Drafts don't count for attribution history."""
    from zira_dashboard import staffing
    from zira_dashboard.production_history import attribution_for

    fake_sched = staffing.Schedule(
        day=date(2026, 4, 27),
        published=False,
        assignments={"Repair 1": ["Christian"]},
    )
    monkeypatch.setattr(staffing, "load_schedule", lambda d: fake_sched)
    out = attribution_for(date(2026, 4, 27), client=object())
    assert out == {}


def test_attribution_for_uses_published_assignments(monkeypatch):
    from zira_dashboard import staffing, production_history
    from zira_dashboard.production_history import attribution_for

    fake_sched = staffing.Schedule(
        day=date(2026, 4, 27),
        published=True,
        assignments={"Trim Saw 1": ["Iban", "Porfirio"]},
    )
    monkeypatch.setattr(staffing, "load_schedule", lambda d: fake_sched)

    # Stub the per-day Zira lookup so we don't hit the real API.
    def fake_wc_totals(client, day):
        return {"Trim Saw 1": (200, 6)}
    monkeypatch.setattr(production_history, "_fetch_wc_totals", fake_wc_totals)
    monkeypatch.setattr(production_history, "_elapsed_minutes_for", lambda d: 480)

    out = attribution_for(date(2026, 4, 27), client=object())
    assert out["Iban"]["Trim Saw 1"]["units"] == 100.0
    assert out["Porfirio"]["Trim Saw 1"]["units"] == 100.0


from zira_dashboard.production_history import rank_by_category


def test_rank_by_category_filters_to_category_wcs_and_threshold():
    range_out = {
        "Christian": {"Repair 1": {"units": 480.0, "downtime": 30.0, "hours": 40.0, "days_worked": 5}},
        "Adrian":    {"Repair 1": {"units": 250.0, "downtime": 10.0, "hours": 16.0, "days_worked": 2}},  # below threshold
        "Eulogio":   {"Repair 4": {"units": 385.0, "downtime": 18.0, "hours": 40.0, "days_worked": 5}},
        "Iban":      {"Trim Saw 1": {"units": 600.0, "downtime": 12.0, "hours": 40.0, "days_worked": 5}},  # different category
    }
    expected_per_wc = {"Repair 1": 100, "Repair 4": 100}

    rows = rank_by_category(
        range_out,
        category_wcs=["Repair 1", "Repair 2", "Repair 3", "Repair 4", "Repair 5"],
        expected_units_per_day_by_wc=expected_per_wc,
        min_days=3,
    )
    names = [r["name"] for r in rows]
    assert names == ["Christian", "Eulogio"]
    assert "Adrian" not in names
    assert "Iban" not in names
    assert rows[0]["pct_of_target"] == 96.0
