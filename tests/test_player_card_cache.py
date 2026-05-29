import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="player-card cache test needs a live DATABASE_URL",
)


@pytest.fixture
def client():
    from zira_dashboard import db
    db.init_pool()
    db.bootstrap_schema()
    from zira_dashboard.app import app
    return TestClient(app)


def test_player_card_serves_from_cache(client, monkeypatch):
    # Any name renders (empty data is fine — it 200s with zeros). Use a
    # genuinely past range (end < today) so it lands in the 5-min past
    # bucket and is immutable for the test.
    r1 = client.get("/staffing/people/Nobody?start=2025-01-01&end=2025-01-31")
    assert r1.status_code == 200

    # A second GET must hit the cache and not recompute attribution.
    from zira_dashboard import production_history

    def _poison(*a, **k):
        raise AssertionError("attribution_range called — player card not cached")

    monkeypatch.setattr(production_history, "attribution_range", _poison)
    r2 = client.get("/staffing/people/Nobody?start=2025-01-01&end=2025-01-31")
    assert r2.status_code == 200
    assert r2.content == r1.content
