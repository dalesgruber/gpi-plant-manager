# Auto Scheduler Minimum Staffing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make automation staff Auto work centers only at their full minimum crews, guide managers through insufficient-capacity selections, and keep new and Saturday schedules manual-first.

**Architecture:** A pure capacity module calculates daily minimum-headcount feasibility and the number of centers that must be disabled. The Staffing route uses it before persisting Auto selections and before calling the existing pure assignment engine. The engine receives effective per-center minima and a feasible center set, prioritizes deficits, then removes incomplete generated crews. The Staffing UI renders server-provided capacity warnings and a replacement dialog, while blank weekday and Saturday views remain manual-only until automation is explicitly invoked.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, vanilla JavaScript, pytest, Postgres-backed work-center settings.

## Global Constraints

- New empty schedules have no prefilled default people or automatic assignments.
- Saturdays hide Auto goal controls and Auto work-center checkboxes; their staffing flow remains manual.
- Auto-generated assignments never leave a work center below its effective min_ops; preserve all manual assignments.
- Availability excludes inactive, reserve, full-day-absent, manually committed, and already scheduled people.
- Preserve qualification, capacity, Trim Saw pairing, training-block, rotation-history, and one-person-per-center guarantees.
- Settings-backed min_ops values are authoritative over static Location.min_ops values.
- The server must reject over-capacity Auto selections even if the client is stale or bypassed.
- Run focused tests with ZIRA_API_KEY=test .venv/bin/python -m pytest -q.

---

## File structure

| File | Responsibility |
| --- | --- |
| src/zira_dashboard/auto_schedule_capacity.py | Pure capacity accounting and deterministic feasible-set calculation; no database or FastAPI dependencies. |
| src/zira_dashboard/rotation_suggestions.py | Apply effective minimums and feasible centers while generating safe automatic assignments. |
| src/zira_dashboard/routes/staffing.py | Build day-specific capacity inputs, stop blank-day seeding, and expose Saturday/capacity context. |
| src/zira_dashboard/routes/rotations.py | Validate Auto-selection replacements and enforce capacity before persisting settings or rebuilding. |
| src/zira_dashboard/templates/staffing.html | Manual-first Auto controls, Saturday guard, capacity warning, and replacement-dialog markup. |
| src/zira_dashboard/static/staffing.js | Request capacity validation, render the replacement flow, and apply server-authoritative selections. |
| tests/test_auto_schedule_capacity.py | Pure capacity contract tests. |
| tests/test_rotation_suggestions.py | Minimum-crew generation regressions. |
| tests/test_staffing_rotations.py | Route, selection-validation, blank-schedule, and template/static contracts. |
| tests/test_staffing_static.py | Browser-script contract for warning filtering and dialog behavior. |

### Task 1: Add a pure Auto-capacity model

**Files:**

- Create: src/zira_dashboard/auto_schedule_capacity.py
- Create: tests/test_auto_schedule_capacity.py

**Interfaces:**

- Consumes: enabled center names, effective minimums, manual people already assigned per center, available automatic headcount, and plant-order names.
- Produces: AutoCapacity(required_people, available_people, shortage, centers_to_disable, runnable_centers, blocked_centers) and analyze_auto_capacity(...) for route and engine callers.

- [ ] **Step 1: Write the failing pure-capacity tests**

~~~python
from zira_dashboard.auto_schedule_capacity import analyze_auto_capacity


def test_capacity_keeps_centers_in_plant_order_until_minimums_fit():
    result = analyze_auto_capacity(
        enabled_centers=("Hand Build #2", "Repair 1", "Big Build #1"),
        minimum_by_center={"Hand Build #2": 2, "Repair 1": 1, "Big Build #1": 2},
        manual_count_by_center={},
        available_people=3,
        center_order={"Hand Build #2": 0, "Repair 1": 1, "Big Build #1": 2},
    )

    assert result.required_people == 5
    assert result.available_people == 3
    assert result.shortage == 2
    assert result.centers_to_disable == 1
    assert result.runnable_centers == ("Hand Build #2", "Repair 1")
    assert result.blocked_centers == ("Big Build #1",)


def test_manual_people_reduce_that_center_remaining_minimum():
    result = analyze_auto_capacity(
        enabled_centers=("Hand Build #2", "Repair 1"),
        minimum_by_center={"Hand Build #2": 2, "Repair 1": 1},
        manual_count_by_center={"Hand Build #2": 1},
        available_people=2,
        center_order={"Hand Build #2": 0, "Repair 1": 1},
    )

    assert result.required_people == 2
    assert result.shortage == 0
    assert result.runnable_centers == ("Hand Build #2", "Repair 1")


def test_center_count_to_disable_uses_largest_remaining_crews_first():
    result = analyze_auto_capacity(
        enabled_centers=("One", "Two", "Three"),
        minimum_by_center={"One": 1, "Two": 2, "Three": 3},
        manual_count_by_center={},
        available_people=3,
        center_order={"One": 0, "Two": 1, "Three": 2},
    )

    assert result.shortage == 3
    assert result.centers_to_disable == 1
~~~

- [ ] **Step 2: Run the tests to verify RED**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_auto_schedule_capacity.py -q

Expected: FAIL during collection because zira_dashboard.auto_schedule_capacity does not exist.

- [ ] **Step 3: Implement the immutable capacity result and deterministic analysis**

~~~python
# src/zira_dashboard/auto_schedule_capacity.py
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class AutoCapacity:
    required_people: int
    available_people: int
    shortage: int
    centers_to_disable: int
    runnable_centers: tuple[str, ...]
    blocked_centers: tuple[str, ...]


def analyze_auto_capacity(
    *,
    enabled_centers: Sequence[str],
    minimum_by_center: Mapping[str, int],
    manual_count_by_center: Mapping[str, int],
    available_people: int,
    center_order: Mapping[str, int],
) -> AutoCapacity:
    ordered = tuple(sorted(
        dict.fromkeys(enabled_centers),
        key=lambda c: (center_order.get(c, 1_000_000), c.lower()),
    ))
    remaining = {
        center: max(
            0,
            int(minimum_by_center.get(center, 1))
            - int(manual_count_by_center.get(center, 0)),
        )
        for center in ordered
    }
    required = sum(remaining.values())
    available = max(0, int(available_people))
    runnable, used = [], 0
    for center in ordered:
        if used + remaining[center] <= available:
            runnable.append(center)
            used += remaining[center]
    blocked = tuple(center for center in ordered if center not in set(runnable))
    shortage = max(0, required - available)
    released, disable_count = 0, 0
    for center in sorted(
        ordered,
        key=lambda c: (-remaining[c], center_order.get(c, 1_000_000), c.lower()),
    ):
        if released >= shortage:
            break
        released += remaining[center]
        disable_count += 1
    return AutoCapacity(
        required, available, shortage,
        disable_count if shortage else 0,
        tuple(runnable), blocked,
    )
~~~

Keep the result pure. Do not import staffing, work_centers_store, db, or FastAPI in this module.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_auto_schedule_capacity.py -q

Expected: PASS with 3 tests passed.

- [ ] **Step 5: Commit the pure capacity model**

~~~bash
git add src/zira_dashboard/auto_schedule_capacity.py tests/test_auto_schedule_capacity.py
git commit -m "feat: calculate auto schedule capacity"
~~~

### Task 2: Enforce minimum crews in automatic assignment generation

**Files:**

- Modify: src/zira_dashboard/rotation_suggestions.py:526-848
- Modify: tests/test_rotation_suggestions.py

**Interfaces:**

- Consumes: existing suggestion inputs plus center_minimums: Mapping[str, int] | None and runnable_centers: Collection[str] | None.
- Produces: RecycledSuggestion containing generated crews only for runnable centers, with no generated assignment surviving below that center's effective minimum.

- [ ] **Step 1: Add failing engine tests for a two-person minimum and deficit-first placement**

~~~python
def test_engine_leaves_two_person_center_empty_when_only_one_qualified_person_exists():
    out = suggest_recycled_assignments(
        day=date(2026, 7, 14), mode="normal",
        roster=[staffing.Person(name="Only Builder", skills={"Hand Build": 3})],
        group_locations={"Hand Build": ("Hand Build #2",)},
        group_required_skills={"Hand Build": ("Hand Build",)},
        center_minimums={"Hand Build #2": 2},
        runnable_centers={"Hand Build #2"},
        history=RecycledHistory(), locked_assignments={}, block_effects=(),
    )

    assert out.assignments.get("Hand Build #2", []) == []
    assert "Hand Build #2 could not be staffed to its minimum of 2 operators." in out.warnings


def test_engine_fills_each_minimum_before_optional_capacity():
    out = suggest_recycled_assignments(
        day=date(2026, 7, 14), mode="normal",
        roster=[
            staffing.Person(name="A", skills={"Hand Build": 3}),
            staffing.Person(name="B", skills={"Hand Build": 3}),
            staffing.Person(name="C", skills={"Hand Build": 3}),
        ],
        group_locations={"Hand Build": ("Hand Build #1", "Hand Build #2")},
        group_required_skills={"Hand Build": ("Hand Build",)},
        center_minimums={"Hand Build #1": 2, "Hand Build #2": 1},
        runnable_centers={"Hand Build #1", "Hand Build #2"},
        history=RecycledHistory(), locked_assignments={}, block_effects=(),
    )

    assert len(out.assignments["Hand Build #1"]) == 2
    assert len(out.assignments["Hand Build #2"]) == 1
~~~

- [ ] **Step 2: Run the engine tests to verify RED**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_suggestions.py -q

Expected: FAIL with TypeError because center_minimums and runnable_centers are not accepted.

- [ ] **Step 3: Thread effective minimums and feasible centers through the engine**

Change the public signature exactly:

~~~python
def suggest_recycled_assignments(
    day: date,
    mode: str,
    roster: Sequence[staffing.Person],
    preferences: dict[str, dict[str, str]] | None = None,
    base_assignments: dict[str, list[str]] | None = None,
    group_locations: dict[str, Sequence[str]] | None = None,
    group_required_skills: dict[str, tuple[str, ...]] | None = None,
    history: RecycledHistory | None = None,
    locked_assignments: dict[str, Sequence[str]] | None = None,
    block_effects: Sequence = (),
    training_cap: int = 2,
    center_minimums: Mapping[str, int] | None = None,
    runnable_centers: Collection[str] | None = None,
) -> RecycledSuggestion:
~~~

Import Collection and Mapping from collections.abc. Resolve the values with:

~~~python
def _effective_minimum(center: str) -> int:
    if center_minimums is not None and center in center_minimums:
        return max(0, int(center_minimums[center]))
    return _center_min_ops(center)

allowed_centers = managed_centers if runnable_centers is None else managed_centers & set(runnable_centers)
~~~

Only generate into allowed_centers. In greedy placement, choose centers with a remaining deficit first:

~~~python
def _center_priority(center: str) -> tuple[int, int, str]:
    deficit = max(0, _effective_minimum(center) - len(assignments.get(center, [])))
    return (0 if deficit else 1, -deficit, center.lower())
~~~

Sort open centers with that priority before applying existing choose_center fairness. After normal and training placement, remove only GENERATED_SOURCE names from every allowed center whose final count remains below _effective_minimum(center). Keep manual names untouched. Emit this exact warning:

~~~python
f"{center} could not be staffed to its minimum of {minimum} operators."
~~~

Keep the existing Trim Saw no-safe-pair warning; do not weaken pairing validation.

- [ ] **Step 4: Run engine and safety regressions to verify GREEN**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_suggestions.py tests/test_staffing_trim_saw_defaults.py -q

Expected: PASS with no removed Trim Saw assertions.

- [ ] **Step 5: Commit the minimum-crew engine behavior**

~~~bash
git add src/zira_dashboard/rotation_suggestions.py tests/test_rotation_suggestions.py
git commit -m "feat: require minimum crews for auto scheduling"
~~~

### Task 3: Build day-specific capacity inputs and enforce them in APIs

**Files:**

- Modify: src/zira_dashboard/routes/staffing.py:120-355
- Modify: src/zira_dashboard/routes/rotations.py:145-275
- Modify: tests/test_staffing_rotations.py

**Interfaces:**

- Consumes: AutoCapacity, work_centers_store.min_ops, date-specific roster/time-off, schedule assignments, assignment sources, and proposed Auto centers.
- Produces: _auto_capacity_for_day(...) -> AutoCapacity, a capacity-aware _recycled_suggestion_for_day(...), and POST /api/rotations/auto-work-centers responses shaped as either {ok: true, enabled_work_centers: [...], capacity: {...}} or {ok: false, error: "...", capacity: {...}, required_disable_count: int}.

- [ ] **Step 1: Add failing day-capacity and endpoint tests**

~~~python
def test_capacity_for_day_uses_effective_minimum_and_excludes_absent_and_manual(monkeypatch):
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    monkeypatch.setattr(
        staffing_route.work_centers_store, "min_ops",
        lambda loc: 2 if loc.name == "Hand Build #2" else 1,
    )
    roster = [
        staffing.Person(name="Manual", skills={"Hand Build": 3}),
        staffing.Person(name="Off", skills={"Hand Build": 3}),
        staffing.Person(name="Available", skills={"Hand Build": 3}),
    ]

    result = staffing_route._auto_capacity_for_day(
        d=TARGET_DAY, enabled_work_centers={"Hand Build #2"}, roster=roster,
        assignments={"Repair 1": ["Manual"]},
        assignment_sources={"Repair 1": {"Manual": "manual"}},
        time_off_entries=[{"name": "Off", "hours": None}],
    )

    assert result.available_people == 1
    assert result.required_people == 2
    assert result.shortage == 1


def test_auto_center_endpoint_rejects_unsafe_enable_without_replacement(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    monkeypatch.setattr(
        rotations.staffing_route, "_auto_capacity_for_day",
        lambda **kwargs: AutoCapacity(4, 2, 2, 1, ("Repair 1",), ("Hand Build #2",)),
    )

    resp = client.post("/api/rotations/auto-work-centers", json={
        "day": "2026-07-14",
        "work_centers": ["Repair 1", "Hand Build #2"],
        "turn_off": [],
    })

    assert resp.status_code == 409
    assert resp.json()["required_disable_count"] == 1
    assert "need 2 more people" in resp.json()["error"]


def test_auto_center_endpoint_accepts_sufficient_replacement(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    saved = []
    monkeypatch.setattr(
        rotations.staffing_route, "_auto_capacity_for_day",
        lambda **kwargs: AutoCapacity(2, 2, 0, 0, ("Hand Build #2",), ()),
    )
    monkeypatch.setattr(
        rotations.staffing_route, "_save_enabled_auto_work_centers",
        lambda names: saved.append(names) or names,
    )

    resp = client.post("/api/rotations/auto-work-centers", json={
        "day": "2026-07-14",
        "work_centers": ["Hand Build #2"],
        "turn_off": ["Repair 1"],
    })

    assert resp.status_code == 200
    assert saved == [["Hand Build #2"]]
~~~

Import AutoCapacity in the test module. Update the existing endpoint test to include day and turn_off: [].

- [ ] **Step 2: Run the route tests to verify RED**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -q

Expected: FAIL because _auto_capacity_for_day and the endpoint response contract do not exist.

- [ ] **Step 3: Implement the day-specific capacity adapter in the Staffing route**

Add imports:

~~~python
from ..auto_schedule_capacity import AutoCapacity, analyze_auto_capacity
~~~

Add the helper:

~~~python
def _auto_capacity_for_day(
    *, d: date, enabled_work_centers, roster, assignments, assignment_sources, time_off_entries,
) -> AutoCapacity:
    enabled = _ordered_work_center_names(enabled_work_centers)
    locks = _protected_locks(assignment_sources, assignments, allowed_centers=None)
    locked_names = {name for names in locks.values() for name in names}
    available = [
        person for person in _roster_minus_full_day_off(roster, time_off_entries)
        if person.active and not person.reserve and person.name not in locked_names
    ]
    minimums = {
        loc.name: work_centers_store.min_ops(loc)
        for loc in staffing.LOCATIONS if loc.name in enabled
    }
    manual_counts = {center: len(set(locks.get(center, []))) for center in enabled}
    return analyze_auto_capacity(
        enabled_centers=enabled,
        minimum_by_center=minimums,
        manual_count_by_center=manual_counts,
        available_people=len(available),
        center_order=_location_order(),
    )
~~~

Extend _recycled_suggestion_for_day and all callers with assignment_sources. Calculate the capacity from base assignments plus assignment sources. Pass the effective map and feasible centers to the engine:

~~~python
capacity = _auto_capacity_for_day(
    d=d, enabled_work_centers=enabled, roster=roster,
    assignments=base_assignments, assignment_sources=assignment_sources,
    time_off_entries=time_off_entries,
)
center_minimums = {
    loc.name: work_centers_store.min_ops(loc)
    for loc in staffing.LOCATIONS if loc.name in enabled
}
# suggest_recycled_assignments(...,
#     center_minimums=center_minimums,
#     runnable_centers=capacity.runnable_centers)
~~~

Append the capacity warning with dataclasses.replace because RecycledSuggestion is frozen:

~~~python
f"Auto centers need {capacity.shortage} more people to run. Turn off at least {capacity.centers_to_disable} work center(s)."
~~~

- [ ] **Step 4: Enforce capacity in the Auto-selection endpoint**

Require this JSON body:

~~~json
{"day":"YYYY-MM-DD","work_centers":["..."],"turn_off":["..."]}
~~~

In save_auto_work_centers, load roster, schedule, and time off in its worker-thread closure. Remove every valid turn_off name from the proposed ordered list, then call _auto_capacity_for_day. If shortage is positive, do not call _save_enabled_auto_work_centers; return status 409 and:

~~~python
{
    "ok": False,
    "error": f"Auto centers need {capacity.shortage} more people to run. Turn off at least {capacity.centers_to_disable} work center(s).",
    "capacity": _capacity_payload(capacity),
    "required_disable_count": capacity.centers_to_disable,
}
~~~

Add _capacity_payload(capacity) in routes/rotations.py returning required_people, available_people, shortage, centers_to_disable, runnable_centers, and blocked_centers. On success include it with ok and enabled_work_centers. Keep both cache invalidations only after a successful save.

- [ ] **Step 5: Run focused route and engine tests to verify GREEN**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_auto_schedule_capacity.py tests/test_rotation_suggestions.py tests/test_staffing_rotations.py -q

Expected: PASS, including manual-lock and enabled-new-work-center rebuild regressions.

- [ ] **Step 6: Commit server-side enforcement**

~~~bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "feat: guard auto centers by daily staffing capacity"
~~~

### Task 4: Make new schedules and Saturdays manual-only

**Files:**

- Modify: src/zira_dashboard/routes/staffing.py:585-650, 900-1030
- Modify: src/zira_dashboard/templates/staffing.html:205-225, 280-310
- Modify: tests/test_staffing_rotations.py

**Interfaces:**

- Consumes: viewed schedule assignments and date.weekday().
- Produces: blank manual Schedule.assignments, auto_scheduler_available: bool template context, and no Auto controls for Saturday views.

- [ ] **Step 1: Add failing blank-schedule and Saturday template-context tests**

~~~python
def test_blank_staffing_day_stays_empty_without_default_or_smart_seed(monkeypatch):
    calls = []
    ctx = _render_staffing_page(
        monkeypatch,
        smart_defaults=lambda *args, **kwargs: calls.append(args) or {"Repair 1": ["Unexpected"]},
    )

    assert calls == []
    assert ctx["sched"].assignments == {}
    assert ctx["auto_scheduler_available"] is True


def test_saturday_staffing_context_is_manual_only(monkeypatch):
    ctx = _render_staffing_page(monkeypatch, day=date(2026, 7, 18))

    assert ctx["auto_scheduler_available"] is False


def test_staffing_template_gates_auto_controls_for_saturday():
    html = (ROOT / "src/zira_dashboard/templates/staffing.html").read_text()

    assert "{% if auto_scheduler_available %}" in html
    assert 'class="rotation-controls"' in html
    assert 'class="wc-auto-cb"' in html
~~~

- [ ] **Step 2: Run the Staffing route tests to verify RED**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -q

Expected: FAIL because a blank day currently calls _smart_defaults_for_day and no auto_scheduler_available context key exists.

- [ ] **Step 3: Remove blank-day seeding and publish Saturday availability to the template**

In staffing_page, replace the entire if not sched.assignments block that reads default_people, staffing.default_assignments(), and _smart_defaults_for_day with:

~~~python
seeded_from_defaults = False
auto_scheduler_available = d.weekday() != 5
~~~

Only calculate saved-day smart defaults for picker hints when sched.assignments is nonempty and auto_scheduler_available is true. Add auto_scheduler_available to the template context.

Wrap the entire rotation-controls block in staffing.html with {% if auto_scheduler_available %}. Wrap only the Auto checkbox label/input in the work-center row with the same condition; leave the Training checkbox and manual person picker visible on Saturdays.

- [ ] **Step 4: Run focused manual-first tests to verify GREEN**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py tests/test_staffing_trim_saw_defaults.py -q

Expected: PASS. Update only assertions that deliberately expected old blank-day default seeding; retain tests that validate an explicit rebuild.

- [ ] **Step 5: Commit manual-first schedule behavior**

~~~bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/templates/staffing.html tests/test_staffing_rotations.py
git commit -m "feat: start schedules manually and keep Saturdays manual"
~~~

### Task 5: Add the Auto replacement dialog and server-driven warnings

**Files:**

- Modify: src/zira_dashboard/templates/staffing.html:205-225
- Modify: src/zira_dashboard/static/staffing.js:1380-1565
- Modify: src/zira_dashboard/static/staffing.css
- Modify: tests/test_staffing_static.py
- Modify: tests/test_staffing_rotations.py

**Interfaces:**

- Consumes: a 409 capacity response with capacity and required_disable_count.
- Produces: an accessible #auto-capacity-dialog replacement workflow that submits {day, work_centers, turn_off} and applies only server-approved center selections.

- [ ] **Step 1: Add failing static contracts for the dialog and API payload**

~~~python
def test_auto_capacity_dialog_has_replacement_controls():
    html = _template()
    js = _script()

    assert 'id="auto-capacity-dialog"' in html
    assert 'aria-labelledby="auto-capacity-title"' in html
    assert 'id="auto-capacity-replacements"' in html
    assert "required_disable_count" in js
    assert "turn_off" in js
    assert "showAutoCapacityDialog" in js


def test_disabled_auto_warning_filter_keeps_capacity_warning_visible():
    js = _script()

    assert "Auto centers need " in js
    assert "warning.startsWith(center + ' could not be staffed to its minimum')" in js
~~~

- [ ] **Step 2: Run static tests to verify RED**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_static.py tests/test_staffing_rotations.py -q

Expected: FAIL because the dialog markup and JavaScript handler do not exist.

- [ ] **Step 3: Add dialog markup and styles**

Place this after rotation-controls inside the Staffing form:

~~~html
<dialog id="auto-capacity-dialog" aria-labelledby="auto-capacity-title">
  <form method="dialog" id="auto-capacity-form">
    <h3 id="auto-capacity-title">Choose work centers to turn off</h3>
    <p id="auto-capacity-message"></p>
    <div id="auto-capacity-replacements"></div>
    <div class="dialog-actions">
      <button type="button" id="auto-capacity-cancel">Cancel</button>
      <button type="submit" id="auto-capacity-confirm">Update Auto centers</button>
    </div>
  </form>
</dialog>
~~~

Add narrowly scoped auto-capacity dialog, replacement, and dialog-actions CSS rules in staffing.css. Use existing panel colors and spacing; do not add a framework or global modal stylesheet.

- [ ] **Step 4: Implement the client-side replacement flow**

Refactor saveAutoCenters(changedCb) to call:

~~~javascript
async function postAutoCenters(workCenters, turnOff) {
  return fetch('/api/rotations/auto-work-centers', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
    body: JSON.stringify({ day, work_centers: workCenters, turn_off: turnOff }),
  });
}
~~~

On a 409 response, restore the changed checkbox, call showAutoCapacityDialog(data, requestedCenter), and do not mutate window.AUTO_SCHEDULE_WC_NAMES. The dialog must:

1. show the server error in #auto-capacity-message;
2. list every currently enabled center except the requested center as a checkbox in #auto-capacity-replacements;
3. disable confirm until at least data.required_disable_count choices are checked;
4. submit the requested center plus every currently selected center not chosen for removal; and
5. call applyEnabledCenters, removeDisabledAutoWarnings, and showToast only after a successful response.

Use dialog.showModal(), close(), and return focus to changedCb on cancel or completion. Add an Escape/cancel handler that leaves the server selection unchanged.

Extend removeDisabledAutoWarnings so it removes the new center-specific warning prefix:

~~~javascript
warning.startsWith(center + ' could not be staffed to its minimum')
~~~

Do not remove the global Auto centers need ... shortage warning when a checkbox is disabled; the next successful server response is authoritative.

- [ ] **Step 5: Run focused UI contracts and scheduling regressions to verify GREEN**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_static.py tests/test_staffing_rotations.py tests/test_rotation_suggestions.py -q

Expected: PASS with the dialog contract, warning behavior, server API, and assignment safety covered.

- [ ] **Step 6: Run the full suite and inspect the final diff**

Run: ZIRA_API_KEY=test .venv/bin/python -m pytest -q

Expected: PASS, with database-backed tests skipped when DATABASE_URL is absent.

Run: git diff --check HEAD~5..HEAD && git status --short

Expected: no whitespace errors and only intended modified files.

- [ ] **Step 7: Commit the Auto-selection interface**

~~~bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py tests/test_staffing_rotations.py
git commit -m "feat: guide auto center capacity replacements"
~~~

## Plan self-review

- Spec coverage: Tasks 1–3 implement minimum capacity, effective settings, capacity messaging, and server enforcement; Task 2 prevents under-minimum generated crews; Task 4 implements manual-first and Saturday-only behavior; Task 5 implements the manager replacement choice and warning UI.
- Placeholder scan: no incomplete markers, undefined interfaces, or generic error-handling steps remain.
- Type consistency: AutoCapacity is defined in Task 1, passed as the exact object in Tasks 3 and 5, and its payload fields are declared in Task 3 before the client consumes them in Task 5. The engine center_minimums and runnable_centers inputs are defined in Task 2 before Task 3 uses them.
