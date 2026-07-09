"""Date-aware navigation between recycling and operator dashboards."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from zira_dashboard.app import app
from zira_dashboard.deps import templates


ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_GRID_JS = (ROOT / "src/zira_dashboard/static/dashboard-grid.js").read_text()


def _stub_operator_dashboard(monkeypatch):
    from zira_dashboard import layout_store, widget_customizer, work_centers_store
    from zira_dashboard import staffing, wc_dashboard_data
    from zira_dashboard.routes import wc_dashboard

    target_day = date(2026, 5, 14)
    today = date(2026, 7, 9)
    calls: dict[str, object] = {}

    class _Loc:
        name = "Repair 1"
        meter_id = "meter-1"
        skill = "Repair"
        bay = "Bay 1"

    loc = _Loc()

    monkeypatch.setattr(wc_dashboard, "plant_today", lambda: today)
    monkeypatch.setattr(wc_dashboard, "_goat_watch_active_alerts", lambda d: [])
    monkeypatch.setattr(staffing, "LOCATIONS", [loc])
    monkeypatch.setattr(wc_dashboard_data, "wc_by_slug", lambda s: loc if s == "repair-1" else None)
    monkeypatch.setattr(work_centers_store, "groups", lambda _loc: ["Repairs"])
    monkeypatch.setattr(layout_store, "layout_map", lambda page: {})
    monkeypatch.setattr(widget_customizer, "load_all", lambda page: {})
    monkeypatch.setitem(
        templates.env.globals,
        "nav_inbox_summary",
        lambda: {"total": 0, "urgent_total": 0, "source_errors": []},
    )
    monkeypatch.setitem(templates.env.globals, "goat_holders", lambda: {})

    def _record(name):
        def inner(*args):
            calls.setdefault(name, []).append(args[-1])
            if name == "assigned":
                return ["Domingo R"]
            if name == "pallets":
                return {
                    "units_today": 42,
                    "target_today": 50,
                    "target_full_day": 100,
                    "pct_of_target": 84.0,
                }
            if name == "progress":
                return {"buckets": [], "bucket_target": 0}
            if name == "kpi":
                return {
                    "units_today": 42,
                    "downtime_minutes": 0,
                    "hours_elapsed": 8.0,
                    "up_time_pct": 100.0,
                    "pallets_per_hour": 5.2,
                }
            if name == "report":
                return {"events": [], "total_minutes": 0}
            if name == "goat":
                return {"group": "Repairs", "goat": None, "units_today": 42, "goat_pace_today": 0, "status": None}
            raise AssertionError(name)

        return inner

    monkeypatch.setattr(wc_dashboard_data, "assigned_operators_for_wc", _record("assigned"))
    monkeypatch.setattr(wc_dashboard_data, "pallets_banner", _record("pallets"))
    monkeypatch.setattr(wc_dashboard_data, "fifteen_min_progress_buckets", _record("progress"))
    monkeypatch.setattr(wc_dashboard_data, "kpi_tiles", _record("kpi"))
    monkeypatch.setattr(wc_dashboard_data, "downtime_report", _record("report"))
    monkeypatch.setattr(wc_dashboard_data, "goat_race", _record("goat"))
    monkeypatch.setattr(wc_dashboard_data, "monthly_ribbons", lambda nm, y, m: {"group": "Repairs", "entries": []})

    return target_day, calls


def test_operator_dashboard_uses_requested_day(monkeypatch):
    target_day, calls = _stub_operator_dashboard(monkeypatch)

    response = TestClient(app).get("/wc/repair-1?day=2026-05-14")

    assert response.status_code == 200
    for key in ("assigned", "pallets", "progress", "kpi", "report", "goat"):
        assert calls[key] == [target_day]


def test_operator_default_preserves_requested_day(monkeypatch):
    from zira_dashboard import staffing, wc_dashboard_data
    from zira_dashboard.routes import wc_dashboard

    target_day = date(2026, 5, 14)
    seen = {}

    class _Loc:
        name = "Repair 1"

    loc = _Loc()
    monkeypatch.setattr(wc_dashboard, "plant_today", lambda: date(2026, 7, 9))
    monkeypatch.setattr(staffing, "LOCATIONS", [loc])
    monkeypatch.setattr(
        wc_dashboard_data,
        "assigned_operators_for_wc",
        lambda name, day: (seen.setdefault("day", day) or ["Domingo R"]),
    )

    response = TestClient(app).get("/operator?day=2026-05-14", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/wc/repair-1?day=2026-05-14"
    assert seen["day"] == target_day


def test_wc_picker_preserves_operator_day_query():
    assert "URLSearchParams(window.location.search)" in DASHBOARD_GRID_JS
    assert "searchParams.set('day', day)" in DASHBOARD_GRID_JS
