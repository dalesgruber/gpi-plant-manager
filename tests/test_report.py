from zira_probe.report import render_report
from zira_probe.results import ProbeResult


def test_render_report_includes_all_categories_and_status_glyphs():
    results = [
        ProbeResult(
            name="read_ds_single_window[DS1]",
            category="reads",
            endpoint="GET /reading",
            status="success",
            observations=["Returned 42 readings."],
        ),
        ProbeResult(
            name="write_single_number",
            category="writes_happy",
            endpoint="POST /reading/ids/",
            status="unexpected_failure",
            observations=["Write errored: HTTPError 400."],
        ),
        ProbeResult(
            name="undoc_get_data-sources",
            category="undocumented",
            endpoint="GET /data-sources",
            status="success",
            observations=["GET /data-sources → 200 (INTERESTING)"],
        ),
        ProbeResult(
            name="write_bad_meter_id",
            category="writes_error_surface",
            endpoint="POST /reading/ids/",
            status="success",
            observations=["Rejected with 404 — as expected."],
        ),
    ]

    md = render_report(results)

    assert "# Zira API Capability Report" in md
    assert "## Reads" in md
    assert "## Writes — Happy Path" in md
    assert "## Writes — Error Surface" in md
    assert "## Undocumented Endpoints" in md
    assert "✅" in md  # success glyph used somewhere
    assert "❌" in md  # unexpected_failure glyph
    assert "INTERESTING" in md
    # All four probe names must appear in the doc
    for r in results:
        assert r.name in md


def test_render_report_lists_open_questions_when_undocumented_was_interesting():
    interesting = ProbeResult(
        name="undoc_get_data-sources",
        category="undocumented",
        endpoint="GET /data-sources",
        status="success",
        observations=["GET /data-sources → 200 (INTERESTING)"],
    )
    md = render_report([interesting])
    assert "Open Questions" in md
    assert "data-sources" in md
