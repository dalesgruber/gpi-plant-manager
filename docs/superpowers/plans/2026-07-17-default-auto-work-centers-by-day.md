# Default Auto Work Centers by Day Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let Settings define the Auto work-center template for new staffing days while persisting each saved day's On/Off set independently.

**Architecture:** Add an ordered `auto_enabled_work_centers` value to `staffing.Schedule` and the `schedules` table. Keep the existing app-setting key as the global default template; first creation copies it to the schedule, and all day-specific reads and writes use the schedule value.

**Tech Stack:** Python 3.12, PostgreSQL JSONB, FastAPI/Jinja, vanilla JavaScript, pytest.

## Global Constraints

- A Settings default applies only when a schedule is first created; no Settings save rewrites an existing schedule.
- An explicit empty daily list means every work center is Off and must remain distinct from a missing legacy value.
- Normalize every persisted list to known work-center names in canonical location order.
- Preserve the existing `rotation_auto_enabled_work_centers` persisted key as the default-template key.
- A daily On/Off save must be transactional with its schedule changes and must not modify the default template.

---

### Task 1: Persist and hydrate a schedule-owned enabled-center list

**Files:**
- Modify: `src/zira_dashboard/_schema.py:191-198,383-390`
- Modify: `src/zira_dashboard/staffing.py:340-365,480-830`
- Modify: `tests/test_rotation_store.py:207-280`

**Interfaces:**
- Produces: `Schedule.auto_enabled_work_centers: list[str]`.
- Produces: `_normalize_auto_enabled_work_centers(value) -> list[str]`.
- Preserves: `snapshot_of(schedule)["auto_enabled_work_centers"]` and schedule load/save/bulk/conditional-create round trips.

- [ ] **Step 1: Write the failing schedule persistence tests**

  Add to `tests/test_rotation_store.py`:

  ```python
  def test_schedule_auto_enabled_work_centers_round_trip(monkeypatch):
      from zira_dashboard import db, staffing

      schedule = staffing.Schedule(
          day=date(2026, 7, 14),
          auto_enabled_work_centers=["Repair 2", "Repair 1", "Unknown", "Repair 1"],
      )
      executed = []

      class Cursor:
          def execute(self, sql, params=None):
              executed.append((sql, params))

      @contextmanager
      def fake_cursor():
          yield Cursor()

      monkeypatch.setattr(db, "cursor", fake_cursor)
      staffing.save_schedule(schedule)

      assert "auto_enabled_work_centers" in executed[0][0]
      assert "[\"Repair 1\", \"Repair 2\"]" in executed[0][1]

      monkeypatch.setattr(db, "query", lambda sql, params=None: [{
          "day": schedule.day, "published": False, "testing_day": False,
          "notes": "", "custom_hours": None, "published_snapshot": None,
          "published_delivery": {}, "recycled_rotation_mode": "normal",
          "assignment_sources": {}, "saturday_availability_overrides": {},
          "auto_enabled_work_centers": ["Repair 2", "Unknown", "Repair 1"],
      }] if "FROM schedules" in sql else [])

      hydrated = staffing._load_schedule_from_db(schedule.day)
      assert hydrated.auto_enabled_work_centers == ["Repair 1", "Repair 2"]
      assert staffing.snapshot_of(hydrated)["auto_enabled_work_centers"] == ["Repair 1", "Repair 2"]
  ```

- [ ] **Step 2: Run the test to verify it fails**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_store.py \
    -k auto_enabled_work_centers -q
  ```

  Expected: FAIL because `Schedule` has no `auto_enabled_work_centers` field.

- [ ] **Step 3: Add schema and model support**

  In `src/zira_dashboard/_schema.py`, after the existing schedule-column `ALTER TABLE` statements and after `app_settings` is created, add the nullable JSONB field and one-time legacy snapshot:

  ```sql
  ALTER TABLE schedules ADD COLUMN IF NOT EXISTS auto_enabled_work_centers JSONB;

  UPDATE schedules
     SET auto_enabled_work_centers = COALESCE(
           (SELECT value FROM app_settings
             WHERE key = 'rotation_auto_enabled_work_centers'),
           '[]'::jsonb
         )
   WHERE auto_enabled_work_centers IS NULL;
  ```

  In `src/zira_dashboard/staffing.py`, add this field to `Schedule` and add a normalizer next to the existing JSON validation helpers:

  ```python
  auto_enabled_work_centers: list[str] = field(default_factory=list)


  def _normalize_auto_enabled_work_centers(value) -> list[str]:
      raw = json.loads(value) if isinstance(value, str) else value
      if not isinstance(raw, list):
          return []
      known = {loc.name for loc in LOCATIONS}
      selected = {str(name).strip() for name in raw if str(name).strip() in known}
      return [loc.name for loc in LOCATIONS if loc.name in selected]
  ```

  Add `auto_enabled_work_centers` to every schedules `SELECT`, the dataclass construction in `_load_schedule_from_db` and `load_schedules_bulk`, both `INSERT` statements, and the `ON CONFLICT` update. Serialize it with `json.dumps(_normalize_auto_enabled_work_centers(schedule.auto_enabled_work_centers))`. Add the normalized list to `snapshot_of` and use it while displaying a posted snapshot.

- [ ] **Step 4: Run focused persistence tests**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_rotation_store.py \
    -k 'schedule_metadata or auto_enabled_work_centers or saturday_availability' -q
  ```

  Expected: PASS.

- [ ] **Step 5: Commit the persistence slice**

  ```bash
  git add src/zira_dashboard/_schema.py src/zira_dashboard/staffing.py tests/test_rotation_store.py
  git commit -m "feat: persist daily auto work centers"
  ```

### Task 2: Make Settings own only the default template

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:97,300-350`
- Modify: `src/zira_dashboard/routes/settings.py:12-25,280-320,893-995`
- Modify: `src/zira_dashboard/templates/settings.html:64-205`
- Modify: `tests/test_settings_group_defaults.py`
- Create: `tests/test_settings_auto_work_centers.py`

**Interfaces:**
- Produces: `_default_auto_work_centers(day: date) -> list[str]`.
- Produces: Settings context key `default_auto_work_centers: list[str]`.
- Consumes: form field `default_auto_work_centers`.
- Preserves: the existing `rotation_auto_enabled_work_centers` database key and recent-history first-run fallback.

- [ ] **Step 1: Write failing Settings tests**

  Create `tests/test_settings_auto_work_centers.py`:

  ```python
  from pathlib import Path


  def test_work_center_settings_render_default_auto_toggle_for_each_location():
      html = Path("src/zira_dashboard/templates/settings.html").read_text()
      assert 'name="default_auto_work_centers"' in html
      assert "default_auto_work_centers" in html
      assert "Default Auto Work Centers" in html


  def test_work_center_settings_save_writes_default_not_daily_state(monkeypatch):
      from zira_dashboard.routes import settings

      saved = []
      monkeypatch.setattr(settings.work_centers_store, "registered_groups", lambda: [])
      monkeypatch.setattr(settings.work_centers_store, "all_group_names", lambda _kind: [])
      monkeypatch.setattr(settings.work_centers_store, "replace_default_targets", lambda **_kwargs: None)
      monkeypatch.setattr(settings.work_centers_store, "save_one", lambda *_args: None)
      monkeypatch.setattr(settings.app_settings, "set_setting", lambda key, value: saved.append((key, value)))

      # The endpoint test harness supplies a form with Repair 2 selected.
      assert settings._ordered_default_auto_work_centers(["Repair 2", "Unknown"]) == ["Repair 2"]
      assert settings.DEFAULT_AUTO_WORK_CENTERS_SETTING == "rotation_auto_enabled_work_centers"
  ```

- [ ] **Step 2: Run Settings tests to verify they fail**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_settings_auto_work_centers.py -q
  ```

  Expected: FAIL because the template, helper, and constant do not exist.

- [ ] **Step 3: Implement explicit default-template reads and saves**

  Rename the route constant to:

  ```python
  DEFAULT_AUTO_WORK_CENTERS_SETTING = "rotation_auto_enabled_work_centers"
  ```

  Replace global-day helpers with these route-local functions in `routes/staffing.py`:

  ```python
  def _default_auto_work_centers(d: date) -> list[str]:
      saved = app_settings.get_setting(DEFAULT_AUTO_WORK_CENTERS_SETTING)
      if isinstance(saved, list):
          return _ordered_work_center_names(saved)
      defaults = _recently_used_work_centers(d)
      app_settings.set_setting(DEFAULT_AUTO_WORK_CENTERS_SETTING, defaults)
      return defaults


  def _save_default_auto_work_centers(names, *, cur=None) -> list[str]:
      enabled = _ordered_work_center_names(names)
      app_settings.set_setting(DEFAULT_AUTO_WORK_CENTERS_SETTING, enabled, cur=cur)
      return enabled
  ```

  Import `app_settings` in `routes/settings.py`. Add `_ordered_default_auto_work_centers(names)` using `staffing.LOCATIONS` order. Add `default_auto_work_centers` to the page context, and in `settings_save_work_centers` save `form.getlist("default_auto_work_centers")` only when its hidden `default_auto_work_centers_present` field is posted.

  Add this panel inside `#wc-form`, before the work-center table:

  ```jinja2
  <section class="default-auto-centers">
    <h3 class="section-title">Default Auto Work Centers</h3>
    <p class="note">Used only when a new staffing day is created. Saved days keep their own On/Off choices.</p>
    <input type="hidden" name="default_auto_work_centers_present" value="1">
    <div class="default-auto-centers-grid">
      {% for row in wc_rows %}
      <label><input type="checkbox" name="default_auto_work_centers" value="{{ row.name }}"
                    {% if row.name in default_auto_work_centers %}checked{% endif %}>{{ row.name }}</label>
      {% endfor %}
    </div>
  </section>
  ```

- [ ] **Step 4: Run the Settings suite**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_settings_auto_work_centers.py tests/test_settings_group_defaults.py -q
  ```

  Expected: PASS.

- [ ] **Step 5: Commit the Settings slice**

  ```bash
  git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/settings.py \
    src/zira_dashboard/templates/settings.html tests/test_settings_auto_work_centers.py \
    tests/test_settings_group_defaults.py
  git commit -m "feat: configure default auto work centers"
  ```

### Task 3: Copy defaults once when a staffing day is created

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:912-959,1035-1070`
- Modify: `tests/test_staffing_rotations.py:2768-3050`

**Interfaces:**
- Consumes: `_default_auto_work_centers(day)` only for an unsaved schedule.
- Produces: a seeded `Schedule(auto_enabled_work_centers=[...])`.
- Preserves: an existing schedule's daily list exactly, including `[]`.

- [ ] **Step 1: Write failing first-view isolation tests**

  Add beside `test_first_future_staffing_view_saves_exact_and_group_defaults`:

  ```python
  def test_first_future_staffing_view_copies_default_auto_work_centers(monkeypatch):
      saved = []
      _render_staffing_page(
          monkeypatch, schedule_revision=None,
          auto_centers={"Repair 1", "Repair 2"}, saved_schedules=saved,
      )
      assert saved[0].auto_enabled_work_centers == ["Repair 1", "Repair 2"]


  def test_saved_day_uses_its_auto_work_centers_not_current_defaults(monkeypatch):
      schedule = staffing.Schedule(
          day=TARGET_DAY, auto_enabled_work_centers=["Repair 3"],
      )
      ctx = _render_staffing_page(
          monkeypatch, saved_schedule=schedule,
          auto_centers={"Repair 1", "Repair 2"},
      )
      assert ctx["auto_schedule_enabled_wc_names"] == ["Repair 3"]
  ```

- [ ] **Step 2: Run the tests to verify they fail**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py \
    -k 'first_future_staffing_view_copies_default or saved_day_uses_its_auto' -q
  ```

  Expected: FAIL because a new schedule does not copy the default and page context still reads the global setting.

- [ ] **Step 3: Implement one-time daily initialization**

  In `_seed_new_future_draft`, read the global default before default placement:

  ```python
  enabled_centers = _default_auto_work_centers(day)
  ```

  Include it in the conditional-create schedule:

  ```python
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
      auto_enabled_work_centers=enabled_centers,
  )
  ```

  After `_seed_new_future_draft` returns, use this exact context derivation in `staffing_page`:

  ```python
  if staffing.schedule_revision(d) is None:
      enabled_auto_work_centers = _default_auto_work_centers(d)
  else:
      enabled_auto_work_centers = list(sched.auto_enabled_work_centers)
  ```

  Keep the try/except fallback empty on read failure. Replace all routine scheduling calls in this page with `enabled_auto_work_centers` rather than rereading the global default.

- [ ] **Step 4: Run focused staffing context tests**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py \
    -k 'first_future_staffing_view or saved_day_uses_its_auto or enabled_auto_work_centers' -q
  ```

  Expected: PASS.

- [ ] **Step 5: Commit the initialization slice**

  ```bash
  git add src/zira_dashboard/routes/staffing.py tests/test_staffing_rotations.py
  git commit -m "feat: seed daily auto work centers from defaults"
  ```

### Task 4: Save and consume On/Off choices per day

**Files:**
- Modify: `src/zira_dashboard/routes/rotations.py:373-451,500-685`
- Modify: `src/zira_dashboard/routes/staffing.py:630-905,1560-1590`
- Modify: `src/zira_dashboard/routes/saturday_recruiting.py:100-115`
- Modify: `src/zira_dashboard/exception_inbox.py:50-85`
- Modify: `tests/test_staffing_rotations.py:1450-1905,2768-3050`
- Modify: `tests/test_saturday_recruiting_manager_routes.py`
- Modify: `tests/test_exception_inbox.py`

**Interfaces:**
- `POST /api/rotations/auto-work-centers` persists `Schedule.auto_enabled_work_centers` for `body.day` and returns the persisted list.
- `_enabled_auto_work_centers(day)` becomes a day-state loader that returns `set(staffing.load_schedule(day).auto_enabled_work_centers)`.
- No per-day caller writes `app_settings`.

- [ ] **Step 1: Write failing day-isolation API test**

  Add to `tests/test_staffing_rotations.py` beside the auto-work-center endpoint tests:

  ```python
  def test_auto_work_center_save_isolated_to_requested_day(monkeypatch):
      client, rotations = _rotations_client(monkeypatch)
      staffing_route = _stub_recommendation_inputs(monkeypatch)
      first = staffing.Schedule(day=date(2026, 7, 14), auto_enabled_work_centers=["Repair 1"])
      second = staffing.Schedule(day=date(2026, 7, 15), auto_enabled_work_centers=["Repair 2"])
      saved = []

      monkeypatch.setattr(rotations.staffing, "load_schedule", lambda d: first if d == first.day else second)
      monkeypatch.setattr(rotations.staffing, "save_schedule", lambda schedule, **_kwargs: saved.append(schedule))
      monkeypatch.setattr(staffing_route.app_settings, "set_setting", lambda *_args, **_kwargs: pytest.fail("daily save must not update defaults"))

      response = client.post("/api/rotations/auto-work-centers", json={
          "day": first.day.isoformat(), "work_centers": ["Repair 3"], "turn_off": [],
      })

      assert response.status_code == 200
      assert saved[-1].day == first.day
      assert saved[-1].auto_enabled_work_centers == ["Repair 3"]
      assert second.auto_enabled_work_centers == ["Repair 2"]
  ```

- [ ] **Step 2: Run the endpoint test to verify it fails**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py \
    -k auto_work_center_save_isolated_to_requested_day -q
  ```

  Expected: FAIL because the endpoint writes the global app setting and does not persist the requested day's list.

- [ ] **Step 3: Persist daily state and migrate all consumers**

  In `save_auto_work_centers`, replace `_save_enabled_auto_work_centers(enabled, cur=cur)` with a schedule replacement and save:

  ```python
  sched = replace(
      sched,
      assignments=assignments,
      auto_enabled_work_centers=enabled,
  )
  staffing.save_schedule(sched, cur=cur)
  ```

  Save even when no assignments were removed, because the enabled list itself changed. Return `list(sched.auto_enabled_work_centers)`.

  Replace global default reads in rebuild, publish validation, Saturday recruiting, and exception inbox code with the daily schedule list. Where a caller already has `sched`, use `sched.auto_enabled_work_centers`; otherwise `_enabled_auto_work_centers(day)` must load the persisted schedule and return its list as a set. Use `_default_auto_work_centers(day)` only for an unsaved day initializer or Settings.

  Update endpoint test stubs that currently monkeypatch `_save_enabled_auto_work_centers` to assert `staffing.save_schedule` receives the day-owned list instead. Keep Saturday recruiting updates inside the same cursor transaction.

- [ ] **Step 4: Run route and downstream regressions**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_staffing_rotations.py tests/test_saturday_recruiting_manager_routes.py \
    tests/test_exception_inbox.py -q
  ```

  Expected: PASS.

- [ ] **Step 5: Run the complete relevant test suite**

  Run:

  ```bash
  ZIRA_API_KEY=test .venv/bin/python -m pytest \
    tests/test_rotation_store.py tests/test_settings_auto_work_centers.py \
    tests/test_settings_group_defaults.py tests/test_staffing_rotations.py \
    tests/test_saturday_recruiting_manager_routes.py tests/test_exception_inbox.py -q
  ```

  Expected: PASS.

- [ ] **Step 6: Commit the per-day behavior**

  ```bash
  git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/rotations.py \
    src/zira_dashboard/routes/saturday_recruiting.py src/zira_dashboard/exception_inbox.py \
    tests/test_staffing_rotations.py tests/test_saturday_recruiting_manager_routes.py \
    tests/test_exception_inbox.py
  git commit -m "fix: isolate auto work centers by staffing day"
  ```
