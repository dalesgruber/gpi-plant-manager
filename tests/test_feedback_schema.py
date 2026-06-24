from zira_dashboard._schema import SCHEMA_DDL


def test_schema_defines_feedback_table():
    assert "CREATE TABLE IF NOT EXISTS feedback" in SCHEMA_DDL
    for col in (
        "id", "created_at", "submitter", "page_url", "category", "message",
        "task_type", "odoo_task_id",
    ):
        assert col in SCHEMA_DDL, f"missing column {col}"


def test_schema_has_idempotent_alters_for_new_feedback_columns():
    assert "ADD COLUMN IF NOT EXISTS task_type" in SCHEMA_DDL
    assert "ADD COLUMN IF NOT EXISTS odoo_task_id" in SCHEMA_DDL
