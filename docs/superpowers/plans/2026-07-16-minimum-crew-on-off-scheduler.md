# Minimum-Crew On/Off Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace opaque Auto-center counts with a live minimum-crew On/Off balance, compact disabled work centers, and make schedule-goal rebuilds fill only enabled minimum crews.

**Architecture:** Extend the existing pure `auto_schedule_capacity` module with a minimum-slot balance model that recommends the smallest number of centers to switch On or Off. The staffing route supplies the initial balance and the On/Off endpoint returns a fresh balance after persistence. The browser renders the model, compacts Off rows, and clears stale Auto-result warnings after manual edits. The pure scheduling engine reserves exact defaults first, then selects only the people required to satisfy enabled minimums.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, vanilla JavaScript/CSS, pytest.

## Global Constraints

- User-facing work-center controls say `On` and `Off`, never `Auto`.
- Balance uses open minimum crew slots only; it never uses maximum capacity.
- An enabled center with `min_ops` safely assigned people contributes zero open minimum slots.
- On/Off recommendations are advisory and do not change center state automatically.
- Goal rebuilds place qualified people only until each enabled center reaches its minimum; spare capacity remains unused.
- Exact defaults are hard, enabled-center reservations and precede ordinary candidate selection.
- Old placement and minimum warnings must not remain after a manual schedule edit.
- Saturday remains manual-only.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/zira_dashboard/auto_schedule_capacity.py` | Pure minimum-slot balance and deterministic On/Off recommendation. |
| `src/zira_dashboard/routes/staffing.py` | Build the day’s initial balance from roster, schedule, configured minimums, and enabled centers. |
| `src/zira_dashboard/routes/rotations.py` | Return a fresh balance with successful On/Off saves. |
| `src/zira_dashboard/rotation_suggestions.py` | Reserve exact defaults and stop generated placements at enabled minimums. |
| `src/zira_dashboard/templates/staffing.html` | Render balance copy and give each scheduler row an On/Off state. |
| `src/zira_dashboard/static/staffing.css` | Style On controls and compact, muted Off rows. |
| `src/zira_dashboard/static/staffing.js` | Live balance rendering, row expansion/collapse, and warning invalidation. |
| `tests/test_auto_schedule_capacity.py` | Pure balance behavior. |
| `tests/test_staffing_rotations.py` | Route, scheduler, and static UI contracts. |

### Task 1: Build the pure minimum-crew balance model

**Files:**
- Modify: `src/zira_dashboard/auto_schedule_capacity.py`
- Modify: `tests/test_auto_schedule_capacity.py`

**Interfaces:**
- Consumes: unassigned count, ordered enabled/disabled center names, and each center’s currently open minimum slots.
- Produces: `MinimumCrewBalance(unassigned_people, open_minimum_slots, direction, center_count, slot_delta, recommended_centers)`.
- Direction is exactly one of `"ready"`, `"turn_on"`, or `"turn_off"`.

- [ ] **Step 1: Write the failing pure tests**

```python
from zira_dashboard.auto_schedule_capacity import analyze_minimum_crew_balance


def test_balance_recommends_turning_off_fewest_open_minimum_centers():
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


def test_balance_recommends_turning_on_fewest_minimum_centers():
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


def test_balance_is_ready_when_open_minimum_slots_match_people_waiting():
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
```

- [ ] **Step 2: Run the pure tests to verify they fail**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_auto_schedule_capacity.py -q
```

Expected: FAIL because `analyze_minimum_crew_balance` and `MinimumCrewBalance` do not exist.

- [ ] **Step 3: Implement the model without using maxima**

```python
@dataclass(frozen=True)
class MinimumCrewBalance:
    unassigned_people: int
    open_minimum_slots: int
    direction: str
    center_count: int
    slot_delta: int
    recommended_centers: tuple[str, ...]


def analyze_minimum_crew_balance(*, unassigned_people, enabled_centers,
                                 disabled_centers, open_minimum_slots_by_center,
                                 center_order) -> MinimumCrewBalance:
    waiting = max(0, int(unassigned_people))
    enabled = tuple(dict.fromkeys(enabled_centers))
    disabled = tuple(dict.fromkeys(disabled_centers))
    open_slots = {name: max(0, int(open_minimum_slots_by_center.get(name, 0)))
                  for name in (*enabled, *disabled)}
    open_minimum_slots = sum(open_slots[name] for name in enabled)
    delta = open_minimum_slots - waiting
    if delta == 0:
        return MinimumCrewBalance(waiting, open_minimum_slots, "ready", 0, 0, ())
    candidates = enabled if delta > 0 else disabled
    ordered = tuple(sorted(
        (name for name in candidates if open_slots[name] > 0),
        key=lambda name: (
            open_slots[name] if delta > 0 else -open_slots[name],
            center_order.get(name, 1_000_000), name.lower(),
        ),
    ))
    covered = 0
    selected = []
    for name in ordered:
        selected.append(name)
        covered += open_slots[name]
        if covered >= abs(delta):
            break
    return MinimumCrewBalance(
        waiting, open_minimum_slots,
        "turn_off" if delta > 0 else "turn_on",
        len(selected), abs(delta), tuple(selected),
    )
```

Keep `analyze_auto_expansion` unchanged until its callers are migrated; it remains separately tested.

- [ ] **Step 4: Run the pure tests to verify they pass**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit the pure model**

```bash
git add src/zira_dashboard/auto_schedule_capacity.py tests/test_auto_schedule_capacity.py
git commit -m "feat: calculate minimum crew toggle balance"
```

### Task 2: Supply the day-specific balance to the page and On/Off API

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`
- Modify: `src/zira_dashboard/routes/rotations.py`
- Modify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Produces: `_minimum_crew_balance_for_day(roster, schedule, time_off_entries, enabled_centers) -> MinimumCrewBalance`.
- Template context key: `minimum_crew_balance` serialized as `{unassigned_people, open_minimum_slots, direction, center_count, slot_delta, recommended_centers}`.
- `POST /api/rotations/auto-work-centers` adds the same serialized object in `minimum_crew_balance` after it persists enabled centers.

- [ ] **Step 1: Write failing route tests**

```python
def test_staffing_context_exposes_open_minimum_slot_balance(monkeypatch):
    ctx = _render_staffing_page(
        monkeypatch,
        unassigned=["A", "B", "C"],
        auto_centers={"Repair 1", "Repair 2"},
        minimum_crew_balance={
            "unassigned_people": 3,
            "open_minimum_slots": 4,
            "direction": "turn_off",
            "center_count": 1,
            "slot_delta": 1,
            "recommended_centers": ["Repair 2"],
        },
    )
    assert ctx["minimum_crew_balance"]["direction"] == "turn_off"
    assert ctx["minimum_crew_balance"]["recommended_centers"] == ["Repair 2"]


def test_auto_center_save_returns_fresh_minimum_crew_balance(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    monkeypatch.setattr(
        staffing_route, "_minimum_crew_balance_for_day",
        lambda **_kwargs: MinimumCrewBalance(3, 3, "ready", 0, 0, ()),
    )

    response = client.post("/api/rotations/auto-work-centers", json={
        "day": TARGET_DAY.isoformat(), "work_centers": ["Repair 1"], "turn_off": [],
    })

    assert response.status_code == 200
    assert response.json()["minimum_crew_balance"] == {
        "unassigned_people": 3, "open_minimum_slots": 3, "direction": "ready",
        "center_count": 0, "slot_delta": 0, "recommended_centers": [],
    }
```

- [ ] **Step 2: Run the route tests to verify they fail**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -k 'minimum_crew_balance or auto_center_save_returns' -q
```

Expected: FAIL because the helper and response field do not exist.

- [ ] **Step 3: Implement safe open-minimum counting and serialization**

Add a route-local helper that:

1. removes full-day-off people with `_roster_minus_full_day_off`;
2. maps enabled names to configured `work_centers_store.min_ops(loc)` values;
3. counts only active, non-reserve, qualified current assignees toward each enabled center’s minimum; and
4. passes each enabled center’s `max(minimum - safe_assigned_count, 0)` plus each disabled center’s configured minimum to `analyze_minimum_crew_balance`.

Use this small serializer at both boundaries:

```python
def _minimum_crew_balance_payload(balance):
    return {
        "unassigned_people": balance.unassigned_people,
        "open_minimum_slots": balance.open_minimum_slots,
        "direction": balance.direction,
        "center_count": balance.center_count,
        "slot_delta": balance.slot_delta,
        "recommended_centers": list(balance.recommended_centers),
    }
```

Build the balance immediately after `bay_model` in `staffing_page`, add the payload to the template context, and calculate it after `_save_enabled_auto_work_centers(enabled)` in `save_auto_work_centers`. Do not use `_configured_center_capacities` or any maximum operator values.

Extend the existing `_render_staffing_page` test helper before adding the context assertion so it can pass the two new values through its fake bay model and route helper:

```python
def _render_staffing_page(
    monkeypatch, *, saved_schedule=None, day=None, smart_defaults=None,
    auto_centers=None, default_people=None, recycled_context=None,
    unassigned=None, minimum_crew_balance=None,
):
    monkeypatch.setattr(
        staffing_routes, "_minimum_crew_balance_for_day",
        lambda **_kwargs: minimum_crew_balance or MinimumCrewBalance(0, 0, "ready", 0, 0, ()),
    )
    # Add "unassigned": list(unassigned or []) to fake_build_staffing_bays.
```

Import `MinimumCrewBalance` in the test module alongside its existing scheduler imports.

- [ ] **Step 4: Run the route tests to verify they pass**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit the route model**

```bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "feat: expose live minimum crew balance"
```

### Task 3: Reserve exact defaults and fill only enabled minimum crews

**Files:**
- Modify: `src/zira_dashboard/rotation_suggestions.py`
- Modify: `tests/test_rotation_suggestions.py`
- Modify: `tests/test_staffing_rotations.py`

**Interfaces:**
- `suggest_recycled_assignments` keeps its public signature.
- Exact defaults are placed before ordinary candidate edges and are restricted to `exact_target_by_person[name]`.
- Non-coupled direct selection consumes at most `remaining_minimum_by_center[center]`, not the center’s remaining maximum capacity.

- [ ] **Step 1: Write failing solver regressions**

```python
def test_exact_default_reserves_its_enabled_center_before_other_candidates():
    result = suggest_recycled_assignments(
        TARGET_DAY, "normal",
        roster=[person("Able", {"Repair": 3}), person("Default", {"Repair": 3})],
        group_locations={"Repair": ("Repair 1",)},
        group_required_skills={"Repair": ("Repair",)},
        center_minimums={"Repair 1": 1}, center_capacities={"Repair 1": 1},
        runnable_centers={"Repair 1"}, exact_defaults={"Repair 1": ("Default",)},
    )

    assert result.assignments["Repair 1"] == ["Default"]
    assert result.unused_people == ("Able",)


def test_goal_rebuild_stops_after_enabled_center_minimums():
    result = suggest_recycled_assignments(
        TARGET_DAY, "normal",
        roster=[person("A", {"Repair": 3}), person("B", {"Repair": 3}), person("C", {"Repair": 3})],
        group_locations={"Repair": ("Repair 1",)},
        group_required_skills={"Repair": ("Repair",)},
        center_minimums={"Repair 1": 1}, center_capacities={"Repair 1": 3},
        runnable_centers={"Repair 1"},
    )

    assert result.assignments["Repair 1"] == ["A"]
    assert result.unused_people == ("B", "C")
```

- [ ] **Step 2: Run the regressions to verify they fail**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_suggestions.py -k 'exact_default_reserves or stops_after_enabled' -q
```

Expected: FAIL because the partial-selection loop fills center capacity and lets ordinary candidates consume an exact default’s slot.

- [ ] **Step 3: Implement default-first, minimum-only selection**

Before ordinary candidate construction, create one `AssignmentDecision` for each valid exact default that has remaining capacity at its exact target; decrement both `remaining_minimum_by_center[target]` and that target’s remaining capacity. Exclude those names from ordinary candidate edges.

When adding ordinary direct candidates, restrict each center to its residual minimum:

```python
choices = [
    edge for edge in direct_candidates
    if edge.person == name
    and remaining_minimum_by_center.get(edge.center, 0) > 0
    and remaining_capacity.get(edge.center, 0) > 0
]
```

After selecting a direct edge, decrement both maps. For coupled Trim Saw or training crews, retain existing safe-complete-crew logic but accept only a crew size needed to meet the remaining minimum or required green partner; do not choose optional larger crews. Keep the existing hard validation and placement issue reporting intact.

- [ ] **Step 4: Run solver and rebuild regressions to verify they pass**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_suggestions.py tests/test_staffing_rotations.py -k 'exact_default or enabled_center_minimum or rebuild' -q
```

Expected: PASS, including the new default reservation and minimum-only tests.

- [ ] **Step 5: Commit scheduler behavior**

```bash
git add src/zira_dashboard/rotation_suggestions.py tests/test_rotation_suggestions.py tests/test_staffing_rotations.py
git commit -m "fix: schedule enabled centers only to minimum crew"
```

### Task 4: Render the live balance and compact Off center rows

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html`
- Modify: `src/zira_dashboard/static/staffing.css`
- Modify: `src/zira_dashboard/static/staffing.js`
- Modify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Template adds `data-minimum-crew-balance='{{ minimum_crew_balance|tojson }}'` to `#rotation-auto-summary`.
- Each work-center table row includes `data-loc="{{ row.loc.name }}"` and `data-on="true|false"`.
- Browser functions: `renderMinimumCrewBalance(balance)`, `setWorkCenterOnState(name, enabled)`, and `clearStaleAutoWarnings()`.

- [ ] **Step 1: Write failing UI-contract tests**

```python
def test_staffing_template_uses_on_off_center_states_and_minimum_balance():
    html = (ROOT / "src/zira_dashboard/templates/staffing.html").read_text()
    css = (ROOT / "src/zira_dashboard/static/staffing.css").read_text()
    js = (ROOT / "src/zira_dashboard/static/staffing.js").read_text()

    assert 'data-minimum-crew-balance="{{ minimum_crew_balance|tojson }}"' in html
    assert 'data-on="{{ (row.loc.name in auto_schedule_enabled_wc_names)|tojson }}"' in html
    assert '>On<' in html and '>Off<' in html
    assert ".work-center-off" in css
    assert "function renderMinimumCrewBalance(balance)" in js
    assert "function renderMinimumCrewBalanceFromGrid()" in js
    assert "function setWorkCenterOnState(name, enabled)" in js
    assert "function clearStaleAutoWarnings()" in js


def test_manual_picker_change_clears_old_auto_result_warnings():
    js = (ROOT / "src/zira_dashboard/static/staffing.js").read_text()
    selection_start = js.index("// Kick autosave on every selection change.")
    selection_window = js[selection_start:selection_start + 220]
    assert "clearStaleAutoWarnings();" in selection_window
```

- [ ] **Step 2: Run UI-contract tests to verify they fail**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py -k 'on_off_center_states or clears_old_auto' -q
```

Expected: FAIL because the page still shows `Auto`, rows never collapse, and the browser has no minimum-balance renderer.

- [ ] **Step 3: Implement markup and styling**

In the work-center `<tr>`, add `data-loc` and the `work-center-off` class when the center is disabled. Replace the checkbox label copy with a switch-style label containing an accessible visual label that renders `On` or `Off`. Wrap the department, picker, and notes cells with `data-on-detail` so CSS can hide them for Off rows without removing their form values.

Render direct balance copy in the existing output:

```jinja2
<output class="minimum-crew-balance" id="rotation-auto-summary"
        data-minimum-crew-balance='{{ minimum_crew_balance|tojson }}'>
  <strong id="minimum-crew-waiting">{{ minimum_crew_balance.unassigned_people }} people waiting</strong>
  <span id="minimum-crew-slots">{{ minimum_crew_balance.open_minimum_slots }} minimum crew slots open</span>
  <span id="minimum-crew-action"></span>
</output>
```

Style `.work-center-off` as a muted, thin row, hide `[data-on-detail]` in that state, and keep the work-center name, `min N`, and toggle visible. Use `prefers-reduced-motion`-safe short height/opacity transitions only for the expansion/collapse.

- [ ] **Step 4: Implement live browser reconciliation**

`renderMinimumCrewBalance` reads the latest server payload and renders exactly one action:

```javascript
if (balance.direction === 'ready') action.textContent = 'Ready to schedule';
else if (balance.direction === 'turn_on') action.textContent = `Turn ${balance.center_count} work center${balance.center_count === 1 ? '' : 's'} on`;
else action.textContent = `Turn ${balance.center_count} work center${balance.center_count === 1 ? '' : 's'} off`;
```

`setWorkCenterOnState` finds `tr[data-loc]`, updates `data-on`, toggles `.work-center-off`, and updates the visible toggle text after the API accepts the change. Call it for every checkbox in `applyEnabledCenters`. On a successful `saveAutoCenters`, call `renderMinimumCrewBalance(data.minimum_crew_balance)`; do not derive the recommendation from a raw center count in JavaScript.

Add `renderMinimumCrewBalanceFromGrid()` for immediate post-picker feedback before the autosave response exists. It counts the current Unscheduled rail, then for every `tr[data-on="true"]` reads `data-minimum` and subtracts selected picker names to derive open minimum slots. It selects the advisory count locally with the same rule as the pure model: smallest deficits first for a Turn Off message, largest minimum contributions first for a Turn On message. It never reads a `data-max` value. Call it after every picker selection/quick clear and after `setWorkCenterOnState`; successful API responses replace this temporary browser state via `renderMinimumCrewBalance(data.minimum_crew_balance)`.

Call `clearStaleAutoWarnings()` from manual picker changes and quick clears. It must remove only `person_unplaced` and `center_minimum_unmet` issues from the browser warning model, leaving training and API-failure messages intact. Rebuild success must always call `renderCoverageIssues` with its fresh server response.

- [ ] **Step 5: Run UI-contract tests to verify they pass**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 6: Commit the user interface**

```bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.css src/zira_dashboard/static/staffing.js tests/test_staffing_rotations.py
git commit -m "feat: show live on-off minimum crew staffing balance"
```

### Task 5: Verify the integrated scheduler workflow

**Files:**
- Modify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Uses the completed public rebuild and On/Off endpoint contracts from Tasks 2–4.
- Produces a regression proving the balance, rows, rebuild result, and warnings agree.

- [ ] **Step 1: Write an end-to-end route regression**

```python
def test_goal_rebuild_matches_minimum_balance_and_returns_no_stale_person_warning(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    staffing_route = _stub_recommendation_inputs(monkeypatch)
    saved = []
    monkeypatch.setattr(rotations.staffing, "load_roster", lambda: [
        staffing.Person("Default", True, False, {"Repair": 3}),
        staffing.Person("Extra", True, False, {"Repair": 3}),
    ])
    monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _day: staffing.Schedule(day=TARGET_DAY))
    monkeypatch.setattr(rotations.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(staffing_route, "_enabled_auto_work_centers", lambda _day: {"Repair 1"})
    monkeypatch.setattr(staffing_route.work_centers_store, "default_people", lambda loc: ["Default"] if loc.name == "Repair 1" else [])

    response = client.post("/api/rotations/rebuild", json={"day": TARGET_DAY.isoformat(), "mode": "normal"})

    assert response.status_code == 200
    assert response.json()["assignments"]["Repair 1"] == ["Default"]
    assert response.json()["unplaced"] == ["Extra"]
    assert not any("Default could not be placed" in warning for warning in response.json()["warnings"])
    assert saved[0].assignments["Repair 1"] == ["Default"]
```

- [ ] **Step 2: Run the focused workflow regression**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_goal_rebuild_matches_minimum_balance_and_returns_no_stale_person_warning -q
```

Expected: PASS.

- [ ] **Step 3: Run the full focused scheduler suite**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_auto_schedule_capacity.py tests/test_rotation_suggestions.py tests/test_staffing_rotations.py -q
```

Expected: PASS with no failures.

- [ ] **Step 4: Run lint on the modified Python files**

Run:

```bash
.venv/bin/ruff check src/zira_dashboard/auto_schedule_capacity.py src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py src/zira_dashboard/rotation_suggestions.py tests/test_auto_schedule_capacity.py tests/test_rotation_suggestions.py tests/test_staffing_rotations.py
```

Expected: `All checks passed!`

- [ ] **Step 5: Commit final regression coverage**

```bash
git add tests/test_staffing_rotations.py
git commit -m "test: cover minimum crew on-off scheduler workflow"
```
