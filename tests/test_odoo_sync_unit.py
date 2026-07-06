from zira_dashboard import odoo_sync


def test_merge_legacy_skill_into_stable_moves_dependencies_before_delete():
    calls = []

    class FakeCursor:
        def execute(self, sql, params=None):
            calls.append((" ".join(sql.split()), params))

    odoo_sync._merge_legacy_skill_into_stable(
        FakeCursor(),
        stable_skill_id=10,
        legacy_skill_id=20,
    )

    assert "DELETE FROM person_skills" in calls[0][0]
    assert "INSERT INTO person_skills" in calls[0][0]
    assert calls[0][1] == (20, 10)
    assert "INSERT INTO work_center_required_skills" in calls[1][0]
    assert calls[1][1] == (10, 20)
    assert calls[2] == (
        "DELETE FROM work_center_required_skills WHERE skill_id = %s",
        (20,),
    )
    assert calls[3] == ("DELETE FROM skills WHERE id = %s", (20,))
