from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from zira_dashboard import staffing
from zira_dashboard.app import app


def test_recycling_headline_uses_per_person_rate(monkeypatch):
    # 100 units across 1.0 elapsed hour with 2 scheduled people = 50 / hr / person
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"], "Repair-2": ["Bob"]},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb, \
         patch("zira_dashboard.routes.value_streams.shift_elapsed_minutes", return_value=60):
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        s2 = Station(meter_id="m2", name="Repair-2", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=50, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
            StationTotal(s2, units=50, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        resp = client.get("/recycling")
    assert resp.status_code == 200
    html = resp.text
    # Headline label changed AND value reflects /person denominator
    assert "pallets/hr/person" in html
    assert ">50.0<" in html or ">50<" in html
