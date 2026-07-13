import os
from datetime import date, datetime, timedelta, timezone

import pytest

from zira_dashboard import db, late_report

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


@pytest.fixture
def day():
    value = date(2099, 7, 13)
    db.bootstrap_schema()
    db.execute("DELETE FROM late_expected_arrivals WHERE day = %s", (value,))
    yield value
    db.execute("DELETE FROM late_expected_arrivals WHERE day = %s", (value,))


def test_expected_arrival_upserts_and_lists_only_future_row(day):
    arrival = datetime.now(timezone.utc) + timedelta(minutes=45)
    late_report.set_expected_arrival(day, "7", "Jesus Galindo", arrival)
    late_report.set_expected_arrival(day, "7", "Jesus G.", arrival + timedelta(minutes=15))

    assert late_report.active_expected_arrivals(day) == [{
        "emp_id": "7",
        "name": "Jesus G.",
        "expected_at_utc": arrival + timedelta(minutes=15),
    }]


def test_clear_expected_arrival_removes_employee(day):
    late_report.set_expected_arrival(
        day, "7", "Jesus Galindo", datetime.now(timezone.utc) + timedelta(minutes=45)
    )

    late_report.clear_expected_arrival(day, "7")

    assert late_report.active_expected_arrivals(day) == []
