from pathlib import Path


def _template():
    return Path("src/zira_dashboard/templates/leaderboards.html").read_text()


def _script():
    return Path("src/zira_dashboard/static/leaderboards.js").read_text()


def test_leaderboard_section_visibility_buttons_name_the_section():
    html = _template()

    assert 'aria-label="Mark {{ s.loc_name }} {{ \'group\' if is_group else \'work center\' }} leaderboard inactive"' in html
    assert 'aria-label="Mark {{ s.loc_name }} {{ \'group\' if is_group else \'work center\' }} leaderboard active"' in html


def test_leaderboard_visibility_buttons_expose_busy_state():
    html = _template()
    js = _script()

    assert 'class="lb-hide-btn" title="Mark inactive" aria-busy="false"' in html
    assert 'class="lb-show-btn" title="Mark active" aria-busy="false"' in html
    assert "btn.disabled = true;" in js
    assert "btn.setAttribute('aria-busy', 'true');" in js
    assert "btn.disabled = false;" in js
    assert "btn.setAttribute('aria-busy', 'false');" in js
