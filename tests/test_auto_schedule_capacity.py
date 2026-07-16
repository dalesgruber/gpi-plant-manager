from zira_dashboard.auto_schedule_capacity import analyze_auto_expansion


def test_minimum_crew_balance_recommends_turning_off_smallest_open_center():
    from zira_dashboard.auto_schedule_capacity import analyze_minimum_crew_balance

    result = analyze_minimum_crew_balance(
        unassigned_people=3,
        enabled_centers=("One", "Two", "Three"),
        disabled_centers=("Four",),
        open_minimum_slots_by_center={"One": 2, "Two": 1, "Three": 1, "Four": 2},
        center_order={"One": 0, "Two": 1, "Three": 2, "Four": 3},
    )

    assert result.open_minimum_slots == 4
    assert result.direction == "turn_off"
    assert result.slot_delta == 1
    assert result.center_count == 1
    assert result.recommended_centers == ("Two",)


def test_minimum_crew_balance_recommends_turning_on_fewest_centers():
    from zira_dashboard.auto_schedule_capacity import analyze_minimum_crew_balance

    result = analyze_minimum_crew_balance(
        unassigned_people=5,
        enabled_centers=("One",),
        disabled_centers=("Two", "Three"),
        open_minimum_slots_by_center={"One": 2, "Two": 2, "Three": 3},
        center_order={"One": 0, "Two": 1, "Three": 2},
    )

    assert result.direction == "turn_on"
    assert result.slot_delta == 3
    assert result.center_count == 1
    assert result.recommended_centers == ("Three",)


def test_minimum_crew_balance_is_ready_when_slots_match_people_waiting():
    from zira_dashboard.auto_schedule_capacity import analyze_minimum_crew_balance

    result = analyze_minimum_crew_balance(
        unassigned_people=3,
        enabled_centers=("One", "Two"),
        disabled_centers=(),
        open_minimum_slots_by_center={"One": 1, "Two": 2},
        center_order={"One": 0, "Two": 1},
    )

    assert result.direction == "ready"
    assert result.center_count == 0
    assert result.slot_delta == 0
    assert result.recommended_centers == ()


def test_expansion_uses_largest_open_centers_to_minimize_toggle_count():
    result = analyze_auto_expansion(
        unassigned_people=4,
        disabled_centers=("One", "Two", "Three"),
        open_slots_by_center={"One": 1, "Two": 3, "Three": 2},
        center_order={"One": 0, "Two": 1, "Three": 2},
    )

    assert result.unassigned_people == 4
    assert result.centers_to_enable == 2
    assert result.usable_centers == ("Two", "Three", "One")


def test_expansion_reports_no_count_when_all_disabled_capacity_is_insufficient():
    result = analyze_auto_expansion(
        unassigned_people=4,
        disabled_centers=("One", "Two"),
        open_slots_by_center={"One": 1, "Two": 2},
        center_order={"One": 0, "Two": 1},
    )

    assert result.unassigned_people == 4
    assert result.centers_to_enable is None
    assert result.usable_centers == ("Two", "One")
