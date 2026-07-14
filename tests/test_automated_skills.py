from datetime import date

from zira_dashboard import automated_skills as subject
from zira_dashboard.automated_skill_settings import BucketSettings


def _record(
    day: str,
    name: str,
    units: float,
    hours: float,
    *,
    operators: int = 1,
    wc: str = "Repair 1",
    person_id: int = 1,
):
    return subject.DailyRecord(
        date.fromisoformat(day), person_id, name, wc, units, hours, operators
    )


def test_bucket_boundaries_are_inclusive():
    settings = BucketSettings(90, 80, 70)

    assert subject.bucket_for(90.0, settings) == 3
    assert subject.bucket_for(80.0, settings) == 2
    assert subject.bucket_for(70.0, settings) == 1
    assert subject.bucket_for(69.999, settings) == 0


def test_two_qualified_days_average_daily_attainment():
    records = [
        _record("2026-07-01", "Ana", 90, 8),
        _record("2026-07-02", "Ana", 100, 8),
    ]

    assert subject.evaluate(records, {"Repair 1": 100}, BucketSettings(), 8) == [
        subject.Evaluation(1, "Ana", 2, 95.0, 3)
    ]


def test_under_four_hour_day_does_not_count_toward_two_day_minimum():
    records = [
        _record("2026-07-01", "Ana", 100, 8),
        _record("2026-07-02", "Ana", 100, 3),
    ]

    assert subject.evaluate(records, {"Repair 1": 100}, BucketSettings(), 8) == [
        subject.Evaluation(1, "Ana", 1, None, None)
    ]


def test_two_people_split_goal_and_output_equally():
    records = [
        _record("2026-07-01", "Ana", 50, 8, operators=2),
        _record("2026-07-02", "Ana", 45, 8, operators=2),
    ]

    assert subject.evaluate(records, {"Repair 1": 100}, BucketSettings(), 8) == [
        subject.Evaluation(1, "Ana", 2, 95.0, 3)
    ]


def test_multiple_centers_combine_daily_goal_shares_before_scoring():
    records = [
        _record("2026-07-01", "Ana", 50, 4, wc="Repair 1"),
        _record("2026-07-01", "Ana", 100, 4, wc="Repair 2"),
        _record("2026-07-02", "Ana", 150, 8, wc="Repair 2"),
    ]

    assert subject.evaluate(
        records, {"Repair 1": 50, "Repair 2": 150}, BucketSettings(), 8
    ) == [subject.Evaluation(1, "Ana", 2, 87.5, 2)]


def test_run_group_changes_only_eligible_mismatched_levels(monkeypatch):
    records = [
        _record("2026-07-01", "Ana", 100, 8),
        _record("2026-07-02", "Ana", 100, 8),
        _record("2026-07-01", "Ben", 100, 8, person_id=2),
    ]
    monkeypatch.setattr(subject, "records_for_group", lambda *_args: records)
    monkeypatch.setattr(subject, "goals_for_group", lambda _group: {"Repair 1": 100})
    monkeypatch.setattr(subject.shift_config, "productive_minutes_per_day", lambda: 480)
    monkeypatch.setattr(subject.settings_store, "current", lambda _group: BucketSettings())
    monkeypatch.setattr(subject, "current_levels", lambda _group: {1: (10, 2), 2: (20, 3)})
    monkeypatch.setattr(subject.settings_store, "save_last_run", lambda _summary: None)
    writes = []
    monkeypatch.setattr(
        subject.skill_levels,
        "set_person_skill_level",
        lambda person_id, skill_id, level: writes.append((person_id, skill_id, level)),
    )

    summary = subject.run_group("Repair", "manual", date(2026, 7, 2))

    assert writes == [(1, 10, 3)]
    assert (summary.evaluated, summary.changed, summary.unchanged, summary.skipped) == (1, 1, 0, 1)


def test_one_odoo_failure_does_not_block_following_person(monkeypatch):
    from zira_dashboard import skill_levels

    records = [
        _record("2026-07-01", "Ana", 100, 8),
        _record("2026-07-02", "Ana", 100, 8),
        _record("2026-07-01", "Ben", 100, 8, person_id=2),
        _record("2026-07-02", "Ben", 100, 8, person_id=2),
    ]
    monkeypatch.setattr(subject, "records_for_group", lambda *_args: records)
    monkeypatch.setattr(subject, "goals_for_group", lambda _group: {"Repair 1": 100})
    monkeypatch.setattr(subject, "current_levels", lambda _group: {1: (10, 0), 2: (10, 0)})
    monkeypatch.setattr(subject.shift_config, "productive_minutes_per_day", lambda: 480)
    monkeypatch.setattr(subject.settings_store, "current", lambda _group: BucketSettings())
    monkeypatch.setattr(subject.settings_store, "save_last_run", lambda _summary: None)
    writes = []

    def writer(person_id, skill_id, level):
        if person_id == 1:
            raise skill_levels.SkillSyncError("Odoo down")
        writes.append((person_id, skill_id, level))

    monkeypatch.setattr(subject.skill_levels, "set_person_skill_level", writer)

    summary = subject.run_group("Repair", "manual", date(2026, 7, 2))

    assert writes == [(2, 10, 3)]
    assert summary.changed == 1
    assert summary.failures == ({"name": "Ana", "error": "Odoo down"},)


def test_eligible_low_attainment_can_demote(monkeypatch):
    records = [
        _record("2026-07-01", "Ana", 60, 8),
        _record("2026-07-02", "Ana", 60, 8),
    ]
    monkeypatch.setattr(subject, "records_for_group", lambda *_args: records)
    monkeypatch.setattr(subject, "goals_for_group", lambda _group: {"Repair 1": 100})
    monkeypatch.setattr(subject, "current_levels", lambda _group: {1: (10, 3)})
    monkeypatch.setattr(subject.shift_config, "productive_minutes_per_day", lambda: 480)
    monkeypatch.setattr(subject.settings_store, "current", lambda _group: BucketSettings())
    monkeypatch.setattr(subject.settings_store, "save_last_run", lambda _summary: None)
    writes = []
    monkeypatch.setattr(
        subject.skill_levels,
        "set_person_skill_level",
        lambda person_id, skill_id, level: writes.append((person_id, skill_id, level)),
    )

    summary = subject.run_group("Repair", "manual", date(2026, 7, 2))

    assert writes == [(1, 10, 0)]
    assert summary.changed == 1


def test_daily_gate_runs_once_after_shift_end(monkeypatch):
    from datetime import datetime, time
    from zoneinfo import ZoneInfo

    writes: dict = {}
    calls = []
    monkeypatch.setattr(subject, "plant_today", lambda: date(2026, 7, 13))
    monkeypatch.setattr(subject.shift_config, "shift_end_for", lambda _day: time(16, 0))
    monkeypatch.setattr(subject.app_settings, "get_setting", lambda key: writes.get(key))
    monkeypatch.setattr(
        subject.app_settings, "set_setting", lambda key, value: writes.update({key: value})
    )
    monkeypatch.setattr(
        subject,
        "run_group",
        lambda group, trigger, day: calls.append((group, trigger, day)) or object(),
    )

    central = ZoneInfo("America/Chicago")
    assert subject.run_daily_if_due(datetime(2026, 7, 13, 15, 59, tzinfo=central)) == []
    subject.run_daily_if_due(datetime(2026, 7, 13, 16, 1, tzinfo=central))
    subject.run_daily_if_due(datetime(2026, 7, 13, 16, 2, tzinfo=central))

    assert calls == [
        ("Repair", "daily", date(2026, 7, 13)),
        ("Dismantler", "daily", date(2026, 7, 13)),
    ]
    assert writes["automated_skills.last_daily_day"] == {"day": "2026-07-13"}
