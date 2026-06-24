from pathlib import Path


def _template():
    return Path("src/zira_dashboard/templates/staffing.html").read_text()


def _script():
    return Path("src/zira_dashboard/static/staffing.js").read_text()


def test_staffing_partial_time_off_controls_name_the_person():
    html = _template()
    js = _script()

    assert 'aria-label="Clear partial time off for {{ n }}"' in html
    assert 'aria-label="Clear partial time off for {{ e.name }}"' in html
    assert 'aria-label="Restore partial time off for {{ c.name }}"' in html
    assert 'aria-label="Clear partial time off for {{ a.name }}"' in html
    assert "btn.setAttribute('aria-label', 'Clear partial time off for ' + name);" in js
