from __future__ import annotations


def test_seed_defaults_backfills_recycling_leaderboard_when_rows_already_exist(monkeypatch):
    from zira_dashboard import app_settings, db, tv_displays_store

    calls: list[tuple[str, tuple | None]] = []
    markers = {
        "tv_displays:seed_recycling_leaderboard_v1": None,
        "tv_displays:seed_new_leaderboard_v1": {"done": True},
    }

    def fake_get_setting(key):
        return markers[key]

    def fake_set_setting(key, value):
        markers[key] = value

    def fake_query(sql, params=None):
        calls.append((sql, params))
        if "SELECT 1 FROM tv_displays LIMIT 1" in sql:
            return [{"exists": 1}]
        if "WHERE kind = %s" in sql:
            return []
        if "SELECT COALESCE(MAX(sort_order), -1)" in sql:
            return [{"sort_order": 10}]
        if "SELECT id FROM tv_displays WHERE slug = %s" in sql:
            return []
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr(app_settings, "get_setting", fake_get_setting)
    monkeypatch.setattr(app_settings, "set_setting", fake_set_setting)
    monkeypatch.setattr(db, "query", fake_query)
    monkeypatch.setattr(db, "execute", lambda sql, params=None: calls.append((sql, params)))

    tv_displays_store.seed_defaults_if_empty()

    inserted = [
        params for sql, params in calls
        if "INSERT INTO tv_displays" in sql and params is not None
    ]
    assert inserted == [
        (
            "Recycling-leaderboard",
            "recycling-leaderboard",
            "vs_recycling_leaderboard",
            None,
            "dark",
            11,
        )
    ]
    assert markers["tv_displays:seed_recycling_leaderboard_v1"] == {"done": True}


def test_seed_defaults_backfills_new_leaderboard_when_rows_already_exist(monkeypatch):
    from zira_dashboard import app_settings, db, tv_displays_store

    calls: list[tuple[str, tuple | None]] = []
    markers = {
        "tv_displays:seed_recycling_leaderboard_v1": {"done": True},
        "tv_displays:seed_new_leaderboard_v1": None,
    }

    monkeypatch.setattr(app_settings, "get_setting", lambda key: markers[key])
    monkeypatch.setattr(
        app_settings,
        "set_setting",
        lambda key, value: markers.__setitem__(key, value),
    )

    def fake_query(sql, params=None):
        calls.append((sql, params))
        if "SELECT 1 FROM tv_displays LIMIT 1" in sql:
            return [{"exists": 1}]
        if "WHERE kind = %s" in sql:
            return []
        if "SELECT COALESCE(MAX(sort_order), -1)" in sql:
            return [{"sort_order": 10}]
        if "SELECT id FROM tv_displays WHERE slug = %s" in sql:
            return []
        raise AssertionError(f"unexpected query: {sql}")

    monkeypatch.setattr(db, "query", fake_query)
    monkeypatch.setattr(db, "execute", lambda sql, params=None: calls.append((sql, params)))

    tv_displays_store.seed_defaults_if_empty()

    inserted = [
        params for sql, params in calls
        if "INSERT INTO tv_displays" in sql and params is not None
    ]
    assert inserted == [
        ("New-Leaderboard", "new-leaderboard", "vs_new_leaderboard", None, "dark", 11)
    ]
    assert markers["tv_displays:seed_new_leaderboard_v1"] == {"done": True}
