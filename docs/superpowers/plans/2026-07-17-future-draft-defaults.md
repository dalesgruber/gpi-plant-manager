# Future Draft Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Seed an unsaved future staffing day with a saved default draft while preserving every later saved draft exactly.

**Architecture:** Move the existing default-only placement builder from the rotations route into the staffing route, where both the Reset to defaults endpoint and the Staffing-page initializer can call it without a route-import cycle. The initializer will require a future day and an absent `schedule_revision`, then use the shared builder and save one `default`-sourced draft; read failures leave the blank schedule untouched.

**Tech Stack:** Python 3.12, FastAPI/Jinja, existing staffing/rotation helpers, pytest.

## Global Constraints

- Seed only days strictly after `plant_today()` and only when `staffing.schedule_revision(day)` is `None`.
- A saved row, including a blank draft, is authoritative and must never be overwritten by a page visit.
- Exact defaults remain at their configured work center.
- Group defaults use only their enabled configured members, respect full-day absence, active/reserve status, duplicate prevention, and configured maxima, and break equal-load choices with recycled rotation history.
- Seeded assignments use the `default` source and a draft remains unpublished.
- If any authoritative defaults, capacity, history, or time-off input cannot be read, render the untouched blank schedule and do not save partial data.

---

### Task 1: Share the default-only placement builder

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:417-445`
- Modify: `src/zira_dashboard/routes/rotations.py:452-505,548-570`
- Modify: `tests/test_staffing_rotations.py:760-920`

**Interfaces:**
- Consumes: `Sequence[staffing.Person]`, full-day-off names, exact and group default maps, configured group membership, enabled work centers, configured capacities, and `rotation_suggestions.RecycledHistory`.
- Produces: `staffing_route._defaults_only_assignments(...) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]`.
- Preserves: the Reset to defaults endpoint's response and metadata behavior; it calls the same builder through `staffing_route`.

- [ ] **Step 1: Write the failing shared-builder regression**

  Add this next to the existing reset-default tests in `tests/test_staffing_rotations.py`:

  ```python
  def test_defaults_only_assignments_pins_exact_and_rotates_group_defaults():
      from zira_dashboard.routes import staffing as staffing_route

      assignments, sources = staffing_route._defaults_only_assignments(
          roster=[_person("Pinned", 1), _person("Ana", 1), _person("Bob", 1)],
          full_day_off_names=set(),
          exact_defaults={"Repair 1": ("Pinned",)},
          group_defaults={"Repair": ("Ana", "Bob")},
          user_group_centers={"Repair": ("Repair 1", "Repair 2", "Repair 3")},
          enabled_centers={"Repair 1", "Repair 2", "Repair 3"},
          center_capacities={"Repair 1": 1, "Repair 2": 1, "Repair 3": 1},
          history=rotation_suggestions.RecycledHistory(),
      )

      assert assignments == {
          "Repair 1": ["Pinned"],
          "Repair 2": ["Ana"],
          "Repair 3": ["Bob"],
      }
      assert sources == {
          "Repair 1": {"Pinned": "default"},
          "Repair 2": {"Ana": "default"},
          "Repair 3": {"Bob": "default"},
      }
  ```

- [ ] **Step 2: Run the regression to verify it fails**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_defaults_only_assignments_pins_exact_and_rotates_group_defaults -q
  ```

  Expected: FAIL with `AttributeError` because `_defaults_only_assignments` exists only in `routes.rotations`.

- [ ] **Step 3: Move the builder to its shared owner**

  Add this function after `_default_inputs` in `src/zira_dashboard/routes/staffing.py`:

  ```python
  def _defaults_only_assignments(
      *, roster, full_day_off_names, exact_defaults, group_defaults,
      user_group_centers, enabled_centers, center_capacities, history,
  ) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
      available = {
          person.name for person in roster
          if person.active and not person.reserve and person.name not in full_day_off_names
      }
      assignments: dict[str, list[str]] = {}
      sources: dict[str, dict[str, str]] = {}
      assigned: set[str] = set()

      def place(center: str, name: str) -> None:
          if not center or name not in available or name in assigned:
              return
          assignments.setdefault(center, []).append(name)
          sources.setdefault(center, {})[name] = "default"
          assigned.add(name)

      for center, names in exact_defaults.items():
          for raw_name in names:
              place(str(center).strip(), str(raw_name).strip())

      enabled = set(enabled_centers)
      for group, names in group_defaults.items():
          group_centers = tuple(
              center for center in user_group_centers.get(group, ()) if center in enabled
          )
          for raw_name in names:
              name = str(raw_name).strip()
              available_centers = tuple(
                  center for center in group_centers
                  if center_capacities.get(center) is None
                  or len(assignments.get(center, ())) < center_capacities[center]
              )
              if not available_centers or name not in available or name in assigned:
                  continue
              least_load = min(len(assignments.get(center, ())) for center in available_centers)
              tied_centers = tuple(
                  center for center in available_centers
                  if len(assignments.get(center, ())) == least_load
              )
              place(rotation_suggestions.choose_center(name, str(group), tied_centers, history), name)
      return assignments, sources
  ```

  Delete the identical private `_defaults_only_assignments` definition from
  `src/zira_dashboard/routes/rotations.py`, then change its reset branch to:

  ```python
  assignments, sources = staffing_route._defaults_only_assignments(
      roster=roster,
      full_day_off_names=absent,
      exact_defaults=exact_defaults,
      group_defaults=group_defaults,
      user_group_centers=user_group_centers,
      enabled_centers=enabled_centers,
      center_capacities=center_capacities,
      history=history,
  )
  ```

- [ ] **Step 4: Run the focused shared-builder and reset regressions**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py \
    -k "defaults_only_assignments_pins_exact or reset_to_defaults" -q
  ```

  Expected: PASS, including existing reset distribution, capacity, disabled-center, and absence coverage.

- [ ] **Step 5: Commit the isolated refactor**

  ```bash
  git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
  git commit -m "refactor: share default draft placement"
  ```

### Task 2: Seed only new future drafts on first view

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:860-955`
- Modify: `tests/test_staffing_rotations.py:2655-2845`

**Interfaces:**
- Consumes: `staffing.schedule_revision(day)`, an already loaded blank `staffing.Schedule`, roster and time-off page inputs, plus the shared default builder from Task 1.
- Produces: `_seed_new_future_draft(day, today, schedule, roster, time_off_entries) -> staffing.Schedule`.
- Preserves: the existing saved schedule instance whenever the day is today/past, has a persisted row, or an authoritative lookup raises.

- [ ] **Step 1: Extend the Staffing-page test harness**

  Add `schedule_revision`, `roster`, `time_off_entries`, `default_inputs`,
  `center_capacities`, `history`, and `saved_schedules` keyword arguments to
  `_render_staffing_page`. Use them in its existing stubs:

  ```python
  monkeypatch.setattr(staffing_mod, "load_roster", lambda: list(roster or []))
  monkeypatch.setattr(staffing_mod, "schedule_revision", lambda _day: schedule_revision)
  monkeypatch.setattr(staffing_routes, "_safe_time_off_entries", lambda _day: list(time_off_entries or []))
  monkeypatch.setattr(staffing_routes, "_default_inputs", default_inputs or (lambda strict=False: ({}, {}, {})))
  monkeypatch.setattr(staffing_routes, "_configured_center_capacities", center_capacities or (lambda centers, strict=False: {center: None for center in centers}))
  monkeypatch.setattr(staffing_routes.rotation_suggestions, "_load_recycled_history", history or (lambda *_args, **_kwargs: rotation_suggestions.RecycledHistory()))
  monkeypatch.setattr(staffing_mod, "save_schedule", lambda schedule: saved_schedules.append(schedule) if saved_schedules is not None else None)
  ```

  Leave the default harness `schedule_revision="test"` so existing blank-day
  context tests model an already-saved blank draft.

- [ ] **Step 2: Write the failing first-visit and no-overwrite regressions**

  Replace `test_blank_staffing_day_stays_empty_without_default_or_smart_seed`
  with these tests:

  ```python
  def test_first_future_staffing_view_saves_exact_and_group_defaults(monkeypatch):
      saved = []
      ctx = _render_staffing_page(
          monkeypatch,
          schedule_revision=None,
          roster=[_person("Pinned", 1), _person("Ana", 1), _person("Bob", 1)],
          auto_centers={"Repair 1", "Repair 2", "Repair 3"},
          default_inputs=lambda strict=False: (
              {"Repair 1": ("Pinned",)},
              {"Repair": ("Ana", "Bob")},
              {"Repair": ("Repair 1", "Repair 2", "Repair 3")},
          ),
          center_capacities=lambda centers, strict=False: {center: 1 for center in centers},
          saved_schedules=saved,
      )

      assert len(saved) == 1
      assert saved[0].published is False
      assert saved[0].assignments == {
          "Repair 1": ["Pinned"],
          "Repair 2": ["Ana"],
          "Repair 3": ["Bob"],
      }
      assert saved[0].assignment_sources == {
          "Repair 1": {"Pinned": "default"},
          "Repair 2": {"Ana": "default"},
          "Repair 3": {"Bob": "default"},
      }
      assert ctx["sched"] is saved[0]


  def test_saved_blank_future_draft_is_not_reseeded(monkeypatch):
      saved = []
      blank = staffing.Schedule(day=TARGET_DAY, published=False, assignments={})

      ctx = _render_staffing_page(
          monkeypatch,
          saved_schedule=blank,
          schedule_revision="already-saved",
          roster=[_person("Pinned", 1)],
          auto_centers={"Repair 1"},
          default_inputs=lambda strict=False: ({"Repair 1": ("Pinned",)}, {}, {}),
          saved_schedules=saved,
      )

      assert saved == []
      assert ctx["sched"] is blank
      assert ctx["sched"].assignments == {}


  def test_first_future_staffing_view_keeps_blank_draft_when_defaults_fail(monkeypatch):
      saved = []
      ctx = _render_staffing_page(
          monkeypatch,
          schedule_revision=None,
          roster=[_person("Pinned", 1)],
          default_inputs=lambda strict=False: (_ for _ in ()).throw(RuntimeError("settings offline")),
          saved_schedules=saved,
      )

      assert saved == []
      assert ctx["sched"].assignments == {}
  ```

- [ ] **Step 3: Run the page regressions to verify they fail**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py::test_first_future_staffing_view_saves_exact_and_group_defaults \
    tests/test_staffing_rotations.py::test_saved_blank_future_draft_is_not_reseeded \
    tests/test_staffing_rotations.py::test_first_future_staffing_view_keeps_blank_draft_when_defaults_fail -q
  ```

  Expected: the first test fails because the page leaves a no-row future schedule blank; the second still passes or confirms the preservation baseline.

- [ ] **Step 4: Add the fail-safe page initializer**

  Add this helper before `staffing_page` in `src/zira_dashboard/routes/staffing.py`:

  ```python
  def _seed_new_future_draft(
      day: date,
      today: date,
      sched: staffing.Schedule,
      roster: Sequence[staffing.Person],
      time_off_entries,
  ) -> staffing.Schedule:
      if day <= today or staffing.schedule_revision(day) is not None:
          return sched
      try:
          exact_defaults, group_defaults, user_group_centers = _default_inputs(strict=True)
          enabled_centers = _ordered_work_center_names(_enabled_auto_work_centers(day))
          center_capacities = _configured_center_capacities(enabled_centers, strict=True)
          history = rotation_suggestions._load_recycled_history(
              day,
              group_locations=_auto_history_group_locations(),
              user_group_centers=user_group_centers,
          )
          assignments, sources = _defaults_only_assignments(
              roster=roster,
              full_day_off_names=rotation_suggestions._full_day_time_off_names(time_off_entries),
              exact_defaults=exact_defaults,
              group_defaults=group_defaults,
              user_group_centers=user_group_centers,
              enabled_centers=enabled_centers,
              center_capacities=center_capacities,
              history=history,
          )
      except Exception:
          log.exception("Could not seed default staffing draft for %s", day)
          return sched
      seeded = staffing.Schedule(
          day=day,
          published=False,
          assignments=assignments,
          notes=sched.notes,
          wc_notes=dict(sched.wc_notes),
          testing_day=sched.testing_day,
          custom_hours=sched.custom_hours,
          rotation_mode=sched.rotation_mode,
          assignment_sources=sources,
      )
      staffing.save_schedule(seeded)
      _http_cache.invalidate_today_cache()
      return seeded
  ```

  Immediately after the Staffing page resolves `sched`, `roster`, and
  `time_off_entries` from its pool, before it starts attendance work, call:

  ```python
  sched = _seed_new_future_draft(d, today, sched, roster, time_off_entries)
  ```

- [ ] **Step 5: Run the focused draft-init and reset coverage**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py \
    -k "first_future_staffing_view or saved_blank_future_draft or reset_to_defaults" -q
  ```

  Expected: PASS.

- [ ] **Step 6: Run adjacent scheduler route coverage**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py tests/test_staffing_trim_saw_defaults.py \
    tests/test_staffing_schedule_metadata.py -q
  ```

  Expected: PASS with zero failures.

- [ ] **Step 7: Commit the behavior change**

  ```bash
  git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
  git commit -m "fix: seed future drafts with defaults"
  ```

### Task 3: Verify the delivered behavior

**Files:**
- Verify: `src/zira_dashboard/routes/staffing.py`
- Verify: `src/zira_dashboard/routes/rotations.py`
- Verify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Consumes: the shared builder and future-draft initializer from Tasks 1–2.
- Produces: fresh evidence that all specified lifecycle and placement rules hold.

- [ ] **Step 1: Run the full scheduling regression suite**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py tests/test_staffing_trim_saw_defaults.py \
    tests/test_staffing_schedule_metadata.py tests/test_staffing_static.py -q
  ```

  Expected: PASS with zero failures.

- [ ] **Step 2: Inspect the implementation diff for unintended changes**

  Run:

  ```bash
  git diff HEAD~2..HEAD --check
  git diff HEAD~2..HEAD -- src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
  ```

  Expected: no whitespace errors and only shared-default placement, first-view seeding, and their regressions.

- [ ] **Step 3: Push the verified commits**

  Run:

  ```bash
  git push origin main
  ```

  Expected: the shared `main` branch advances with the verified future-draft behavior.
