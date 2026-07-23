# Saturday Recruiting Scheduling Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Saturdays start blank, recruit only from manager-selected work centers, surface closed recruiting in the Inbox, seed committed recruits into defaults once, and let the manager optionally Auto-place the remaining committed crew.

**Architecture:** Extend the existing Postgres recruiting lifecycle with a one-time `staffing_prepared_at` marker. Keep all state transitions explicit: selecting centers only saves center state, Recruit only opens responses, the deadline worker only closes responses, opening a closed Saturday performs one transactional defaults-only preparation, and the existing schedule-goal buttons are the only path that invokes Auto.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, vanilla JavaScript/CSS, PostgreSQL/psycopg2, pytest, Ruff.

## Global Constraints

- A new Saturday has no assignments and no enabled Auto work centers.
- Recruiting starts only when a manager presses `Recruit N`.
- The recruiting deadline may close responses but must not prepare or Auto-build a schedule.
- Closed, unprepared recruiting must create an Exception Inbox action.
- Opening the closed Saturday places only effectively available committed recruits into valid enabled defaults, exactly once.
- Auto runs only from the existing manager-pressed schedule-goal controls and may consider only effectively available committed recruits.
- Manual scheduling remains available after preparation.
- Weekday seeding and Reset-to-defaults behavior remain unchanged.
- Legacy seeded defaults may be cleared during recruiting activation; manual, generated, and unknown-source assignments must remain protected.
- Preserve the unrelated untracked `uv.lock`; never stage or edit it for this work.

---

## File Structure

- `src/zira_dashboard/_schema.py`: add the idempotent database column for one-time preparation.
- `src/zira_dashboard/saturday_recruiting_store.py`: own recruiting lifecycle reads, legacy-default cleanup, pending-Inbox lookup, and prepared-marker writes.
- `src/zira_dashboard/routes/staffing.py`: seed Saturdays blank, prepare a closed Saturday once, and expose the UI readiness flags.
- `src/zira_dashboard/routes/rotations.py`: reject Auto while recruiting is open and scope closed-Saturday Auto to committed recruits.
- `src/zira_dashboard/exception_inbox.py`: shape the closed-recruiting action for summary and queue views.
- `src/zira_dashboard/inbox_keys.py`: provide the stable Inbox identity for a Saturday recruitment.
- `src/zira_dashboard/templates/staffing.html`: render `Recruit N`, use Publish-compatible classes, and hide schedule-goal controls until preparation.
- `src/zira_dashboard/static/staffing.js`: keep the live Recruit count copy in sync after work-center toggles.
- `src/zira_dashboard/static/saturday-recruiting.css`: apply the blue Publish-compatible Recruit treatment.
- `tests/test_saturday_recruiting_schema.py`: schema and value-object contracts.
- `tests/test_saturday_recruiting_store.py`: transactional lifecycle and legacy cleanup.
- `tests/test_staffing_rotations.py`: blank Saturday seed, one-time preparation, and committed-only Auto.
- `tests/test_staffing_saturday_recruiting.py`: preparation failure and availability behavior.
- `tests/test_exception_inbox.py`: Inbox summary/queue behavior.
- `tests/test_saturday_recruiting_static.py`: Recruit label, class, live copy, and control visibility.

---

### Task 1: Persist preparation state and protect activation

**Files:**
- Modify: `src/zira_dashboard/_schema.py:970`
- Modify: `src/zira_dashboard/saturday_recruiting_store.py:37`
- Modify: `tests/test_saturday_recruiting_schema.py`
- Modify: `tests/test_saturday_recruiting_store.py`

**Interfaces:**
- Produces: `Recruitment.staffing_prepared_at: datetime | None`.
- Produces: `list_closed_unprepared(start_day: date) -> tuple[RecruitmentBundle, ...]`.
- Produces: `mark_staffing_prepared(day: date, now: datetime, *, cur) -> RecruitmentBundle`.
- Preserves: `activate(...) -> RecruitmentBundle`, now clearing only source-proven legacy defaults.

- [ ] **Step 1: Write failing schema and lifecycle tests**

Add these tests:

```python
import json


def test_schema_defines_saturday_staffing_preparation_marker():
    assert "ALTER TABLE saturday_recruitments" in SCHEMA_DDL
    assert (
        "ADD COLUMN IF NOT EXISTS staffing_prepared_at TIMESTAMPTZ"
        in SCHEMA_DDL
    )


def test_closed_unprepared_rounds_exclude_prepared_and_old_saturdays():
    first = _activate()
    store.close_due(DEADLINE)
    assert [item.recruitment.day for item in store.list_closed_unprepared(SATURDAY)] == [
        SATURDAY
    ]
    with db.cursor() as cur:
        store.mark_staffing_prepared(SATURDAY, DEADLINE, cur=cur)
    assert store.list_closed_unprepared(SATURDAY) == ()


def test_activate_clears_only_legacy_seeded_defaults():
    db.execute(
        "INSERT INTO schedules (day, assignment_sources) "
        "VALUES (%s, %s::jsonb)",
        (SATURDAY, '{"Saturday Test Repair":{"Saturday Test Volunteer":"default"}}'),
    )
    db.execute(
        "INSERT INTO schedule_assignments (day, wc_id, person_id) "
        "VALUES (%s, 910101, 910101)",
        (SATURDAY,),
    )
    _activate()
    assert db.query(
        "SELECT 1 FROM schedule_assignments WHERE day = %s", (SATURDAY,)
    ) == []


@pytest.mark.parametrize("source", ["manual", "generated", None])
def test_activate_preserves_and_rejects_nondefault_assignments(source):
    sources = (
        {}
        if source is None
        else {"Saturday Test Repair": {"Saturday Test Volunteer": source}}
    )
    db.execute(
        "INSERT INTO schedules (day, assignment_sources) VALUES (%s, %s::jsonb)",
        (SATURDAY, json.dumps(sources)),
    )
    db.execute(
        "INSERT INTO schedule_assignments (day, wc_id, person_id) "
        "VALUES (%s, 910101, 910101)",
        (SATURDAY,),
    )
    with pytest.raises(store.LifecycleConflict):
        _activate()
    assert db.query(
        "SELECT 1 FROM schedule_assignments WHERE day = %s", (SATURDAY,)
    )
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_saturday_recruiting_schema.py::test_schema_defines_saturday_staffing_preparation_marker \
  tests/test_saturday_recruiting_store.py::test_closed_unprepared_rounds_exclude_prepared_and_old_saturdays \
  tests/test_saturday_recruiting_store.py::test_activate_clears_only_legacy_seeded_defaults \
  tests/test_saturday_recruiting_store.py::test_activate_preserves_and_rejects_nondefault_assignments
```

Expected: failures for the missing column, missing store methods, and current blanket assignment rejection.

- [ ] **Step 3: Add the schema column and lifecycle field**

Add the idempotent migration after the recruiting table:

```sql
ALTER TABLE saturday_recruitments
  ADD COLUMN IF NOT EXISTS staffing_prepared_at TIMESTAMPTZ;
```

Extend the value object without breaking existing positional test fixtures:

```python
@dataclass(frozen=True)
class Recruitment:
    day: date
    status: str
    shift_start: time
    shift_end: time
    response_deadline: datetime
    staffing_prepared_at: datetime | None = None
```

Include `staffing_prepared_at` in `_load_bundle` and `_lock_recruitment` SELECTs and constructors.

- [ ] **Step 4: Implement source-aware legacy cleanup and preparation methods**

Add a JSON normalizer and replace the blanket assignment guard:

```python
def _source_mapping(value) -> dict[str, dict[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _clear_legacy_seeded_defaults(cur, day: date, raw_sources) -> None:
    cur.execute(
        "SELECT wc.name AS wc_name, p.name AS person_name "
        "FROM schedule_assignments sa "
        "JOIN work_centers wc ON wc.id = sa.wc_id "
        "JOIN people p ON p.id = sa.person_id WHERE sa.day = %s",
        (day,),
    )
    rows = cur.fetchall()
    if not rows:
        return
    sources = _source_mapping(raw_sources)
    if not all(
        sources.get(row["wc_name"], {}).get(row["person_name"]) == "default"
        for row in rows
    ):
        raise LifecycleConflict(
            "Clear existing Saturday assignments before activating recruiting."
        )
    cur.execute("DELETE FROM schedule_assignments WHERE day = %s", (day,))
    cur.execute(
        "UPDATE schedules SET assignment_sources = '{}'::jsonb, updated_at = now() "
        "WHERE day = %s",
        (day,),
    )
```

Change activation's locked schedule read to select `published, assignment_sources`
and call `_clear_legacy_seeded_defaults`.

Add:

```python
def list_closed_unprepared(start_day: date) -> tuple[RecruitmentBundle, ...]:
    from . import db
    with db.cursor() as cur:
        cur.execute(
            "SELECT day FROM saturday_recruitments "
            "WHERE status = 'closed' AND staffing_prepared_at IS NULL "
            "AND day >= %s ORDER BY day",
            (start_day,),
        )
        return tuple(
            bundle
            for row in cur.fetchall()
            if (bundle := _load_bundle(cur, row["day"])) is not None
        )


def mark_staffing_prepared(
    day: date, now: datetime, *, cur
) -> RecruitmentBundle:
    recruitment = _lock_recruitment(cur, day)
    if recruitment.status != "closed":
        raise LifecycleConflict("Saturday recruiting must close before scheduling")
    if recruitment.staffing_prepared_at is None:
        cur.execute(
            "UPDATE saturday_recruitments "
            "SET staffing_prepared_at = %s, updated_at = %s WHERE day = %s",
            (now, now, day),
        )
    bundle = _load_bundle(cur, day)
    assert bundle is not None
    return bundle
```

- [ ] **Step 5: Run focused tests and the store suite**

Run:

```bash
pytest -q tests/test_saturday_recruiting_schema.py tests/test_saturday_recruiting_store.py
```

Expected: all non-Postgres tests pass; Postgres-marked tests pass when `DATABASE_URL` is configured and otherwise skip.

- [ ] **Step 6: Commit Task 1**

```bash
git add src/zira_dashboard/_schema.py \
  src/zira_dashboard/saturday_recruiting_store.py \
  tests/test_saturday_recruiting_schema.py \
  tests/test_saturday_recruiting_store.py
git commit -m "feat: track Saturday staffing preparation"
git push origin main
```

---

### Task 2: Start Saturdays blank and simplify the Recruit button

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:990`
- Modify: `src/zira_dashboard/templates/staffing.html:199`
- Modify: `src/zira_dashboard/static/staffing.js:1721`
- Modify: `src/zira_dashboard/static/saturday-recruiting.css`
- Modify: `tests/test_staffing_rotations.py:3238`
- Modify: `tests/test_saturday_recruiting_static.py`

**Interfaces:**
- Consumes: existing `staffing.Schedule`.
- Produces: future Saturdays persisted with empty assignments, sources, and enabled centers.
- Produces: dynamic `Recruit N` button copy.

- [ ] **Step 1: Write failing Saturday seed and static UI tests**

Add:

```python
def test_first_future_saturday_starts_blank_with_every_center_off(monkeypatch):
    saved = []
    ctx = _render_staffing_page(
        monkeypatch,
        day=date(2026, 7, 18),
        schedule_revision=None,
        roster=[_person("Pinned", 3)],
        auto_centers={"Repair 1"},
        default_inputs=lambda strict=False: ({"Repair 1": ("Pinned",)}, {}, {}),
        saved_schedules=saved,
    )
    assert saved[0].assignments == {}
    assert saved[0].assignment_sources == {}
    assert saved[0].auto_enabled_work_centers == []
    assert ctx["sched"] is saved[0]
```

Replace the old Recruit copy assertion with:

```python
assert (
    'class="publish-btn saturday-recruit-button"' in template
)
assert (
    'Recruit <span data-saturday-recruit-demand>'
    '{{ saturday_recruit_enabled_count }}</span>' in template
)
assert "work centers</span>" not in template
assert "demand.textContent = String((enabledCenters || []).length);" in js
assert "button.hidden = count === 0;" in js
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_staffing_rotations.py::test_first_future_saturday_starts_blank_with_every_center_off \
  tests/test_saturday_recruiting_static.py
```

Expected: Saturday is seeded with weekday defaults and the template still says “Recruit for N work centers.”

- [ ] **Step 3: Branch future-day seeding for Saturday**

Inside `_seed_new_future_draft`, keep the revision check and implement:

```python
if day.weekday() == 5:
    assignments: dict[str, list[str]] = {}
    sources: dict[str, dict[str, str]] = {}
    enabled_centers: list[str] = []
else:
    enabled_centers = _default_auto_work_centers(day)
    assignments, sources = defaults_only_schedule(
        day, roster, time_off_entries, enabled_centers,
    )
```

Do not change weekday tests or Reset-to-defaults.

- [ ] **Step 4: Update the Recruit markup and live count**

Change the branch condition from
`day_is_saturday and not saturday_recruiting and saturday_recruit_enabled_count`
to `day_is_saturday and not saturday_recruiting`. Keep the button in the DOM
so the first work-center toggle can reveal it:

```html
<button type="button" class="publish-btn saturday-recruit-button"
        data-saturday-action="activate-from-schedule" data-day="{{ day }}"
        {% if not saturday_recruit_enabled_count %}hidden disabled{% endif %}>
  Recruit <span data-saturday-recruit-demand>{{ saturday_recruit_enabled_count }}</span>
</button>
```

In `renderSaturdayRecruitingDemand`, use:

```javascript
if (!bundle) {
  const count = (enabledCenters || []).length;
  demand.textContent = String(count);
  const button = demand.closest('[data-saturday-action="activate-from-schedule"]');
  if (button) {
    button.hidden = count === 0;
    button.disabled = count === 0;
  }
  return;
}
```

Keep the closed/open response-summary branch unchanged.

- [ ] **Step 5: Keep the button blue while inheriting Publish geometry**

Use a title-bar-specific override:

```css
.title-bar .saturday-recruit-button {
  background: #2563eb;
  border-color: #2563eb;
  color: #fff;
}

.title-bar .saturday-recruit-button:hover,
.title-bar .saturday-recruit-button:focus-visible {
  background: #1d4ed8;
  border-color: #1d4ed8;
  filter: none;
}
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
pytest -q \
  tests/test_staffing_rotations.py::test_first_future_staffing_view_seeds_and_persists_only_defaults \
  tests/test_staffing_rotations.py::test_first_future_saturday_starts_blank_with_every_center_off \
  tests/test_saturday_recruiting_static.py \
  tests/test_staffing_static.py
```

Expected: all pass, proving weekday seeding is unchanged.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/zira_dashboard/routes/staffing.py \
  src/zira_dashboard/templates/staffing.html \
  src/zira_dashboard/static/staffing.js \
  src/zira_dashboard/static/saturday-recruiting.css \
  tests/test_staffing_rotations.py \
  tests/test_saturday_recruiting_static.py
git commit -m "fix: start Saturday recruiting from a blank schedule"
git push origin main
```

---

### Task 3: Add the closed-recruiting Inbox action

**Files:**
- Modify: `src/zira_dashboard/inbox_keys.py`
- Modify: `src/zira_dashboard/exception_inbox.py`
- Modify: `tests/test_exception_inbox.py`

**Interfaces:**
- Consumes: `list_closed_unprepared(start_day)`.
- Produces: `inbox_keys.saturday_recruitment(day) -> str`.
- Produces: a `saturday_recruiting` Inbox section with generic `href` rows.

- [ ] **Step 1: Write failing Inbox summary and snapshot tests**

Add a bundle fixture and tests:

```python
def _closed_saturday_bundle():
    recruitment = SimpleNamespace(
        day=date(2026, 7, 25),
        status="closed",
        staffing_prepared_at=None,
    )
    commitments = (
        SimpleNamespace(status="committed"),
        SimpleNamespace(status="committed"),
        SimpleNamespace(status="declined"),
    )
    return SimpleNamespace(recruitment=recruitment, commitments=commitments)


def test_closed_saturday_recruiting_adds_schedule_action(monkeypatch):
    _empty_inbox_sources(monkeypatch)
    monkeypatch.setattr(
        exception_inbox.saturday_recruiting_store,
        "list_closed_unprepared",
        lambda _today: (_closed_saturday_bundle(),),
    )
    snapshot = exception_inbox.build_snapshot()
    section = next(
        item for item in snapshot["sections"]
        if item["id"] == "saturday_recruiting"
    )
    assert section["count"] == 1
    assert section["rows"][0]["detail"] == "2 committed · Ready to schedule"
    assert section["rows"][0]["href"] == "/staffing?day=2026-07-25"
    assert section["rows"][0]["item_key"] == "saturday_recruitment:2026-07-25"
    assert snapshot["total"] == 1


def test_closed_saturday_recruiting_counts_in_summary(monkeypatch):
    _empty_inbox_sources(monkeypatch)
    monkeypatch.setattr(
        exception_inbox.saturday_recruiting_store,
        "list_closed_unprepared",
        lambda _today: (_closed_saturday_bundle(),),
    )
    summary = exception_inbox.build_summary()
    assert summary["sections"]["saturday_recruiting"] == 1
    assert summary["total"] == 1
```

Update existing exact section-count expectations to include
`"saturday_recruiting": 0`, and centralize empty-source monkeypatching so unit
tests never depend on a live database.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_exception_inbox.py::test_closed_saturday_recruiting_adds_schedule_action \
  tests/test_exception_inbox.py::test_closed_saturday_recruiting_counts_in_summary
```

Expected: missing store import, key helper, and section.

- [ ] **Step 3: Add the canonical key and row shaper**

Add:

```python
def saturday_recruitment(day) -> str:
    return f"saturday_recruitment:{day}"
```

Import `saturday_recruiting_store` in `exception_inbox.py` and add:

```python
def _saturday_staffing_actions(today: date) -> tuple[int, list[dict]]:
    bundles = saturday_recruiting_store.list_closed_unprepared(today)
    rows = []
    for bundle in bundles:
        day = bundle.recruitment.day
        committed = sum(
            item.status == "committed" for item in bundle.commitments
        )
        rows.append({
            "name": "Saturday recruitment",
            "label": day.strftime("%A, %b %-d"),
            "detail": f"{committed} committed · Ready to schedule",
            "priority": "warn",
            "badge": "Schedule",
            "href": f"/staffing?day={day.isoformat()}",
            "row_key": _row_key("saturday_recruitment", day.isoformat()),
            "item_key": inbox_keys.saturday_recruitment(day.isoformat()),
        })
    return len(rows), rows
```

- [ ] **Step 4: Wire summary and snapshot**

Read the source through `_capture` in both builders, add its count to totals and
the summary `sections` mapping, and insert this section immediately after Plant
Schedule:

```python
{
    "id": "saturday_recruiting",
    "title": "Saturday Recruiting",
    "count": saturday_count,
    "tone": "warn",
    "action_key": None,
    "action_label": None,
    "href": saturday_rows[0]["href"] if saturday_rows else "/staffing",
    "empty": "All clear",
    "context": {},
    "rows": saturday_rows,
}
```

- [ ] **Step 5: Run the Inbox suite**

Run:

```bash
pytest -q tests/test_exception_inbox.py tests/test_exception_inbox_breakdown.py \
  tests/test_exception_inbox_breakdown_template.py
```

Expected: all pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add src/zira_dashboard/inbox_keys.py \
  src/zira_dashboard/exception_inbox.py \
  tests/test_exception_inbox.py
git commit -m "feat: prompt managers to schedule closed Saturday recruiting"
git push origin main
```

---

### Task 4: Prepare committed defaults exactly once

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`
- Modify: `src/zira_dashboard/templates/staffing.html`
- Modify: `tests/test_staffing_saturday_recruiting.py`
- Modify: `tests/test_staffing_rotations.py`
- Modify: `tests/test_saturday_recruiting_static.py`

**Interfaces:**
- Consumes: `Recruitment.staffing_prepared_at`.
- Consumes: `mark_staffing_prepared(day, now, *, cur)`.
- Produces: `_prepare_closed_saturday_schedule(...) -> staffing.Schedule`.
- Produces: `_saturday_defaults_only_schedule(...) -> tuple[assignments, sources]`.
- Produces: template context `saturday_staffing_prepared: bool`.

- [ ] **Step 1: Write failing one-time preparation tests**

Add route-helper tests:

```python
from contextlib import nullcontext


def test_closed_saturday_preparation_places_only_committed_enabled_defaults(monkeypatch):
    schedule = staffing.Schedule(
        day=SATURDAY,
        assignments={},
        auto_enabled_work_centers=["Repair 1"],
    )
    bundle = SimpleNamespace(
        recruitment=SimpleNamespace(
            status="closed",
            shift_start=time(6),
            shift_end=time(12),
            staffing_prepared_at=None,
        ),
        commitments=(
            SimpleNamespace(
                person_name="Ana", status="committed",
                availability_start=time(6), availability_end=time(12),
            ),
            SimpleNamespace(
                person_name="Bob", status="declined",
                availability_start=None, availability_end=None,
            ),
        ),
    )
    saved = []
    marked = []
    monkeypatch.setattr(
        staffing_routes, "_saturday_defaults_only_schedule",
        lambda day, roster, time_off, enabled: (
            {"Repair 1": ["Ana"]}, {"Repair 1": {"Ana": "default"}},
        ),
    )
    monkeypatch.setattr(
        staffing_routes.staffing, "load_schedule_for_update",
        lambda day, *, cur: schedule,
    )
    monkeypatch.setattr(
        staffing_routes.staffing,
        "load_schedule",
        lambda day: schedule,
    )
    monkeypatch.setattr(
        staffing_routes.saturday_recruiting_store,
        "get",
        lambda day, *, cur=None: bundle,
    )
    monkeypatch.setattr(
        staffing_routes.staffing,
        "save_schedule",
        lambda value, *, cur=None: saved.append(value),
    )
    monkeypatch.setattr(
        staffing_routes.saturday_recruiting_store,
        "mark_staffing_prepared",
        lambda day, now, *, cur: marked.append(day),
    )
    monkeypatch.setattr(
        staffing_routes.db,
        "cursor",
        lambda: nullcontext(object()),
    )

    result = staffing_routes._prepare_closed_saturday_schedule(
        SATURDAY,
        [_person("Ana", Repair=3), _person("Bob", Repair=3)],
        [],
    )

    assert saved[0].assignments == {"Repair 1": ["Ana"]}
    assert marked == [SATURDAY]
    assert result is saved[0]


def test_prepared_saturday_does_not_reapply_defaults(monkeypatch):
    bundle = SimpleNamespace(
        recruitment=SimpleNamespace(
            status="closed", staffing_prepared_at=datetime(2026, 7, 24, 8),
        ),
    )
    schedule = staffing.Schedule(day=SATURDAY, assignments={})
    monkeypatch.setattr(
        staffing_routes.saturday_recruiting_store,
        "get",
        lambda day, *, cur=None: bundle,
    )
    monkeypatch.setattr(
        staffing_routes.staffing, "load_schedule", lambda day: schedule,
    )
    assert staffing_routes._prepare_closed_saturday_schedule(
        SATURDAY, [], [],
    ) is schedule
```

Add a page-render test asserting the helper is invoked after the closed bundle
is loaded and its returned schedule is used to build the Saturday model.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_staffing_saturday_recruiting.py::test_closed_saturday_preparation_places_only_committed_enabled_defaults \
  tests/test_staffing_saturday_recruiting.py::test_prepared_saturday_does_not_reapply_defaults
```

Expected: `_prepare_closed_saturday_schedule` does not exist.

- [ ] **Step 3: Add an enabled-center-safe Saturday defaults helper**

Reuse the existing defaults engine with exact defaults scoped to the manager's
Saturday selection:

```python
def _saturday_defaults_only_schedule(
    day: date,
    roster: Sequence[staffing.Person],
    time_off_entries,
    enabled_centers,
) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
    enabled = _ordered_work_center_names(enabled_centers)
    enabled_set = set(enabled)
    exact_defaults, group_defaults, user_group_centers = _default_inputs(
        strict=True
    )
    exact_defaults = {
        center: names
        for center, names in exact_defaults.items()
        if center in enabled_set
    }
    user_group_centers = {
        group: tuple(center for center in centers if center in enabled_set)
        for group, centers in user_group_centers.items()
    }
    center_capacities = _configured_center_capacities(enabled, strict=True)
    history = rotation_suggestions._load_recycled_history(
        day,
        group_locations=_auto_history_group_locations(),
        user_group_centers=user_group_centers,
    )
    return _defaults_only_assignments(
        roster=roster,
        full_day_off_names=rotation_suggestions._full_day_time_off_names(
            time_off_entries
        ),
        exact_defaults=exact_defaults,
        group_defaults=group_defaults,
        user_group_centers=user_group_centers,
        enabled_centers=enabled,
        center_capacities=center_capacities,
        history=history,
    )
```

- [ ] **Step 4: Implement transactional preparation**

Add a helper that reloads both rows under one database transaction:

```python
def _prepare_closed_saturday_schedule(
    day: date,
    roster,
    time_off_entries,
) -> staffing.Schedule:
    current = staffing.load_schedule(day)
    with db.cursor() as cur:
        bundle = saturday_recruiting_store.get(day, cur=cur)
        if (
            bundle is None
            or bundle.recruitment.status != "closed"
            or bundle.recruitment.staffing_prepared_at is not None
        ):
            return current
        locked = staffing.load_schedule_for_update(day, cur=cur)
        if locked is None:
            raise RuntimeError("Saturday schedule row is missing")
        if locked.assignments:
            raise RuntimeError(
                "Saturday recruiting schedule is not blank before preparation"
            )
        commitments = {
            item.person_name: {
                "start": item.availability_start,
                "end": item.availability_end,
            }
            for item in bundle.commitments
            if item.status == "committed"
        }
        effective = staffing.effective_saturday_commitments(
            commitments,
            locked.saturday_availability_overrides,
            bundle.recruitment.shift_start,
            bundle.recruitment.shift_end,
        )
        committed_roster = [
            person for person in roster if person.name in effective
        ]
        assignments, sources = _saturday_defaults_only_schedule(
            day,
            committed_roster,
            time_off_entries,
            locked.auto_enabled_work_centers,
        )
        prepared = staffing.Schedule(
            day=day,
            published=locked.published,
            assignments=assignments,
            notes=locked.notes,
            wc_notes=dict(locked.wc_notes),
            testing_day=locked.testing_day,
            custom_hours=locked.custom_hours,
            published_snapshot=locked.published_snapshot,
            published_delivery=locked.published_delivery,
            rotation_mode=locked.rotation_mode,
            assignment_sources=sources,
            auto_enabled_work_centers=list(locked.auto_enabled_work_centers),
            saturday_availability_overrides=dict(
                locked.saturday_availability_overrides
            ),
        )
        staffing.save_schedule(prepared, cur=cur)
        saturday_recruiting_store.mark_staffing_prepared(
            day, plant_now(), cur=cur,
        )
    staffing.invalidate_schedule_cache(day)
    _bust_after_mutation()
    return prepared
```

Catch preparation failures in the page route, log them, retain the blank
schedule, and leave the marker unset so the Inbox action remains open.

- [ ] **Step 5: Call preparation only after recruiting closes**

After loading the Saturday bundle and before building `saturday_context`:

```python
if (
    saturday_bundle is not None
    and saturday_bundle.recruitment.status == "closed"
    and saturday_bundle.recruitment.staffing_prepared_at is None
):
    try:
        sched = _prepare_closed_saturday_schedule(d, roster, time_off_entries)
        saturday_bundle = saturday_recruiting_store.get(d)
    except Exception:
        log.exception("Could not prepare closed Saturday recruiting for %s", d)
```

Set:

```python
"saturday_staffing_prepared": bool(
    saturday_bundle
    and saturday_bundle.recruitment.staffing_prepared_at is not None
),
```

- [ ] **Step 6: Keep the recruiting-open grid blank and hide Auto**

In `_staffing_save_work`, allow blank metadata autosaves but reject any attempt
to persist staffing assignments while responses are still open:

```python
if (
    saturday_bundle is not None
    and saturday_bundle.recruitment.status == "recruiting"
    and assignments
):
    return JSONResponse(
        {
            "ok": False,
            "error": "Saturday recruiting must close before scheduling people.",
        },
        status_code=409,
    )
```

Change the template condition:

```jinja2
{% if auto_scheduler_available
      and (not day_is_saturday or saturday_staffing_prepared) %}
```

This leaves work-center row toggles usable before recruiting but prevents the
schedule-goal buttons from invoking Auto while recruiting is open.

- [ ] **Step 7: Run preparation and rendering tests**

Run:

```bash
pytest -q tests/test_staffing_saturday_recruiting.py \
  tests/test_staffing_rotations.py \
  tests/test_saturday_recruiting_static.py
```

Expected: all pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add src/zira_dashboard/routes/staffing.py \
  src/zira_dashboard/templates/staffing.html \
  tests/test_staffing_saturday_recruiting.py \
  tests/test_staffing_rotations.py \
  tests/test_saturday_recruiting_static.py
git commit -m "feat: prepare closed Saturday recruiting defaults"
git push origin main
```

---

### Task 5: Restrict optional Saturday Auto to committed recruits

**Files:**
- Modify: `src/zira_dashboard/routes/rotations.py:557`
- Modify: `tests/test_staffing_rotations.py`

**Interfaces:**
- Consumes: closed and prepared `RecruitmentBundle`.
- Produces: `_saturday_auto_roster(...) -> list[staffing.Person]`.
- Preserves: weekday Auto behavior and existing JSON response shape.

- [ ] **Step 1: Write failing Auto guard and roster-scope tests**

Add route tests:

```python
from datetime import datetime


def test_rebuild_rejects_open_saturday_recruiting(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    _stub_recommendation_inputs(monkeypatch)
    monkeypatch.setattr(
        rotations.saturday_recruiting_store,
        "get",
        lambda day: SimpleNamespace(
            recruitment=SimpleNamespace(
                status="recruiting", staffing_prepared_at=None,
            ),
            commitments=(),
        ),
    )
    response = client.post("/api/rotations/rebuild", json={
        "day": "2026-07-18", "mode": "normal",
        "reset_to_defaults": False,
    })
    assert response.status_code == 409
    assert "must close" in response.json()["error"]


def test_closed_saturday_auto_passes_only_effective_committed_roster(monkeypatch):
    client, rotations = _rotations_client(monkeypatch)
    _stub_recommendation_inputs(monkeypatch)
    captured = {}
    bundle = SimpleNamespace(
        recruitment=SimpleNamespace(
            status="closed",
            staffing_prepared_at=datetime(2026, 7, 17, 8),
            shift_start=time(6),
            shift_end=time(12),
        ),
        commitments=(
            SimpleNamespace(
                person_name="Ana",
                status="committed",
                availability_start=time(6),
                availability_end=time(12),
            ),
        ),
    )
    monkeypatch.setattr(
        rotations.saturday_recruiting_store, "get", lambda day: bundle,
    )
    monkeypatch.setattr(
        rotations.staffing,
        "load_roster",
        lambda: [_person("Ana", 3), _person("Bob", 3)],
    )
    def capture_suggestion(day, roster, *args, **kwargs):
        captured["names"] = [person.name for person in roster]
        captured["minimum_only"] = kwargs["minimum_only"]
        return rotation_suggestions.RecycledSuggestion({}, {}, {}, ())

    monkeypatch.setattr(
        rotations.staffing_route,
        "_recycled_suggestion_for_day",
        capture_suggestion,
    )
    response = client.post("/api/rotations/rebuild", json={
        "day": "2026-07-18", "mode": "normal",
        "reset_to_defaults": False,
    })
    assert response.status_code == 200
    assert captured["names"] == ["Ana"]
    assert captured["minimum_only"] is False
```

Also assert the Saturday call passes `minimum_only=False`, while an existing
weekday route test continues to assert `minimum_only=True`.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
pytest -q \
  tests/test_staffing_rotations.py::test_rebuild_rejects_open_saturday_recruiting \
  tests/test_staffing_rotations.py::test_closed_saturday_auto_passes_only_effective_committed_roster
```

Expected: open recruiting currently reaches the solver and the full roster is
currently passed on Saturday.

- [ ] **Step 3: Add effective-commitment derivation**

Add:

```python
def _saturday_auto_roster(day: date, roster, schedule):
    bundle = saturday_recruiting_store.get(day)
    if bundle is None:
        return roster, None
    if bundle.recruitment.status == "recruiting":
        raise saturday_recruiting_store.LifecycleConflict(
            "Saturday recruiting must close before Auto scheduling."
        )
    if (
        bundle.recruitment.status != "closed"
        or bundle.recruitment.staffing_prepared_at is None
    ):
        raise saturday_recruiting_store.LifecycleConflict(
            "Open the closed Saturday schedule before Auto scheduling."
        )
    commitments = {
        item.person_name: {
            "start": item.availability_start,
            "end": item.availability_end,
        }
        for item in bundle.commitments
        if item.status == "committed"
    }
    names = set(staffing.effective_saturday_commitments(
        commitments,
        schedule.saturday_availability_overrides,
        bundle.recruitment.shift_start,
        bundle.recruitment.shift_end,
    ))
    return [person for person in roster if person.name in names], names
```

Call it only for `d.weekday() == 5` from both the ordinary rebuild and
Reset-to-defaults paths. Catch `SaturdayRecruitingError` and return
`_error(str(exc), 409)` before invoking either scheduler. This keeps Reset from
loading weekday defaults for non-recruits.

- [ ] **Step 4: Preserve prepared defaults and place the remaining crew**

Build Saturday locks from current `manual` and `default` assignment sources:

```python
def _saturday_prepared_locks(schedule, available_names, enabled_centers):
    enabled = set(enabled_centers)
    return {
        center: [
            name for name in schedule.assignments.get(center, ())
            if name in available_names
            and schedule.assignment_sources.get(center, {}).get(name)
            in {"manual", "default"}
        ]
        for center in enabled
        if any(
            name in available_names
            and schedule.assignment_sources.get(center, {}).get(name)
            in {"manual", "default"}
            for name in schedule.assignments.get(center, ())
        )
    }
```

Use these locks for Saturday, retain the existing weekday `_protected_locks`
path, and call the ordinary solver with:

```python
minimum_only=(d.weekday() != 5)
```

This makes a manager-pressed Saturday Auto attempt to place every remaining
committed recruit, while weekday goal buttons continue staffing to minimum.
For Saturday Reset-to-defaults, pass the filtered committed roster to
`_saturday_defaults_only_schedule`; for weekday Reset, continue calling
`defaults_only_schedule` with the full active roster.

- [ ] **Step 5: Run rotation tests**

Run:

```bash
pytest -q tests/test_staffing_rotations.py
```

Expected: all pass.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/zira_dashboard/routes/rotations.py tests/test_staffing_rotations.py
git commit -m "fix: scope Saturday Auto to committed recruits"
git push origin main
```

---

### Task 6: Full regression verification

**Files:**
- Modify only if a failing regression demonstrates an in-scope defect.

**Interfaces:**
- Verifies all interfaces produced by Tasks 1–5.

- [ ] **Step 1: Run focused Saturday, Inbox, and scheduler suites**

Run:

```bash
pytest -q \
  tests/test_saturday_recruiting_schema.py \
  tests/test_saturday_recruiting_store.py \
  tests/test_saturday_recruiting_manager_routes.py \
  tests/test_saturday_recruiting_static.py \
  tests/test_staffing_saturday_recruiting.py \
  tests/test_staffing_rotations.py \
  tests/test_staffing_static.py \
  tests/test_exception_inbox.py \
  tests/test_exception_inbox_breakdown.py \
  tests/test_exception_inbox_breakdown_template.py
```

Expected: PASS, with only documented `DATABASE_URL` skips.

- [ ] **Step 2: Run the complete test suite**

Run:

```bash
pytest -q
```

Expected: PASS, with only environment-gated skips.

- [ ] **Step 3: Run static validation**

Run:

```bash
ruff check src tests
git diff --check
```

Expected: both commands exit 0 with no findings.

- [ ] **Step 4: Verify the final diff and working tree**

Run:

```bash
git status --short
git diff HEAD~5 --stat
git log -6 --oneline
```

Expected: only the user's pre-existing untracked `uv.lock` remains; the five
implementation commits and this plan are visible on `main`.

- [ ] **Step 5: Push the final verified state**

```bash
git push origin main
```

Expected: `origin/main` advances to the verified implementation commit.
