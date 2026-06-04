"""Tests for the /admin/ribbon-vs-widget read-only diagnostic.

Decomposes the per-PERSON ribbon total (production_daily) vs the
per-WORK-CENTER widget total (zira_daily_cache) for one day. Monkeypatches
``db.query`` so it runs with no database (locally + CI).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from zira_dashboard.app import app
    return TestClient(app)


def _fake_query(pd_rows, cache_rows):
    def query(sql, params=None):
        if "production_daily" in sql:
            return pd_rows
        if "zira_daily_cache" in sql:
            return cache_rows
        raise AssertionError(f"unexpected query: {sql}")
    return query


def test_groups_person_total_across_work_centers(client, monkeypatch):
    # Jose worked two stations that day: Repair 3 (763) + Dismantler 4 (110).
    # His ribbon total is the SUM (873); the Repair-3 widget bar is just 763.
    pd_rows = [
        {"name": "Jose Ochoa", "wc_name": "Repair 3", "units": 763.0,
         "hours": 7.0, "downtime": 0.0, "computed_at": "2026-06-02T20:00:00+00:00"},
        {"name": "Jose Ochoa", "wc_name": "Dismantler 4", "units": 110.0,
         "hours": 1.0, "downtime": 0.0, "computed_at": "2026-06-02T20:00:00+00:00"},
        {"name": "Jesus Galindo", "wc_name": "Repair 1", "units": 729.0,
         "hours": 7.0, "downtime": 0.0, "computed_at": "2026-06-02T20:00:00+00:00"},
    ]
    cache_rows = [
        {"meter_id": "40719", "computed_at": "2026-06-02T23:30:00+00:00",
         "payload": {"station": {"name": "Repair 3", "category": "Repair"},
                     "units": 763}},
        {"meter_id": "40721", "computed_at": "2026-06-02T23:30:00+00:00",
         "payload": {"station": {"name": "Repair 1", "category": "Repair"},
                     "units": 729}},
    ]
    monkeypatch.setattr("zira_dashboard.db.query", _fake_query(pd_rows, cache_rows))

    r = client.get("/admin/ribbon-vs-widget?day=2026-06-02")
    assert r.status_code == 200
    body = r.json()
    assert body["day"] == "2026-06-02"

    people = {p["name"]: p for p in body["ribbon_by_person"]}
    jose = people["Jose Ochoa"]
    assert jose["ribbon_total_units"] == 873.0  # 763 + 110 across two WCs
    assert {w["wc_name"] for w in jose["per_wc"]} == {"Repair 3", "Dismantler 4"}
    # ranked by total desc -> Jose (873) ahead of Jesus (729)
    assert body["ribbon_by_person"][0]["name"] == "Jose Ochoa"

    widget = {w["wc_name"]: w for w in body["widget_by_wc"]}
    assert widget["Repair 3"]["units"] == 763
    # the gap itself: ribbon 873 vs widget 763 == 110 credited on a 2nd station
    assert jose["ribbon_total_units"] - widget["Repair 3"]["units"] == 110.0


def test_parses_jsonb_payload_delivered_as_string(client, monkeypatch):
    # Some psycopg2 configs hand back JSONB as a string rather than a dict.
    import json
    cache_rows = [
        {"meter_id": "40720", "computed_at": None,
         "payload": json.dumps({"station": {"name": "Repair 2",
                                            "category": "Repair"}, "units": 671})},
    ]
    monkeypatch.setattr("zira_dashboard.db.query", _fake_query([], cache_rows))

    r = client.get("/admin/ribbon-vs-widget?day=2026-06-02")
    assert r.status_code == 200
    widget = {w["wc_name"]: w for w in r.json()["widget_by_wc"]}
    assert widget["Repair 2"]["units"] == 671


def test_defaults_to_yesterday_utc(client, monkeypatch):
    seen: dict = {}

    def query(sql, params=None):
        if "production_daily" in sql:
            seen["day"] = params[0]
        return []

    monkeypatch.setattr("zira_dashboard.db.query", query)
    r = client.get("/admin/ribbon-vs-widget")
    assert r.status_code == 200
    assert seen["day"] == datetime.now(timezone.utc).date() - timedelta(days=1)


def test_rejects_malformed_day(client):
    # Validated before any DB access, so no monkeypatch needed.
    r = client.get("/admin/ribbon-vs-widget?day=not-a-date")
    assert r.status_code == 400
