"""Unit tests for the pure planner in scripts.normalize_odoo_timezones."""

from scripts import normalize_odoo_timezones as tz


def test_skips_records_already_canonical():
    rows = [
        {"id": 1, "name": "A", "tz": "America/Chicago"},
        {"id": 2, "name": "B", "tz": "America/Chicago"},
    ]
    assert tz.rows_to_normalize(rows) == []


def test_flags_utc_and_legacy_alias_and_blank():
    # UTC (Odoo default for new employees), the US/Central legacy alias, and
    # a blank tz on a template/system user all get normalized.
    rows = [
        {"id": 1, "name": "utc", "tz": "UTC"},
        {"id": 2, "name": "alias", "tz": "US/Central"},
        {"id": 3, "name": "blank", "tz": False},
        {"id": 4, "name": "none", "tz": None},
        {"id": 5, "name": "ok", "tz": "America/Chicago"},
    ]
    assert [r["id"] for r in tz.rows_to_normalize(rows)] == [1, 2, 3, 4]


def test_custom_canonical_zone_is_honored():
    rows = [
        {"id": 1, "tz": "America/Chicago"},
        {"id": 2, "tz": "America/Denver"},
    ]
    assert [r["id"] for r in tz.rows_to_normalize(rows, canon="America/Denver")] == [1]
