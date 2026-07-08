from zira_dashboard import inbox_keys


def test_breakdown_key_without_person():
    assert inbox_keys.breakdown("Dismantler 2", "2026-07-08T18:02:00+00:00") == \
        "breakdown:Dismantler 2:2026-07-08T18:02:00+00:00"


def test_breakdown_key_with_person():
    assert inbox_keys.breakdown("Dismantler 2", "2026-07-08T18:02:00+00:00", "Juan") == \
        "breakdown:Dismantler 2:2026-07-08T18:02:00+00:00:Juan"
