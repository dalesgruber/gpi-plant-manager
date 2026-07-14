import os

import pytest

from zira_dashboard import db, work_centers_store


def test_normalize_default_targets_rejects_cross_target_duplicates():
    with pytest.raises(work_centers_store.InvalidDefaultTargets) as caught:
        work_centers_store._normalize_default_targets(
            exact_by_center={"Repair 1": ["Ana"], "Repair 2": ["Luis"]},
            group_by_name={"Repair": ["Ana"]},
        )

    assert caught.value.conflicts == {
        "Ana": ("group:Repair", "work_center:Repair 1"),
    }


def test_normalize_default_targets_cleans_and_preserves_order():
    exact, groups = work_centers_store._normalize_default_targets(
        exact_by_center={"Repair 1": [" Ana ", "", "Ana", "Luis"]},
        group_by_name={"Repair": [" Zoe ", "Zoe"]},
    )

    assert exact == {"Repair 1": ("Ana", "Luis")}
    assert groups == {"Repair": ("Zoe",)}


pytestmark_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


@pytestmark_db
def test_group_defaults_follow_rename_and_delete():
    person_name = "__group_default_test_person__"
    old_group = "__group_default_old__"
    new_group = "__group_default_new__"
    db.execute(
        "INSERT INTO people (name, active, excluded) VALUES (%s, TRUE, FALSE) "
        "ON CONFLICT (name) DO UPDATE SET active = TRUE, excluded = FALSE",
        (person_name,),
    )
    work_centers_store.add_group(old_group)
    try:
        work_centers_store.replace_default_targets(
            exact_by_center={}, group_by_name={old_group: [person_name]}
        )
        assert work_centers_store.group_default_people(old_group) == [person_name]

        work_centers_store.rename_group(old_group, new_group)
        assert work_centers_store.group_default_people(old_group) == []
        assert work_centers_store.group_default_people(new_group) == [person_name]

        work_centers_store.delete_group(new_group)
        assert work_centers_store.group_default_people(new_group) == []
    finally:
        db.execute("DELETE FROM groups WHERE name IN (%s, %s)", (old_group, new_group))
        db.execute("DELETE FROM people WHERE name = %s", (person_name,))
        work_centers_store._invalidate_caches()
