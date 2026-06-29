"""The Inbox count badge is server-rendered into the top nav.

These render ``_topnav.html`` directly through the shared Jinja env with a
stubbed ``nav_inbox_summary`` global, so the badge + bootstrap are present in
the page HTML the instant it paints (no client-side flash on navigation).
No DB needed — the summary is stubbed.
"""

from __future__ import annotations

from zira_dashboard.deps import templates


def _render(summary, active_nav="staffing", monkeypatch=None):
    monkeypatch.setitem(templates.env.globals, "nav_inbox_summary", lambda: summary)
    return templates.env.get_template("_topnav.html").render(active_nav=active_nav)


def test_open_items_render_count_and_classes(monkeypatch):
    html = _render(
        {"total": 3, "urgent_total": 1, "source_errors": []}, monkeypatch=monkeypatch
    )
    # Badge spans are in the server HTML, with the count shown.
    assert 'class="inbox-nav-label"' in html
    assert "inbox-nav-count" in html
    assert ">3<" in html
    # State classes mirror updateInboxSummaryLink() in footer.js.
    assert "has-open" in html
    assert "has-urgent" in html
    # Bootstrap blob is present on the page and reflects the summary, so
    # footer.js adopts it instantly instead of the 650ms delayed fetch.
    assert 'id="gpi-inbox-summary-bootstrap"' in html
    assert '"total": 3' in html
    assert '"urgent_total": 1' in html


def test_all_clear_hides_count(monkeypatch):
    html = _render(
        {"total": 0, "urgent_total": 0, "source_errors": []}, monkeypatch=monkeypatch
    )
    assert "has-open" not in html
    assert "has-urgent" not in html
    # The count span still exists (so footer.js adopts it) but is hidden.
    assert '<span class="inbox-nav-count" hidden' in html
    # Bootstrap still emitted with a zero total.
    assert 'id="gpi-inbox-summary-bootstrap"' in html
    assert '"total": 0' in html


def test_degraded_shows_bang(monkeypatch):
    html = _render(
        {"total": 0, "urgent_total": 0, "source_errors": [{"source": "Late / Absence"}]},
        monkeypatch=monkeypatch,
    )
    assert "is-degraded" in html
    # Degraded with no open items shows "!" instead of a number, not hidden.
    assert ">!<" in html


def test_active_nav_marks_inbox_active(monkeypatch):
    html = _render(
        {"total": 0, "urgent_total": 0, "source_errors": []},
        active_nav="inbox",
        monkeypatch=monkeypatch,
    )
    assert "active" in html
