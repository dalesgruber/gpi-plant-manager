"""`time_off_requests.local_record` — the poller-proof flag behind
"record the absence locally when Odoo rejects it for a Working Schedule
conflict". Schema is asserted at the DDL-string level (no Postgres needed),
matching test_schema_employee_notifications.py."""
from zira_dashboard._schema import SCHEMA_DDL


def test_schema_defines_local_record_column():
    # Fresh databases get the column from CREATE TABLE; live databases get
    # it from the idempotent ALTER (bootstrap_schema never reconciles
    # columns inside an existing table).
    assert SCHEMA_DDL.count("local_record") >= 2
    assert (
        "ALTER TABLE time_off_requests ADD COLUMN IF NOT EXISTS "
        "local_record BOOLEAN NOT NULL DEFAULT FALSE" in SCHEMA_DDL
    )
