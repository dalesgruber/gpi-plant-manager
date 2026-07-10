from datetime import UTC, date, datetime
from types import SimpleNamespace

from zira_dashboard.routes import departments


DAY = date(2026, 7, 10)
NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)


def test_new_day_data_uses_one_new_group(monkeypatch):
    captured = {}

    def fake_compute(d, now, is_today_d, **kwargs):
        captured.update(kwargs)
        return {"group_buckets": {"New": []}}

    monkeypatch.setattr(departments, "_department_day_data", fake_compute)
    monkeypatch.setattr(
        departments.staffing,
        "LOCATIONS",
        (SimpleNamespace(
            name="Junior #2", skill="Junior", department="New",
            meter_id="42345",
        ),),
    )
    monkeypatch.setattr(
        departments.work_centers_store,
        "department",
        lambda loc: "New",
    )

    result = departments._new_day_data(DAY, NOW, True)

    assert captured["labor_department"] == "New"
    assert captured["group_categories"] == ("New",)
    assert [s.name for s in captured["stations"]] == ["Junior #2"]
    assert captured["stations"][0].category == "New"
    assert result["group_buckets"] == {"New": []}


def test_recycling_wrapper_preserves_two_groups(monkeypatch):
    captured = {}

    def fake_compute(d, now, is_today_d, **kwargs):
        captured.update(kwargs)
        return {"group_buckets": {"Dismantler": [], "Repair": []}}

    monkeypatch.setattr(departments, "_department_day_data", fake_compute)
    monkeypatch.setattr(departments, "recycling_stations", lambda: [])

    departments._recycling_day_data(DAY, NOW, True)

    assert captured["labor_department"] == "Recycled"
    assert captured["group_categories"] == ("Dismantler", "Repair")
