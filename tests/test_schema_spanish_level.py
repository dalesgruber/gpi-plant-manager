import os

import pytest

from zira_dashboard import db


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


def test_people_has_exact_spanish_level_bucket():
    db.bootstrap_schema()
    rows = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'people'"
    )
    assert "spanish_level" in {row["column_name"] for row in rows}
