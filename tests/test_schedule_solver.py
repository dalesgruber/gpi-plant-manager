from zira_dashboard.schedule_solver import (
    CandidateEdge,
    CenterRequirement,
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
