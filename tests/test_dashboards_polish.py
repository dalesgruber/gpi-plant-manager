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


def test_recycling_bar_row_renders_person_and_wc_stacked(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"]},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb:
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=10, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        html = client.get("/recycling").text
    assert "name-primary" in html
    assert "name-secondary" in html
    assert "Alice" in html and "Repair-1" in html


def test_recycling_bar_row_no_assignment_fallback(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True, assignments={},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb:
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=20, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        html = client.get("/recycling").text
    assert "(no assignment)" in html
    assert "Repair-1" in html


def test_recycling_downtime_row_renders_person_and_wc_stacked(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"]},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb:
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=5, reading_count=1, truncated=False, downtime_minutes=12,
                         active_minutes=48, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        html = client.get("/recycling").text
    # Both Alice and Repair-1 appear in the rendered HTML; with 12 downtime minutes
    # the WC qualifies as active so we expect at least 2 occurrences (bar widget + downtime row).
    assert html.count("Alice") >= 2
    assert html.count("Repair-1") >= 2


def test_top_nav_renamed_and_work_centers_dropped():
    client = TestClient(app)
    html = client.get("/recycling").text
    assert ">Dashboards<" in html
    # The top-nav "Work Centers" link is gone (subnav still has it)
    # We can assert by counting: there should be exactly one "Work Centers" string
    # (in the subnav).
    assert html.count("Work Centers") == 1
    assert ">Recycling VS<" in html
    assert ">New VS<" in html


def test_work_centers_subnav_active_on_index():
    client = TestClient(app)
    html = client.get("/").text
    # subnav appears on the index page
    assert ">Recycling VS<" in html
    assert ">New VS<" in html
    assert ">Work Centers<" in html
    # "Work Centers" tab is active
    import re
    m = re.search(r'class="[^"]*active[^"]*"[^>]*>\s*Work Centers', html)
    assert m, "Work Centers tab should be active on index page"
