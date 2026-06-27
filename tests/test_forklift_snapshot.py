from datetime import date

from zira_dashboard import forklift_snapshot


def test_snapshot_today_fetches_transforms_and_stores(monkeypatch):
    calls = {}
    monkeypatch.setattr(forklift_snapshot.forklift_client, "fetch_dashboard",
                        lambda: {"driverLeaderboard": [
                            {"driverId": "fk-1", "name": "Trent", "total": 10}],
                            "hourlyClaimAvgs": [{"slot": 9, "calls": 5}]})
    monkeypatch.setattr(forklift_snapshot.forklift_client, "fetch_queue_history",
                        lambda: [{"workstationName": "Prosaw #4", "status": "completed",
                                  "priority": "normal", "requiredSkillId": "sk-2"}])
    monkeypatch.setattr(forklift_snapshot.forklift_client, "fetch_drivers",
                        lambda: [{"name": "Louie", "isOverloadResponder": True},
                                 {"name": "Trent", "isOverloadResponder": False}])
    monkeypatch.setattr(forklift_snapshot.forklift_store, "upsert_calls_daily",
                        lambda row: calls.setdefault("calls", row))
    monkeypatch.setattr(forklift_snapshot.forklift_store, "upsert_driver_daily",
                        lambda rows: calls.setdefault("drivers", rows) or len(rows))
    saved = {}
    monkeypatch.setattr(forklift_snapshot.app_settings, "set_setting",
                        lambda k, v: saved.update({k: v}))

    out = forklift_snapshot.snapshot_today(client=None, day=date(2026, 6, 26))

    assert calls["calls"]["total_calls"] == 1
    assert calls["drivers"][0]["driver_id"] == "fk-1"
    assert saved["forklift_overload_responders"] == ["Louie"]
    assert out["day"] == "2026-06-26"
