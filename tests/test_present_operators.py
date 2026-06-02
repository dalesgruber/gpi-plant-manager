from zira_dashboard import staffing


def test_present_operators_excludes_full_day_off():
    assigned = [{"name": "Ana", "level": 3}, {"name": "Bob", "level": 2}]
    assert staffing.present_operators(assigned, {"Bob"}) == [{"name": "Ana", "level": 3}]


def test_present_operators_empty_off_set_returns_all():
    assigned = [{"name": "Ana", "level": 3}]
    assert staffing.present_operators(assigned, set()) == assigned


def test_present_operators_all_off_returns_empty():
    assigned = [{"name": "Ana", "level": 3}]
    assert staffing.present_operators(assigned, {"Ana"}) == []
