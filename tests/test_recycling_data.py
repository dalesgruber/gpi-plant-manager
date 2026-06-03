"""Characterization tests for zira_dashboard.recycling_data.

These pin the EXACT current behavior of the pure helpers extracted from
routes/departments.py, so a future refactor cannot silently change a
dashboard color (or any other computed value). Pure -- no backend needed.
"""

from zira_dashboard import recycling_data as rd


def test_progress_color_none_and_on_goal_band():
    # None in -> None out.
    assert rd.progress_color(None) is None
    # Within +/-1% of 100% -> neutral gray (the |delta| < 1.0 branch).
    assert rd.progress_color(100.0) == "#9ca3af"
    assert rd.progress_color(100.5) == "#9ca3af"
    assert rd.progress_color(99.5) == "#9ca3af"


def test_progress_color_ramps_and_clamps():
    # Just over/under the gray band: step 1 ramp. Green above, red below.
    assert rd.progress_color(101.0) == "hsl(130, 57%, 62%)"
    assert rd.progress_color(99.0) == "hsl(0, 57%, 62%)"
    # Mid ramp (50% off goal in either direction): step 6 bucket.
    assert rd.progress_color(150.0) == "hsl(130, 67%, 44%)"
    assert rd.progress_color(50.0) == "hsl(0, 67%, 44%)"
    # Far end of the ramp: step 12 bucket (delta clamps at +/-100).
    assert rd.progress_color(200.0) == "hsl(130, 79%, 23%)"
    assert rd.progress_color(0.0) == "hsl(0, 79%, 23%)"
    # Beyond the clamp stays pinned to the step-12 extreme.
    assert rd.progress_color(300.0) == "hsl(130, 79%, 23%)"
    assert rd.progress_color(-50.0) == "hsl(0, 79%, 23%)"
