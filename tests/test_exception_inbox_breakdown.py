"""The breakdown section appears in build_summary/build_snapshot."""
from zira_dashboard import exception_inbox


def test_build_summary_includes_breakdown_count(monkeypatch):
    from zira_dashboard import machine_breakdown
    monkeypatch.setattr(machine_breakdown, "current_rows", lambda: [
        {"name": "Dismantler 2", "action": None},
        {"name": "Juan", "action": {"type": "breakdown"}},
    ])
    summary = exception_inbox.build_summary()
    assert summary["sections"]["breakdown"] == 2


def test_build_snapshot_includes_breakdown_section_and_rows(monkeypatch):
    from zira_dashboard import machine_breakdown
    row = {
        "name": "Dismantler 2", "label": "Stopped producing", "detail": "No output since 1:02 PM (23 min)",
        "priority": "urgent", "badge": "AUTO-DETECTED",
        "row_key": "breakdown_header:Dismantler 2:x", "item_key": "breakdown:Dismantler 2:x",
        "action": None, "dismiss_action": {"type": "breakdown_dismiss", "incident_id": 1},
    }
    monkeypatch.setattr(machine_breakdown, "current_rows", lambda: [row])
    snapshot = exception_inbox.build_snapshot()
    section = next(s for s in snapshot["sections"] if s["id"] == "breakdown")
    assert section["rows"] == [row]
    assert section["count"] == 1
    queue_item_keys = [r["item_key"] for r in snapshot["queue"]]
    assert "breakdown:Dismantler 2:x" in queue_item_keys
