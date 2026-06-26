from datetime import date

from zira_dashboard import forklift_advisor


def test_build_advisor_with_history(monkeypatch):
    monkeypatch.setattr(forklift_advisor.forklift_store, "calls_daily_for_weekday",
                        lambda wd, limit=8: [
                            {"day": date(2026, 6, 19), "total_calls": 420,
                             "by_hour": {"9": {"calls": 70}, "8": {"calls": 30}},
                             "by_station": {}}])
    monkeypatch.setattr(forklift_advisor.app_settings, "get_setting",
                        lambda k: ["Louie", "Juan", "Luke"])

    adv = forklift_advisor.build_advisor(
        target_day=date(2026, 6, 26),   # Friday
        dedicated=3, certified=4, backups=3,
    )
    assert adv["available"] is True
    assert adv["total_calls"] == 420
    assert adv["recommended"] == 3            # ceil(70/30)
    assert adv["coverage"].status == "ok"
    assert adv["basis"] == "history"
    assert "9" in adv["peak_label"] or "9" in str(adv["peak_label"])


def test_build_advisor_no_data_returns_unavailable(monkeypatch):
    monkeypatch.setattr(forklift_advisor.forklift_store, "calls_daily_for_weekday",
                        lambda wd, limit=8: [])
    monkeypatch.setattr(forklift_advisor, "_weekly_trends_or_none", lambda: None)
    adv = forklift_advisor.build_advisor(
        target_day=date(2026, 6, 26), dedicated=0, certified=0, backups=0)
    assert adv["available"] is False
