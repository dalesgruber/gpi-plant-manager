from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

Preference = Literal["primary", "regular", "occasional", "never"]


@dataclass(frozen=True)
class CandidateRejection:
    person: str
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"person": self.person, "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class CandidateEdge:
    person: str
    center: str
    level: int
    preference: Preference
    rank_cost: int = 0

    @property
    def override_cost(self) -> int:
        return int(self.preference == "never")


@dataclass(frozen=True)
class CrewOption:
    center: str
    members: tuple[CandidateEdge, ...]

    @property
    def people(self) -> tuple[str, ...]:
        return tuple(member.person for member in self.members)


@dataclass(frozen=True)
class CenterRequirement:
    center: str
    group: str
    remaining_slots: int
    protected_people: tuple[str, ...] = ()
    candidates: tuple[CandidateEdge, ...] = ()
    crew_options: tuple[CrewOption, ...] = ()
    level_zero_people: tuple[str, ...] = ()
    rejections: tuple[CandidateRejection, ...] = ()
    unresolved_code: str = "insufficient_qualified_headcount"
    unresolved_message: str = ""


@dataclass(frozen=True)
class AssignmentDecision:
    center: str
    person: str
    level: int
    preference: Preference
    reason_code: str
    reason: str
    rank_cost: int


@dataclass(frozen=True)
class CoverageIssue:
    center: str
    group: str
    code: str
    message: str
    rejections: tuple[CandidateRejection, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "center": self.center,
            "group": self.group,
            "code": self.code,
            "message": self.message,
            "rejections": [item.to_dict() for item in self.rejections],
        }


@dataclass(frozen=True)
class CoverageResult:
    decisions: tuple[AssignmentDecision, ...]
    staffed_centers: tuple[str, ...]
    unresolved_centers: tuple[str, ...]
    issues: tuple[CoverageIssue, ...]

    @property
    def assigned_people(self) -> frozenset[str]:
        return frozenset(item.person for item in self.decisions)


@dataclass
class _Arc:
    to: int
    reverse: int
    capacity: int
    cost: int


def _add_arc(graph: list[list[_Arc]], start: int, end: int, capacity: int, cost: int) -> int:
    forward_index = len(graph[start])
    graph[start].append(_Arc(end, len(graph[end]), capacity, cost))
    graph[end].append(_Arc(start, forward_index, 0, -cost))
    return forward_index


def _decision(edge: CandidateEdge) -> AssignmentDecision:
    override = edge.preference == "never"
    return AssignmentDecision(
        center=edge.center,
        person=edge.person,
        level=edge.level,
        preference=edge.preference,
        reason_code="preference_override" if override else "minimum_coverage",
        reason=(
            "Assigned despite Never to meet minimum coverage."
            if override
            else "Assigned to meet minimum coverage."
        ),
        rank_cost=edge.rank_cost,
    )


def _match_single_requirements(
    requirements: Sequence[CenterRequirement],
    unavailable_people: frozenset[str] = frozenset(),
) -> tuple[AssignmentDecision, ...]:
    centers = tuple(sorted(requirements, key=lambda item: item.center.lower()))
    edges = tuple(sorted(
        (
            edge
            for requirement in centers
            for edge in requirement.candidates
            if edge.person not in unavailable_people
        ),
        key=lambda edge: (edge.person.lower(), edge.center.lower(), edge.rank_cost),
    ))
    people = tuple(sorted({edge.person for edge in edges}, key=str.lower))
    source = 0
    person_node = {name: index + 1 for index, name in enumerate(people)}
    center_offset = 1 + len(people)
    center_node = {item.center: center_offset + index for index, item in enumerate(centers)}
    sink = center_offset + len(centers)
    graph: list[list[_Arc]] = [[] for _ in range(sink + 1)]

    for person in people:
        _add_arc(graph, source, person_node[person], 1, 0)
    for requirement in centers:
        _add_arc(graph, center_node[requirement.center], sink, 1, 0)

    ordinary_bound = sum(max(0, edge.rank_cost) for edge in edges) + len(edges) + 1
    chosen_arcs: list[tuple[int, int, CandidateEdge]] = []
    for edge in edges:
        cost = edge.override_cost * ordinary_bound + max(0, edge.rank_cost)
        start = person_node[edge.person]
        arc_index = _add_arc(graph, start, center_node[edge.center], 1, cost)
        chosen_arcs.append((start, arc_index, edge))

    node_count = len(graph)
    while True:
        distance = [10**18] * node_count
        previous: list[tuple[int, int] | None] = [None] * node_count
        distance[source] = 0
        for _ in range(node_count - 1):
            changed = False
            for node, arcs in enumerate(graph):
                if distance[node] == 10**18:
                    continue
                for arc_index, arc in enumerate(arcs):
                    candidate = distance[node] + arc.cost
                    if arc.capacity and candidate < distance[arc.to]:
                        distance[arc.to] = candidate
                        previous[arc.to] = (node, arc_index)
                        changed = True
            if not changed:
                break
        if previous[sink] is None:
            break
        node = sink
        while node != source:
            prior_node, arc_index = previous[node]  # type: ignore[misc]
            arc = graph[prior_node][arc_index]
            arc.capacity -= 1
            graph[node][arc.reverse].capacity += 1
            node = prior_node

    selected = [
        _decision(edge)
        for start, arc_index, edge in chosen_arcs
        if graph[start][arc_index].capacity == 0
    ]
    return tuple(sorted(selected, key=lambda item: (item.center.lower(), item.person.lower())))


def _issue(
    requirement: CenterRequirement,
    decisions: Sequence[AssignmentDecision],
) -> CoverageIssue:
    if requirement.level_zero_people and not requirement.candidates and not requirement.crew_options:
        code = "training_required"
        message = (
            f"{requirement.center} could not be staffed. "
            f"Training is required for {requirement.group}."
        )
    else:
        code = requirement.unresolved_code
        message = requirement.unresolved_message or (
            f"{requirement.center} could not be staffed to its minimum."
        )
    assigned_center = {item.person: item.center for item in decisions}
    candidate_people = {
        edge.person
        for edge in requirement.candidates
    } | {
        edge.person
        for option in requirement.crew_options
        for edge in option.members
    }
    rejections = list(requirement.rejections)
    known = {(item.person, item.code) for item in rejections}
    for person in sorted(candidate_people, key=str.lower):
        other_center = assigned_center.get(person)
        if other_center and other_center != requirement.center:
            item = CandidateRejection(
                person=person,
                code="needed_elsewhere",
                detail=f"Used at {other_center} by the maximum-coverage solution.",
            )
        else:
            item = CandidateRejection(
                person=person,
                code="global_tie_break",
                detail="Available, but assigning them here would not improve total coverage.",
            )
        if (item.person, item.code) not in known:
            rejections.append(item)
    return CoverageIssue(
        center=requirement.center,
        group=requirement.group,
        code=code,
        message=message,
        rejections=tuple(rejections),
    )


def _assemble_result(
    requirements: Sequence[CenterRequirement],
    decisions: Sequence[AssignmentDecision],
) -> CoverageResult:
    assigned_centers = {item.center for item in decisions}
    staffed = {
        requirement.center
        for requirement in requirements
        if requirement.remaining_slots == 0 or requirement.center in assigned_centers
    }
    unresolved = tuple(sorted(
        (item for item in requirements if item.center not in staffed),
        key=lambda item: item.center.lower(),
    ))
    return CoverageResult(
        decisions=tuple(sorted(
            decisions,
            key=lambda item: (item.center.lower(), item.person.lower()),
        )),
        staffed_centers=tuple(sorted(staffed, key=str.lower)),
        unresolved_centers=tuple(item.center for item in unresolved),
        issues=tuple(_issue(item, decisions) for item in unresolved),
    )


def solve_minimum_coverage(
    requirements: Sequence[CenterRequirement],
) -> CoverageResult:
    normalized = tuple(sorted(requirements, key=lambda item: item.center.lower()))
    singles = tuple(item for item in normalized if item.remaining_slots == 1)
    if any(item.remaining_slots > 1 for item in normalized):
        raise ValueError("multi-person requirements need complete crew options")
    return _assemble_result(normalized, _match_single_requirements(singles))
