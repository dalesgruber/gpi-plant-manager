from zira_dashboard.schedule_solver import (
    CandidateEdge,
    CandidateRejection,
    CenterRequirement,
    CrewOption,
    solve_minimum_coverage,
)


def edge(person, center, *, level=1, never=False, rank=0):
    return CandidateEdge(
        person=person,
        center=center,
        level=level,
        preference="never" if never else "regular",
        rank_cost=rank,
    )


def test_cross_trained_person_moves_to_scarce_center_and_backfills_old_role():
    result = solve_minimum_coverage((
        CenterRequirement(
            center="Repair 2",
            group="Repair",
            remaining_slots=1,
            candidates=(
                edge("Jose Luis", "Repair 2", level=3),
                edge("Domingo Recinos", "Repair 2", level=1),
            ),
        ),
        CenterRequirement(
            center="Dismantler 1",
            group="Dismantler",
            remaining_slots=1,
            candidates=(edge("Jose Luis", "Dismantler 1", level=1),),
        ),
    ))

    assert {(d.center, d.person) for d in result.decisions} == {
        ("Repair 2", "Domingo Recinos"),
        ("Dismantler 1", "Jose Luis"),
    }
    assert result.unresolved_centers == ()


def test_never_is_used_only_when_it_increases_staffed_center_count():
    required = (
        CenterRequirement(
            center="Repair 1",
            group="Repair",
            remaining_slots=1,
            candidates=(
                edge("Regular", "Repair 1", level=1),
                edge("Never", "Repair 1", level=3, never=True),
            ),
        ),
        CenterRequirement(
            center="Dismantler 1",
            group="Dismantler",
            remaining_slots=1,
            candidates=(edge("Never", "Dismantler 1", level=3, never=True),),
        ),
    )

    result = solve_minimum_coverage(required)

    assert {(d.center, d.person) for d in result.decisions} == {
        ("Repair 1", "Regular"),
        ("Dismantler 1", "Never"),
    }
    override = next(d for d in result.decisions if d.person == "Never")
    assert override.reason_code == "preference_override"


def test_equal_results_are_stable_across_input_order():
    first = CenterRequirement(
        center="Repair 1",
        group="Repair",
        remaining_slots=1,
        candidates=(edge("B", "Repair 1"), edge("A", "Repair 1")),
    )
    second = CenterRequirement(
        center="Dismantler 1",
        group="Dismantler",
        remaining_slots=1,
        candidates=(edge("B", "Dismantler 1"), edge("A", "Dismantler 1")),
    )

    forward = solve_minimum_coverage((first, second))
    reverse = solve_minimum_coverage((second, first))

    assert forward.decisions == reverse.decisions
    assert len(forward.staffed_centers) == 2


def test_multi_person_center_is_either_complete_or_empty():
    a = edge("A", "Hand Build #1", level=2)
    b = edge("B", "Hand Build #1", level=2)
    only_a_elsewhere = edge("A", "Repair 1", level=2)
    result = solve_minimum_coverage((
        CenterRequirement(
            center="Hand Build #1",
            group="Hand Build",
            remaining_slots=2,
            crew_options=(CrewOption("Hand Build #1", (a, b)),),
        ),
        CenterRequirement(
            center="Repair 1",
            group="Repair",
            remaining_slots=1,
            candidates=(only_a_elsewhere,),
        ),
    ))

    assert result.staffed_centers == ("Hand Build #1",)
    assert {(item.center, item.person) for item in result.decisions} == {
        ("Hand Build #1", "A"),
        ("Hand Build #1", "B"),
    }
    assert result.unresolved_centers == ("Repair 1",)


def test_protected_safe_minimum_needs_no_generated_assignment():
    result = solve_minimum_coverage((CenterRequirement(
        center="Repair 1",
        group="Repair",
        remaining_slots=0,
        protected_people=("Manual",),
    ),))

    assert result.staffed_centers == ("Repair 1",)
    assert result.decisions == ()


def test_level_zero_only_reports_training_required_without_assigning_anyone():
    result = solve_minimum_coverage((CenterRequirement(
        center="Dismantler 1",
        group="Dismantler",
        remaining_slots=1,
        level_zero_people=("Potential Trainee",),
        rejections=(CandidateRejection(
            person="Potential Trainee",
            code="level_zero",
            detail="Skill level is 0; an active training block is required.",
        ),),
    ),))

    assert result.decisions == ()
    assert result.issues[0].code == "training_required"
    assert result.issues[0].message == (
        "Dismantler 1 could not be staffed. Training is required for Dismantler."
    )


def test_best_safe_partial_leaves_unresolved_centers_enabled_in_result():
    result = solve_minimum_coverage((
        CenterRequirement(
            center="Repair 1",
            group="Repair",
            remaining_slots=1,
            candidates=(edge("Only Person", "Repair 1"),),
        ),
        CenterRequirement(
            center="Dismantler 1",
            group="Dismantler",
            remaining_slots=1,
            candidates=(edge("Only Person", "Dismantler 1"),),
        ),
    ))

    assert len(result.staffed_centers) == 1
    assert len(result.unresolved_centers) == 1
    assert len(result.decisions) == 1
    assert result.issues[0].code == "insufficient_qualified_headcount"
