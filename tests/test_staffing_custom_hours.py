from datetime import date
from zira_dashboard.staffing import Schedule


def test_schedule_custom_hours_defaults_to_none():
    s = Schedule(day=date(2026, 4, 28))
    assert s.custom_hours is None


import json
from zira_dashboard import staffing


def test_load_schedule_reads_custom_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    payload = {
        "day": d.isoformat(),
        "published": True,
        "assignments": {"Repair 1": ["Jose"]},
        "custom_hours": {
            "start": "09:00",
            "end": "13:00",
            "breaks": [{"start": "11:00", "end": "11:15", "name": "Stand-up"}],
        },
    }
    (tmp_path / f"{d.isoformat()}.json").write_text(json.dumps(payload), encoding="utf-8")
    sched = staffing.load_schedule(d)
    assert sched.custom_hours == {
        "start": "09:00",
        "end": "13:00",
        "breaks": [{"start": "11:00", "end": "11:15", "name": "Stand-up"}],
    }


def test_load_schedule_treats_missing_custom_hours_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    (tmp_path / f"{d.isoformat()}.json").write_text(
        json.dumps({"day": d.isoformat(), "published": False, "assignments": {}}),
        encoding="utf-8",
    )
    sched = staffing.load_schedule(d)
    assert sched.custom_hours is None


def test_save_schedule_writes_custom_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    sched = staffing.Schedule(
        day=d,
        published=False,
        assignments={"Repair 1": ["Jose"]},
        custom_hours={"start": "09:00", "end": "13:00", "breaks": []},
    )
    staffing.save_schedule(sched)
    raw = (tmp_path / f"{d.isoformat()}.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["custom_hours"] == {"start": "09:00", "end": "13:00", "breaks": []}


def test_save_schedule_omits_custom_hours_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    sched = staffing.Schedule(day=d, published=False, assignments={}, custom_hours=None)
    staffing.save_schedule(sched)
    parsed = json.loads((tmp_path / f"{d.isoformat()}.json").read_text(encoding="utf-8"))
    assert "custom_hours" not in parsed or parsed["custom_hours"] is None
