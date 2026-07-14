from zira_dashboard import automated_skill_settings as store

import pytest


def test_missing_groups_use_independent_default_buckets(monkeypatch):
    monkeypatch.setattr(store.app_settings, "get_setting", lambda _key: None)

    repair = store.current("Repair")
    dismantler = store.current("Dismantler")

    assert repair == store.BucketSettings(90.0, 80.0, 70.0)
    assert dismantler == store.BucketSettings(90.0, 80.0, 70.0)
    assert repair is not dismantler


def test_thresholds_must_be_numbers_in_descending_order():
    with pytest.raises(ValueError, match="0 through 100"):
        store.validate(store.BucketSettings(101, 80, 70))
    with pytest.raises(ValueError, match="Level 3 >= Level 2 >= Level 1"):
        store.validate(store.BucketSettings(80, 90, 70))


def test_save_preserves_the_other_group(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        store.app_settings,
        "get_setting",
        lambda _key: {
            "Repair": {"level_3_min": 91, "level_2_min": 81, "level_1_min": 71}
        },
    )
    monkeypatch.setattr(
        store.app_settings, "set_setting", lambda key, value: saved.update({key: value})
    )

    store.save("Dismantler", store.BucketSettings(92, 82, 72))

    assert saved[store.BUCKET_SETTINGS_NAME] == {
        "Repair": {"level_3_min": 91.0, "level_2_min": 81.0, "level_1_min": 71.0},
        "Dismantler": {"level_3_min": 92.0, "level_2_min": 82.0, "level_1_min": 72.0},
    }


def test_last_run_round_trips_failures(monkeypatch):
    saved = {}
    monkeypatch.setattr(store.app_settings, "get_setting", lambda key: saved.get(key))
    monkeypatch.setattr(
        store.app_settings, "set_setting", lambda key, value: saved.update({key: value})
    )
    expected = store.RunSummary(
        group="Repair",
        trigger="manual",
        evaluated=4,
        changed=1,
        unchanged=2,
        skipped=1,
        failures=({"name": "Ana", "error": "Odoo down"},),
        run_at="2026-07-13T18:00:00+00:00",
    )

    store.save_last_run(expected)

    assert store.last_run("Repair") == expected
