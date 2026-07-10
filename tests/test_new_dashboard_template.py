from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "zira_dashboard" / "templates" / "new_dept.html"


def _html():
    return TEMPLATE.read_text(encoding="utf-8")


def test_new_has_full_recycling_range_toolbar():
    html = _html()
    for label in (
        "Today", "Yesterday", "This Week", "Last Week",
        "This Month", "Last Month", "Custom",
    ):
        assert label in html
    assert '<form class="rc-toolbar"' in html
    assert '<div class="edit-bar">' in html


def test_new_is_independent_editable_gridstack_page():
    html = _html()
    assert "/static/vendor/gridstack.min.css" in html
    assert "/static/vendor/gridstack-all.js" in html
    assert "/static/dashboard-grid.js" in html
    assert 'data-layout-page="new"' in html
    assert 'id="reset-layout"' in html


def test_new_default_layout_matches_reference():
    html = _html()
    expected = {
        "kpi-pallets": (0, 0, 2, 3),
        "kpi-palletshr": (0, 3, 2, 3),
        "new-bars": (2, 0, 5, 6),
        "downtime-report": (7, 0, 5, 6),
        "new-progress": (0, 6, 12, 5),
        "new-cumulative": (0, 11, 12, 5),
    }
    for widget_id, defaults in expected.items():
        assert f"widget_attrs('{widget_id}', {', '.join(map(str, defaults))})" in html


def test_new_daily_progress_is_cumulative_bars_and_no_stop_widget():
    html = _html()
    assert "cumulative_progress_chart(new_progress)" in html
    assert "Unplanned Stops" not in html


def test_new_keeps_shared_dashboard_surfaces_and_refresh_behavior():
    html = _html()
    for surface in (
        'include "_topnav.html"',
        'include "_dashboards_subnav.html"',
        'include "_goat_watch_banner.html"',
        '/static/assign-popover.js',
        '/static/tv-refresh.js',
        "include '_footer.html'",
    ):
        assert surface in html
    assert "tv_mode or range_includes_today" in html
