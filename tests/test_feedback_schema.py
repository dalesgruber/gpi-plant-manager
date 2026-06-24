from zira_dashboard._schema import SCHEMA_DDL


def test_schema_defines_feedback_table():
    assert "CREATE TABLE IF NOT EXISTS feedback" in SCHEMA_DDL
    for col in (
        "id", "created_at", "submitter", "page_url", "category", "message",
    ):
        assert col in SCHEMA_DDL, f"missing column {col}"
