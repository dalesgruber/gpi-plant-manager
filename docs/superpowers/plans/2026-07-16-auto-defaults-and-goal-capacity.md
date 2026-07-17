# Auto Defaults and Goal Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep exact Auto defaults ahead of stale generated assignments and show a live unscheduled-versus-Auto-enabled center count beside Schedule Goal.

**Architecture:** The Staffing route will give the automatic solver ownership of every enabled Auto center, preserving only assignments outside that set while restoring manual locks separately. Exact defaults can then be solved at their target center without duplicating a prior generated placement. The route will also provide an initial count model; the existing staffing script will keep its compact display current from the actual Auto checkboxes and the existing Unscheduled rail.

**Tech Stack:** Python 3.12, FastAPI/Jinja, existing pure scheduling helpers, vanilla browser JavaScript, pytest.

## Global Constraints

- Exact defaults override stale generated Auto assignments but never manual locks, absence, qualification, enabled-center, or capacity rules.
- `Auto On` is the count of enabled Auto work centers, never a capacity total.
- The signed Auto-mode value is `auto_on_count - unscheduled_count`.
- Preserve the project’s existing static template/JavaScript contract-test style; do not add browser test tooling.

---

### Task 1: Give the solver ownership of enabled Auto centers

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:429-505`
- Modify: `tests/test_staffing_rotations.py:592-668`

**Interfaces:**
- Consumes: schedule `assignments`, `assignment_sources`, and the resolved enabled Auto center names.
- Produces: `_auto_solver_base_assignments(base_assignments, enabled_centers) -> dict[str, list[str]]`, containing only the non-Auto assignments that must pass through unchanged.
- Preserves: manual Auto assignments through the existing `locked_assignments` argument.

- [ ] **Step 1: Write the failing helper and rebuild regression tests**

  Add a focused helper test proving all enabled centers are removed from the solver base while a disabled/manual pass-through remains:

  ```python
  def test_auto_solver_base_assignments_keeps_only_centers_outside_auto_scope():
      assert staffing_route._auto_solver_base_assignments(
          {
              "Work Orders": ["Default Mechanic"],
              "Tablets": ["Old Generated"],
              "Repair 1": ["Manual Repair"],
              "Truck Driver": ["Outside Auto"],
          },
          {"Work Orders", "Tablets", "Repair 1"},
      ) == {"Truck Driver": ["Outside Auto"]}
  ```

  Extend `test_normal_rebuild_uses_enabled_auto_centers_to_distribute_defaults` so its captured `base_assignments` is only `{"Truck Driver": ["Outside Auto"]}` while `locked_assignments` remains `{"Repair 1": ["Manual Inside"]}`.

- [ ] **Step 2: Run the focused tests to verify they fail**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_auto_solver_base_assignments_keeps_only_centers_outside_auto_scope \
    tests/test_staffing_rotations.py::test_normal_rebuild_uses_enabled_auto_centers_to_distribute_defaults -q
  ```

  Expected: the new helper test fails because `_auto_solver_base_assignments` does not exist; the endpoint assertion fails because all saved assignments still reach the solver.

- [ ] **Step 3: Implement the smallest ownership boundary**

  Add this route-local pure helper immediately before `_recycled_suggestion_for_day`:

  ```python
  def _auto_solver_base_assignments(base_assignments, enabled_centers):
      """Keep only assignments outside the Auto solver's owned centers."""
      enabled = set(enabled_centers)
      return {
          center: list(names or [])
          for center, names in (base_assignments or {}).items()
          if center not in enabled
      }
  ```

  In `_recycled_suggestion_for_day`, after resolving `enabled`, pass the filtered value to `suggest_recycled_assignments`:

  ```python
  solver_base_assignments = _auto_solver_base_assignments(base_assignments, enabled)
  # ...
  base_assignments=solver_base_assignments,
  ```

  Do not add manual Auto assignments back to the base map: the existing `scoped_locks` path already reserves them before the solver runs.

- [ ] **Step 4: Run the focused tests to verify they pass**

  Run the command from Step 2. Expected: 2 passed.

- [ ] **Step 5: Commit the focused change**

  ```bash
  git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
  git commit -m "fix: prioritize defaults over stale auto assignments"
  ```

### Task 2: Cover the default-versus-generated conflict end to end

**Files:**
- Modify: `tests/test_staffing_rotations.py:1092-1127`
- Modify: `src/zira_dashboard/routes/staffing.py:904-918`

**Interfaces:**
- Consumes: an enabled exact default, a stale generated assignment for the same person, and an `assignment_sources` map.
- Produces: a rebuild result with the person at their exact default work center and no `not a safe Auto assignment` warning.
- Preserves: page-context warnings for genuine manual/qualification/capacity conflicts.

- [ ] **Step 1: Write the failing end-to-end regression**

  Add a test alongside `test_rebuild_treats_default_people_as_exact_generated_anchors`:

  ```python
  def test_rebuild_moves_stale_generated_assignment_to_exact_default(monkeypatch):
      client, rotations = _rotations_client(monkeypatch)
      staffing_route = _stub_recommendation_inputs(monkeypatch)
      saved = []
      sched = staffing.Schedule(
          day=TARGET_DAY,
          assignments={"Repair 2": ["Default Green"]},
          assignment_sources={"Repair 2": {"Default Green": "generated"}},
      )
      monkeypatch.setattr(staffing_route, "_enabled_auto_work_centers", lambda _d: {"Repair 1", "Repair 2"})
      monkeypatch.setattr(
          staffing_route.work_centers_store,
          "default_people",
          lambda loc: ["Default Green"] if loc.name == "Repair 1" else [],
      )
      monkeypatch.setattr(rotations.staffing, "load_roster", lambda: [
          staffing.Person(name="Default Green", skills={"Repair": 3}),
      ])
      monkeypatch.setattr(rotations.staffing, "load_schedule", lambda _d: sched)
      monkeypatch.setattr(rotations.staffing, "save_schedule", saved.append)
      monkeypatch.setattr(rotations._http_cache, "invalidate_today_cache", lambda: None)

      response = client.post("/api/rotations/rebuild", json={
          "day": TARGET_DAY.isoformat(), "mode": "normal",
      })

      assert response.status_code == 200
      assert response.json()["assignments"]["Repair 1"] == ["Default Green"]
      assert "Repair 2" not in response.json()["assignments"]
      assert not any("not a safe Auto assignment" in warning for warning in response.json()["warnings"])
  ```

- [ ] **Step 2: Run the regression to verify it fails**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_rebuild_moves_stale_generated_assignment_to_exact_default -q
  ```

  Expected: failure because the stale generated Repair 2 assignment remains protected before the exact Repair 1 default is considered.

- [ ] **Step 3: Route page-context input through the same ownership boundary**

  In `staffing_page`, build `recycled_ctx` with a base map filtered by `_auto_solver_base_assignments` and manual-only locks:

  ```python
  base_assignments=_auto_solver_base_assignments(
      sched.assignments, enabled_auto_work_centers,
  ),
  locked_assignments=_protected_locks(
      sched.assignment_sources,
      sched.assignments,
      allowed_centers=enabled_auto_work_centers,
      include_saved_defaults=False,
  ),
  ```

  Exact defaults still flow through `_default_inputs()` into the solver. This prevents the page-only preview from trying to add defaults as duplicate locks after stale generated placements.

- [ ] **Step 4: Run the regression and adjacent default tests**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_rebuild_moves_stale_generated_assignment_to_exact_default \
    tests/test_staffing_rotations.py::test_rebuild_treats_default_people_as_exact_generated_anchors \
    tests/test_staffing_rotations.py::test_default_people_locks_merge_with_manual_locks -q
  ```

  Expected: all pass; the last test preserves the independent helper contract.

- [ ] **Step 5: Commit the regression coverage**

  ```bash
  git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
  git commit -m "fix: keep defaults ahead of generated assignments"
  ```

### Task 3: Add the Schedule Goal count indicator

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:904-970`
- Modify: `src/zira_dashboard/templates/staffing.html:214-227,490-498`
- Modify: `src/zira_dashboard/static/staffing.js:646-659,1354-1535`
- Modify: `src/zira_dashboard/static/staffing.css:637-672`
- Modify: `tests/test_staffing_rotations.py:1496-1535,2105-2121`

**Interfaces:**
- Consumes: `bay_model["unassigned"]` and `enabled_auto_work_centers` in the route; the rendered Unscheduled list and `.wc-auto-cb` checkboxes in the browser.
- Produces: `rotation_auto_summary` with `unscheduled_count`, `auto_on_count`, and `delta`, plus `#rotation-auto-summary` whose text is kept current in the browser.
- Leaves: schedule feasibility, warning rendering, Auto-toggle persistence, and rebuild APIs unchanged.

- [ ] **Step 1: Write failing context and static UI contract tests**

  Update `_render_staffing_page` to accept a fake `unassigned` list, then add:

  ```python
  def test_staffing_context_exposes_auto_summary_counts(monkeypatch):
      ctx = _render_staffing_page(
          monkeypatch,
          unassigned=["A", "B", "C"],
          enabled_auto_work_centers={"Repair 1", "Dismantler 1"},
      )
      assert ctx["rotation_auto_summary"] == {
          "unscheduled_count": 3,
          "auto_on_count": 2,
          "delta": -1,
      }
  ```

  Extend the existing static staffing-controls test with these contracts:

  ```python
  assert 'id="rotation-auto-summary"' in html
  assert 'data-unscheduled-count="{{ rotation_auto_summary.unscheduled_count }}"' in html
  assert 'id="rotation-auto-delta"' in html
  assert 'function renderAutoSummary()' in js
  assert 'renderAutoSummary();' in js
  assert '.rotation-auto-summary' in css
  ```

- [ ] **Step 2: Run the new tests to verify they fail**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_staffing_context_exposes_auto_summary_counts \
    tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes -q
  ```

  Expected: the context key and indicator hooks do not yet exist.

- [ ] **Step 3: Implement server render, markup, style, and live refresh**

  In `staffing_page`, after `bay_model` is available, calculate:

  ```python
  unscheduled_count = len(bay_model.get("unassigned") or ())
  auto_on_count = len(enabled_auto_work_centers)
  rotation_auto_summary = {
      "unscheduled_count": unscheduled_count,
      "auto_on_count": auto_on_count,
      "delta": auto_on_count - unscheduled_count,
  }
  ```

  Add `rotation_auto_summary` to the template context. In the `.rotation-mode` block, place a concise `<output>` after the help text with the initial counts and stable IDs:

  ```html
  <output class="rotation-auto-summary" id="rotation-auto-summary"
          data-unscheduled-count="{{ rotation_auto_summary.unscheduled_count }}">
    <span class="rotation-auto-label">Auto mode</span>
    <strong id="rotation-auto-delta">{% if rotation_auto_summary.delta > 0 %}+{% endif %}{{ rotation_auto_summary.delta }}</strong>
    <span><span id="rotation-unscheduled-count">{{ rotation_auto_summary.unscheduled_count }}</span> unscheduled / <span id="rotation-auto-on-count">{{ rotation_auto_summary.auto_on_count }}</span> Auto On</span>
  </output>
  ```

  Add a compact, right-aligned flex style that wraps below the help line on narrow screens. In the rotation-control IIFE add `renderAutoSummary()` that counts `.section.unscheduled ul li:not(.empty)` and checked `.wc-auto-cb` elements, writes those two counts, and formats the signed delta. Call it at IIFE initialization, at the end of `applyEnabledCenters`, and after `syncLeftRailWithSchedule()` in `applyRebuild`. The checkbox save path already calls `applyEnabledCenters` only after the server accepts the change, so failed requests keep the prior count.

- [ ] **Step 4: Run the focused UI tests to verify they pass**

  Run the command from Step 2. Expected: 2 passed.

- [ ] **Step 5: Run the complete focused regression suite**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_rotation_suggestions.py tests/test_schedule_solver.py \
    tests/test_schedule_solver_properties.py tests/test_staffing_rotations.py -q
  ```

  Expected: all selected tests pass.

- [ ] **Step 6: Commit the indicator**

  ```bash
  git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/templates/staffing.html \
    src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css \
    tests/test_staffing_rotations.py
  git commit -m "feat: show auto schedule capacity count"
  ```
