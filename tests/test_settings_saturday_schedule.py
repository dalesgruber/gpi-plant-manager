"""Settings route for the Saturday default schedule. Postgres-backed."""
import os
from datetime import time
import pytest
from fastapi.testclient import TestClient
from zira_dashboard.app import app
from zira_dashboard import db, saturday_schedule_store

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    db.execute("DELETE FROM saturday_schedule WHERE id = 1")
    saturday_schedule_store.reload()
    yield
    db.execute("DELETE FROM saturday_schedule WHERE id = 1")
    saturday_schedule_store.reload()


def test_get_settings_renders_saturday_panel():
    r = client.get("/settings")
    assert r.status_code == 200
    assert "Saturday Default" in r.text


def test_post_saves_saturday_schedule():
    r = client.post("/settings/saturday_schedule", data={
        "shift_start": "06:00", "shift_end": "12:00",
        "break_start_0": "08:00", "break_end_0": "08:15", "break_name_0": "Morning break",
        "break_start_1": "10:00", "break_end_1": "10:30", "break_name_1": "Lunch",
    }, headers={"accept": "application/json"})
    assert r.status_code == 200
    saturday_schedule_store.reload()
    s = saturday_schedule_store.current()
    assert s.shift_start == time(6, 0)
    assert s.shift_end == time(12, 0)
    assert len(s.breaks) == 2


def test_post_end_before_start_keeps_previous_end():
    client.post("/settings/saturday_schedule",
                data={"shift_start": "06:00", "shift_end": "12:00"},
                headers={"accept": "application/json"})
    saturday_schedule_store.reload()
    client.post("/settings/saturday_schedule",
                data={"shift_start": "09:00", "shift_end": "08:00"},
                headers={"accept": "application/json"})
    saturday_schedule_store.reload()
    s = saturday_schedule_store.current()
    assert s.shift_end > s.shift_start  # fell back, did not persist end<=start
