# Saturday Work Recruiting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let managers recruit a qualification-safe Saturday crew through the timeclock, collect firm full/partial commitments, populate Saturday Unassigned, and publish only after the response cutoff.

**Architecture:** A pure domain module owns deadline, availability, matching, and publish rules. A Postgres store owns lifecycle and atomic decisions; focused manager and employee routers adapt it to Staffing and Timeclock. Recruiting remains separate from the normal draft/published `Schedule`.

**Tech Stack:** Python 3.11, FastAPI, Jinja2/HTMX, vanilla JavaScript/CSS, Postgres/psycopg2, pytest.

## Global Constraints

- Complete `2026-07-16-timeclock-spanish-primary-language-mode.md` first.
- A full or partial commitment consumes exactly one opening.
- Eligibility requires level 2 or 3 in every required skill of at least one remaining requested work center.
- Partial hours stay inside the snapshotted Saturday shift and use 30-minute increments.
- The response and employee-cancellation deadline is the clock-in time of the nearest earlier configured plant workday.
- Recruiting and publication stay separate; publication is blocked before the deadline.
- Use `America/Chicago` for deadline calculations and display.
- Add no runtime dependency.
- Validate every mutation on the server and serialize final-slot decisions with a database row lock.

---

## File Structure

- Create `src/zira_dashboard/saturday_recruiting.py` for pure rules and types.
- Create `src/zira_dashboard/saturday_recruiting_store.py` for all recruiting persistence.
- Create `src/zira_dashboard/routes/saturday_recruiting.py` for manager APIs.
- Create `src/zira_dashboard/routes/timeclock_saturday.py` for employee routes.
- Create `src/zira_dashboard/saturday_work_reminder.py` for one-time punch-out reminders.
- Create focused manager/timeclock templates and `static/saturday-recruiting.{js,css}`.
- Modify `_schema.py`, `app.py`, Staffing, Timeclock, notifications, and i18n only at the integration points named in the tasks.

---

### Task 1: Build the pure deadline, availability, and matching domain

**Files:**
- Create: `src/zira_dashboard/saturday_recruiting.py`
- Create: `tests/test_saturday_recruiting.py`

**Interfaces:**
- Produces: `Opening`, `Commitment`, and `Coverage` frozen dataclasses.
- Produces: `response_deadline(day, work_weekdays, shift_start_for) -> datetime`.
- Produces: `format_deadline(value) -> str` for the one persisted deadline label used by every surface.
- Produces: `format_time_range(start, end) -> str` for full and partial commitment hours.
- Produces: `validate_availability(start, end, shift_start, shift_end) -> None`.
- Produces: `eligible_work_centers(skill_levels, openings) -> frozenset[int]`.
- Produces: `match_commitments(openings, commitments) -> Coverage | None`.

- [ ] **Step 1: Write failing deadline and partial-hour tests**

```python
from datetime import date, datetime, time
import pytest
from zira_dashboard import saturday_recruiting as sr
from zira_dashboard.shift_config import SITE_TZ


def test_deadline_is_previous_configured_workday_start():
    starts = {date(2026, 7, 24): time(7, 0)}
    assert sr.response_deadline(
        date(2026, 7, 25), frozenset({0, 1, 2, 3, 4}), starts.__getitem__
    ) == datetime(2026, 7, 24, 7, 0, tzinfo=SITE_TZ)


def test_deadline_label_is_consistent_and_explicit():
    value = datetime(2026, 7, 24, 7, 0, tzinfo=SITE_TZ)
    assert sr.format_deadline(value) == "Friday, July 24 at 7:00 AM"


def test_partial_hours_label_uses_half_hour_range():
    assert sr.format_time_range(time(7, 0), time(11, 30)) == "7:00 AM–11:30 AM"


def test_deadline_skips_nonworking_friday():
    starts = {date(2026, 7, 23): time(6, 30)}
    assert sr.response_deadline(
        date(2026, 7, 25), frozenset({0, 1, 2, 3}), starts.__getitem__
    ) == datetime(2026, 7, 23, 6, 30, tzinfo=SITE_TZ)


@pytest.mark.parametrize("start,end", [
    (time(5, 30), time(10, 0)), (time(6, 0), time(12, 30)),
    (time(8, 15), time(10, 0)), (time(10, 0), time(10, 0)),
])
def test_partial_rejects_invalid_boundaries(start, end):
    with pytest.raises(sr.InvalidAvailability):
        sr.validate_availability(start, end, time(6, 0), time(12, 0))


def test_partial_accepts_half_hour_boundaries():
    sr.validate_availability(time(7, 0), time(11, 30), time(6, 0), time(12, 0))
```

- [ ] **Step 2: Run the tests and verify the module is missing**

Run: `pytest tests/test_saturday_recruiting.py -v`

Expected: FAIL on import.

- [ ] **Step 3: Implement deadline and availability rules**

```python
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from collections.abc import Callable, Mapping, Sequence
from .shift_config import SITE_TZ


class SaturdayRecruitingError(ValueError):
    pass


class InvalidAvailability(SaturdayRecruitingError):
    pass


def response_deadline(day, work_weekdays, shift_start_for):
    if day.weekday() != 5:
        raise SaturdayRecruitingError("Saturday recruiting requires a Saturday")
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if cursor.weekday() in work_weekdays:
            return datetime.combine(cursor, shift_start_for(cursor), tzinfo=SITE_TZ)
        cursor -= timedelta(days=1)
    raise SaturdayRecruitingError("No prior configured plant workday")


def format_deadline(value: datetime) -> str:
    local = value.astimezone(SITE_TZ)
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%A, %B')} {local.day} at {clock}"


def format_time_range(start: time, end: time) -> str:
    def clock(value: time) -> str:
        return datetime.combine(date.min, value).strftime("%I:%M %p").lstrip("0")
    return f"{clock(start)}–{clock(end)}"


def validate_availability(start, end, shift_start, shift_end):
    on_half_hour = lambda value: (
        value.minute in (0, 30) and value.second == 0 and value.microsecond == 0
    )
    if not on_half_hour(start) or not on_half_hour(end):
        raise InvalidAvailability("Availability must use 30-minute increments")
    if start < shift_start or end > shift_end or start >= end:
        raise InvalidAvailability("Availability must stay within the Saturday shift")
```

- [ ] **Step 4: Add failing matching tests**

```python
def _opening(wc_id, count, *skills):
    return sr.Opening(wc_id, f"WC {wc_id}", count, tuple(skills))


def test_eligibility_requires_level_two_in_every_skill():
    openings = [_opening(10, 1, "Repair", "Forklift")]
    assert sr.eligible_work_centers({"Repair": 3, "Forklift": 2}, openings) == {10}
    assert sr.eligible_work_centers({"Repair": 3, "Forklift": 1}, openings) == set()
    assert sr.eligible_work_centers({"Repair": 3, "Forklift": 4}, openings) == set()


def test_matcher_rematches_multiskilled_person():
    openings = [_opening(10, 1, "Repair"), _opening(20, 1, "Dismantle")]
    result = sr.match_commitments(openings, [
        sr.Commitment(1, frozenset({10, 20})),
        sr.Commitment(2, frozenset({10})),
    ])
    assert result.wc_by_person == {1: 20, 2: 10}


def test_matcher_rejects_impossible_skill_mix():
    openings = [_opening(10, 1, "Repair"), _opening(20, 1, "Dismantle")]
    assert sr.match_commitments(openings, [
        sr.Commitment(1, frozenset({10})), sr.Commitment(2, frozenset({10})),
    ]) is None
```

- [ ] **Step 5: Implement deterministic bipartite matching**

```python
@dataclass(frozen=True)
class Opening:
    wc_id: int
    wc_name: str
    requested_count: int
    required_skills: tuple[str, ...]


@dataclass(frozen=True)
class Commitment:
    person_id: int
    eligible_wc_ids: frozenset[int]


@dataclass(frozen=True)
class Coverage:
    total: int
    filled_by_wc: dict[int, int]
    wc_by_person: dict[int, int]


def eligible_work_centers(skill_levels, openings):
    return frozenset(
        opening.wc_id for opening in openings
        if opening.required_skills
        and all(int(skill_levels.get(skill, 0)) in (2, 3) for skill in opening.required_skills)
    )


def match_commitments(openings, commitments):
    slots = [(o.wc_id, i) for o in sorted(openings, key=lambda x: x.wc_id)
             for i in range(o.requested_count)]
    by_person = {c.person_id: c for c in commitments}
    if len(by_person) > len(slots):
        return None
    person_for_slot = {}

    def assign(person_id, seen):
        for slot in slots:
            if slot[0] not in by_person[person_id].eligible_wc_ids or slot in seen:
                continue
            seen.add(slot)
            prior = person_for_slot.get(slot)
            if prior is None or assign(prior, seen):
                person_for_slot[slot] = person_id
                return True
        return False

    for person_id in sorted(by_person):
        if not assign(person_id, set()):
            return None
    wc_by_person = {pid: slot[0] for slot, pid in person_for_slot.items()}
    filled = {o.wc_id: 0 for o in openings}
    for wc_id in wc_by_person.values():
        filled[wc_id] += 1
    return Coverage(len(by_person), filled, wc_by_person)
```

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_saturday_recruiting.py -v`

Expected: PASS.

```bash
git add src/zira_dashboard/saturday_recruiting.py tests/test_saturday_recruiting.py
git commit -m "feat: add Saturday recruiting domain"
```

---

### Task 2: Add schema and recruitment lifecycle storage

**Files:**
- Modify: `src/zira_dashboard/_schema.py`
- Create: `src/zira_dashboard/saturday_recruiting_store.py`
- Create: `tests/test_saturday_recruiting_schema.py`
- Create: `tests/test_saturday_recruiting_store.py`

**Interfaces:**
- Produces: `AvailablePosition`, `Recruitment`, `StoredCommitment`, and `RecruitmentBundle` frozen dataclasses.
- Produces: `get(day: date) -> RecruitmentBundle | None`.
- Produces: `available_positions() -> tuple[AvailablePosition, ...]`.
- Produces: `activate(day: date, shift_start: time, shift_end: time, response_deadline: datetime, requested_counts: Mapping[int, int], actor: str | None, now: datetime) -> RecruitmentBundle`.
- Produces: `update_openings(day: date, requested_counts: Mapping[int, int], shift_start: time, shift_end: time, actor: str | None, now: datetime) -> RecruitmentBundle`.
- Produces: `close_due(now: datetime) -> int` and `mark_published(day: date, now: datetime) -> RecruitmentBundle`.
- Raises: `LifecycleConflict` for invalid status transitions and protected opening/hour changes.

- [ ] **Step 1: Write the failing Postgres schema test**

```python
import os
import pytest
from zira_dashboard import db

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")


def test_recruiting_tables_and_notification_key_exist():
    db.bootstrap_schema()
    rows = db.query(
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(%s)",
        (["saturday_recruitments", "saturday_recruitment_openings", "saturday_work_responses"],),
    )
    assert {r["table_name"] for r in rows} == {
        "saturday_recruitments", "saturday_recruitment_openings", "saturday_work_responses",
    }
```

- [ ] **Step 2: Add idempotent DDL**

```sql
CREATE TABLE IF NOT EXISTS saturday_recruitments (
  day DATE PRIMARY KEY CHECK (EXTRACT(ISODOW FROM day) = 6),
  status TEXT NOT NULL CHECK (status IN ('recruiting','closed','published','cancelled')),
  shift_start TIME NOT NULL,
  shift_end TIME NOT NULL,
  response_deadline TIMESTAMPTZ NOT NULL,
  activated_by TEXT,
  activated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ,
  published_at TIMESTAMPTZ,
  cancelled_by TEXT,
  cancelled_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (shift_end > shift_start)
);
CREATE TABLE IF NOT EXISTS saturday_recruitment_openings (
  day DATE NOT NULL REFERENCES saturday_recruitments(day) ON DELETE CASCADE,
  wc_id INTEGER NOT NULL REFERENCES work_centers(id) ON DELETE RESTRICT,
  requested_count INTEGER NOT NULL CHECK (requested_count > 0),
  PRIMARY KEY (day, wc_id)
);
CREATE TABLE IF NOT EXISTS saturday_work_responses (
  day DATE NOT NULL REFERENCES saturday_recruitments(day) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
  status TEXT NOT NULL CHECK (status IN ('later','declined','committed','cancelled')),
  availability_start TIME,
  availability_end TIME,
  eligible_wc_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
  responded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  committed_at TIMESTAMPTZ,
  cancelled_at TIMESTAMPTZ,
  cancelled_by TEXT,
  cancellation_reason TEXT,
  punch_reminder_shown_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, person_id),
  CHECK (
    (status IN ('later','declined') AND availability_start IS NULL AND availability_end IS NULL)
    OR
    (status IN ('committed','cancelled') AND availability_start IS NOT NULL
      AND availability_end IS NOT NULL AND availability_end > availability_start)
  )
);
ALTER TABLE employee_notifications ADD COLUMN IF NOT EXISTS saturday_day DATE;
CREATE UNIQUE INDEX IF NOT EXISTS employee_notifications_saturday_dedupe
  ON employee_notifications (person_odoo_id, saturday_day, kind)
  WHERE saturday_day IS NOT NULL;
```

- [ ] **Step 3: Add failing activation/read/closure tests**

```python
bundle = store.activate(
    day=date(2026, 7, 25), shift_start=time(6, 0), shift_end=time(12, 0),
    response_deadline=datetime(2026, 7, 24, 7, 0, tzinfo=SITE_TZ),
    requested_counts={repair_wc_id: 3, dismantle_wc_id: 2},
    actor="manager@gruberpallets.com", now=NOW,
)
assert bundle.recruitment.status == "recruiting"
assert {o.wc_id: o.requested_count for o in bundle.openings} == {
    repair_wc_id: 3, dismantle_wc_id: 2,
}
assert store.get(bundle.recruitment.day) == bundle
assert store.close_due(bundle.recruitment.response_deadline) == 1
```

Add these named tests with the stated results:

- `test_activate_rejects_non_saturday` → `SaturdayRecruitingError`.
- `test_activate_rejects_elapsed_deadline` → `LifecycleConflict`.
- `test_activate_rejects_empty_requested_counts` → `LifecycleConflict`.
- `test_activate_rejects_work_center_without_required_skills` → `LifecycleConflict`.
- `test_activate_rejects_existing_draft_assignments` → `LifecycleConflict` with “Clear existing Saturday assignments before activating recruiting.”
- `test_activate_rejects_already_published_schedule` → `LifecycleConflict`.
- `test_repeated_identical_activation_is_idempotent` → the original bundle and audit timestamp are unchanged.
- `test_reactivation_with_different_payload_is_rejected` → `LifecycleConflict`.
- `test_update_rejects_count_below_current_coverage` → `LifecycleConflict`.
- `test_update_rejects_shift_hour_change_after_first_commitment` → `LifecycleConflict`.
- `test_closed_recruitment_can_only_reduce_unfilled_count` → reduction succeeds;
  increasing or adding a work center raises `LifecycleConflict`.

- [ ] **Step 4: Implement typed row shaping and transactional lifecycle methods**

```python
@dataclass(frozen=True)
class AvailablePosition:
    wc_id: int
    wc_name: str
    required_skills: tuple[str, ...]


@dataclass(frozen=True)
class Recruitment:
    day: date
    status: str
    shift_start: time
    shift_end: time
    response_deadline: datetime


@dataclass(frozen=True)
class StoredCommitment:
    person_id: int
    person_odoo_id: int | None
    person_name: str
    status: str
    availability_start: time | None
    availability_end: time | None
    eligible_wc_ids: frozenset[int]


@dataclass(frozen=True)
class RecruitmentBundle:
    recruitment: Recruitment
    openings: tuple[sr.Opening, ...]
    commitments: tuple[StoredCommitment, ...]
```

`activate()` validates work centers and required skills before inserting. It
also rejects any existing `schedule_assignments` rows or an already published
schedule for the day, so a non-volunteer cannot silently enter the optional
crew. It upserts the recruitment and replaces opening rows in one `db.cursor()`
transaction. `_load_bundle(cur, day)` is the single row-shaping implementation
used by every public method. `available_positions()` returns local work-center
ids/names and nonempty required-skill tuples from one set-based query.
`update_openings()` uses `SELECT ... FOR UPDATE`: while Recruiting it permits
add/increase/reduction no lower than current coverage; while Closed it permits
only reductions of unfilled counts so managers can reconcile shortages.
It accepts replacement shift hours only while Recruiting has zero commitments;
otherwise changed hours raise `LifecycleConflict`. Every accepted update
revalidates 30-minute shift boundaries and ensures all existing commitments
still have a complete qualification matching under the proposed counts.
`close_due()` performs one set-based `UPDATE`.

- [ ] **Step 5: Run store/schema tests and commit**

Run: `pytest tests/test_saturday_recruiting.py tests/test_saturday_recruiting_store.py tests/test_saturday_recruiting_schema.py -v`

Expected: PASS; DB tests SKIP only when `DATABASE_URL` is absent.

```bash
git add src/zira_dashboard/_schema.py src/zira_dashboard/saturday_recruiting_store.py tests/test_saturday_recruiting_schema.py tests/test_saturday_recruiting_store.py
git commit -m "feat: persist Saturday recruiting lifecycle"
```

---

### Task 3: Make employee decisions atomic and concurrency-safe

**Files:**
- Modify: `src/zira_dashboard/saturday_recruiting_store.py`
- Modify: `tests/test_saturday_recruiting_store.py`

**Interfaces:**
- Produces: `Offer`, `HomeBanner`, `CommitmentStatus`, `DecisionResult`, `offer_for_person`, `home_banner`, `commitment_for_person`, `record_later`, `decline`, `commit`, `cancel_by_employee`, and `cancel_by_manager`.
- Produces: `offer_for_person(person_id: int, now: datetime) -> Offer | None`, `home_banner(now: datetime) -> HomeBanner | None`, and `commitment_for_person(person_id: int, now: datetime) -> CommitmentStatus | None`.
- Produces: `record_later(day: date, person_id: int, now: datetime) -> DecisionResult`, `decline(day: date, person_id: int, now: datetime) -> DecisionResult`, and `commit(day: date, person_id: int, start: time, end: time, now: datetime) -> DecisionResult`.
- Produces: `cancel_by_employee(day: date, person_id: int, now: datetime) -> DecisionResult` and `cancel_by_manager(day: date, person_id: int, actor: str | None, reason: str, now: datetime) -> DecisionResult`.
- Raises: `LifecycleConflict`, `RecruitingClosed`, `NoCompatibleOpening`, and `InvalidAvailability` with user-safe text.

- [ ] **Step 1: Add failing decision tests**

```python
first = store.commit(SATURDAY, multi_id, time(6, 0), time(12, 0), NOW)
second = store.commit(SATURDAY, repair_id, time(7, 0), time(11, 30), NOW)
assert first.status == second.status == "committed"
coverage = sr.match_commitments(second.bundle.openings, [
    sr.Commitment(c.person_id, c.eligible_wc_ids)
    for c in second.bundle.commitments if c.status == "committed"
])
assert coverage.wc_by_person == {multi_id: dismantle_wc_id, repair_id: repair_wc_id}
```

Add these named tests:

- `test_decline_suppresses_future_offer_for_same_saturday` → `offer_for_person()` returns `None`.
- `test_later_keeps_offer_and_reserves_no_capacity` → the response is `later`, committed count stays zero, and the next offer is present.
- `test_full_day_time_off_has_no_offer` and `test_salaried_person_has_no_offer` → `None`.
- `test_employee_cancel_before_cutoff_reopens_capacity` → status `cancelled` and banner remaining count increases by one.
- `test_employee_cancel_at_or_after_cutoff_is_rejected` → `RecruitingClosed`.
- `test_manager_cancel_after_cutoff_records_actor_and_reason` → `cancelled_by`, `cancellation_reason`, and `cancelled_at` are persisted.
- `test_repeated_identical_commit_is_idempotent` → one response row and the original `committed_at`.
- `test_repeated_employee_cancel_is_idempotent` → one cancelled row and the original `cancelled_at`.

- [ ] **Step 2: Implement direct qualification reads and offer shaping**

```python
@dataclass(frozen=True)
class Offer:
    day: date
    shift_start: time
    shift_end: time
    response_deadline: datetime
    eligible_wc_ids: frozenset[int]


@dataclass(frozen=True)
class HomeBanner:
    day: date
    response_deadline: datetime
    remaining_count: int


@dataclass(frozen=True)
class CommitmentStatus:
    day: date
    availability_start: time
    availability_end: time
    response_deadline: datetime
    can_employee_cancel: bool


@dataclass(frozen=True)
class DecisionResult:
    status: str
    bundle: RecruitmentBundle


def offer_for_person(person_id: int, now: datetime) -> Offer | None:
    # Load the nearest open recruitment, current active/hourly person,
    # response state, full-day time off, direct person_skills, and current
    # commitments. Return an Offer only if adding the candidate still matches.
```

Query `person_skills` directly inside the store rather than using the roster
cache. Existing commitments use their persisted `eligible_wc_ids`; the new
candidate uses current skills.

`home_banner(now)` returns the next effectively Recruiting Saturday only when
deterministic coverage has at least one unfilled slot. `commitment_for_person()`
returns a full/partial status model for committed employees even after cutoff.

- [ ] **Step 3: Implement the locked commit core**

```python
def commit(day, person_id, start, end, now):
    with db.cursor() as cur:
        recruitment = _lock_recruitment(cur, day)
        _require_open(recruitment, now)
        sr.validate_availability(start, end, recruitment.shift_start, recruitment.shift_end)
        bundle = _load_bundle(cur, day)
        eligible = _eligible_wc_ids_for_person(cur, person_id, bundle.openings)
        existing = [
            sr.Commitment(c.person_id, c.eligible_wc_ids)
            for c in bundle.commitments
            if c.status == "committed" and c.person_id != person_id
        ]
        if not eligible or sr.match_commitments(
            bundle.openings, [*existing, sr.Commitment(person_id, eligible)]
        ) is None:
            raise NoCompatibleOpening("That opening was just filled. You have not been scheduled.")
        _upsert_response(
            cur, day, person_id, "committed", start, end,
            eligible_wc_ids=eligible, responded_at=now, committed_at=now,
        )
        return DecisionResult("committed", _load_bundle(cur, day))
```

The other response operations use the same recruitment lock and one
`INSERT ... ON CONFLICT`. A cancelled response is never automatically offered
again. Manager cancellation bypasses only the employee deadline restriction.
Enforce this transition table under the lock:

| Current response | Allowed next response |
|---|---|
| none | later, declined, committed |
| later | later, declined, committed |
| committed | committed with identical hours; cancelled through a cancel method |
| declined | declined only |
| cancelled | cancelled only |

A stale Later or No post must never downgrade a commitment. Add
`test_stale_decline_cannot_replace_commitment` and
`test_stale_later_cannot_replace_commitment`; both raise `LifecycleConflict`
and leave the committed row unchanged.

- [ ] **Step 4: Add the concurrent final-slot test**

Use `ThreadPoolExecutor(max_workers=2)` plus `threading.Barrier(2)` to have two
qualified employees call `commit()` for one opening. Assert exactly one
success, one `NoCompatibleOpening`, and one committed DB row. Gate only this
integration test on `DATABASE_URL`.

- [ ] **Step 5: Run decision tests three times and commit**

Run the following command three times (do not add `pytest-repeat`):

`pytest tests/test_saturday_recruiting.py tests/test_saturday_recruiting_store.py -v`

Expected: PASS every time.

```bash
git add src/zira_dashboard/saturday_recruiting_store.py tests/test_saturday_recruiting_store.py
git commit -m "feat: reserve Saturday openings atomically"
```

---

### Task 4: Add the manager recruiting panel and lifecycle routes

**Files:**
- Modify: `src/zira_dashboard/saturday_recruiting_store.py`
- Create: `src/zira_dashboard/routes/saturday_recruiting.py`
- Create: `src/zira_dashboard/templates/_saturday_recruiting_panel.html`
- Create: `src/zira_dashboard/static/saturday-recruiting.js`
- Create: `src/zira_dashboard/static/saturday-recruiting.css`
- Modify: `src/zira_dashboard/routes/staffing.py`
- Modify: `src/zira_dashboard/staffing.py`
- Modify: `src/zira_dashboard/templates/staffing.html`
- Modify: `src/zira_dashboard/app.py`
- Create: `tests/test_saturday_recruiting_manager_routes.py`
- Create: `tests/test_saturday_recruiting_static.py`

**Interfaces:**
- Consumes: store APIs from Tasks 2–3.
- Produces: `/api/staffing/saturday-recruiting/activate`, `/openings`, `/commitments/{person_id}/cancel`, and `/cancel`.
- Produces: Staffing context keys `saturday_recruiting`, `saturday_positions`, `saturday_coverage`, `saturday_shift_start`, and `saturday_shift_end`.
- Produces: `serialize_bundle(bundle) -> dict` and `cancel_recruitment(day, actor, now) -> tuple[StoredCommitment, ...]` in the store.

- [ ] **Step 1: Add failing manager route tests**

```python
def test_activate_passes_snapshotted_values_and_actor(monkeypatch):
    captured = {}
    monkeypatch.setattr(routes.store, "activate", lambda **kw: captured.update(kw) or BUNDLE)
    response = client.post("/api/staffing/saturday-recruiting/activate", json={
        "day": "2026-07-25", "shift_start": "06:00", "shift_end": "12:00",
        "requested_counts": {"17": 3, "22": 2},
    })
    assert response.status_code == 200
    assert captured["day"] == date(2026, 7, 25)
    assert captured["actor"] is None


def test_non_saturday_activation_is_422():
    response = client.post("/api/staffing/saturday-recruiting/activate", json={
        "day": "2026-07-24", "shift_start": "06:00", "shift_end": "12:00",
        "requested_counts": {"17": 1},
    })
    assert response.status_code == 422
```

Add `test_openings_can_increase_while_recruiting`,
`test_filled_count_reduction_returns_409`,
`test_manager_commitment_cancel_requires_reason`, and
`test_full_cancel_unpublishes_and_clears_assignments_atomically`. In the last
test, begin with a published schedule and assert the recruitment is Cancelled,
`schedules.published` is false, and `schedule_assignments` has no rows for the
Saturday after the request commits.

- [ ] **Step 2: Implement validated manager endpoints**

```python
router = APIRouter(prefix="/api/staffing/saturday-recruiting")


def _actor(request):
    return getattr(request.state, "user_upn", None)


@router.post("/activate")
async def activate(request: Request):
    body = await request.json()
    day = date.fromisoformat(str(body["day"]))
    start = time.fromisoformat(str(body["shift_start"]))
    end = time.fromisoformat(str(body["shift_end"]))
    counts = {int(key): int(value) for key, value in body["requested_counts"].items()}
    deadline = sr.response_deadline(
        day, schedule_store.current().work_weekdays,
        shift_config.configured_shift_start_for,
    )
    bundle = store.activate(
        day=day, shift_start=start, shift_end=end,
        response_deadline=deadline, requested_counts=counts,
        actor=_actor(request), now=plant_now(),
    )
    staffing_routes._bust_after_mutation()
    return JSONResponse({"ok": True, "recruitment": store.serialize_bundle(bundle)})
```

Return 409 for lifecycle/capacity conflicts and 422 for malformed input. Log
unexpected exceptions and return a generic 500 message without SQL details.
`cancel_recruitment()` locks the recruitment row and returns the previously
committed people for notification. In that same `db.cursor()` transaction it:

```python
cur.execute(
    "UPDATE saturday_recruitments SET status = 'cancelled', "
    "cancelled_by = %s, cancelled_at = %s, updated_at = %s "
    "WHERE day = %s AND status <> 'cancelled'",
    (actor, now, now, day),
)
cur.execute(
    "UPDATE schedules SET published = FALSE, published_snapshot = NULL, "
    "assignment_sources = '{}'::jsonb, updated_at = %s WHERE day = %s",
    (now, day),
)
cur.execute("DELETE FROM schedule_assignments WHERE day = %s", (day,))
```

After the transaction commits, call the new public
`staffing.invalidate_schedule_cache(day)` wrapper and the normal Staffing page
cache invalidator. Preserve schedule notes, work-center notes, and custom hours;
only the operational publication and active assignments are cleared. Repeated
full cancellation returns the same committed-notification targets without
changing audit timestamps.

```python
# src/zira_dashboard/staffing.py
def invalidate_schedule_cache(day: date) -> None:
    _invalidate_schedule_cache(day)
```

Implement the JSON adapter once in the store so the manager routes and static
tests share an exact response shape:

```python
def serialize_bundle(bundle: RecruitmentBundle) -> dict:
    active = [c for c in bundle.commitments if c.status == "committed"]
    coverage = sr.match_commitments(
        bundle.openings,
        [sr.Commitment(c.person_id, c.eligible_wc_ids) for c in active],
    )
    filled = coverage.filled_by_wc if coverage else {}
    return {
        "recruitment": {
            "day": bundle.recruitment.day.isoformat(),
            "status": bundle.recruitment.status,
            "shift_start": bundle.recruitment.shift_start.isoformat(timespec="minutes"),
            "shift_end": bundle.recruitment.shift_end.isoformat(timespec="minutes"),
            "response_deadline": bundle.recruitment.response_deadline.isoformat(),
        },
        "coverage": {
            "total": len(active),
            "requested": sum(o.requested_count for o in bundle.openings),
            "openings": [
                {
                    "wc_id": o.wc_id,
                    "wc_name": o.wc_name,
                    "filled": filled.get(o.wc_id, 0),
                    "requested": o.requested_count,
                }
                for o in bundle.openings
            ],
        },
        "commitments": [
            {
                "person_id": c.person_id,
                "person_name": c.person_name,
                "availability_start": (
                    c.availability_start.isoformat(timespec="minutes")
                    if c.availability_start else None
                ),
                "availability_end": (
                    c.availability_end.isoformat(timespec="minutes")
                    if c.availability_end else None
                ),
            }
            for c in active
        ],
    }
```

- [ ] **Step 3: Add manager context on Saturday only**

```python
saturday_bundle = None
saturday_positions = []
saturday_coverage = None
saturday_deadline = None
if d.weekday() == 5:
    try:
        saturday_bundle = saturday_recruiting_store.get(d)
        saturday_positions = saturday_recruiting_store.available_positions()
        saturday_deadline = (
            saturday_bundle.recruitment.response_deadline
            if saturday_bundle else sr.response_deadline(
                d,
                schedule_store.current().work_weekdays,
                shift_config.configured_shift_start_for,
            )
        )
        saturday_payload = (
            saturday_recruiting_store.serialize_bundle(saturday_bundle)
            if saturday_bundle else None
        )
        saturday_coverage = saturday_payload["coverage"] if saturday_payload else None
    except Exception:
        log.exception("Saturday recruiting context failed for %s", d)

saturday_context = {
    "day_is_saturday": d.weekday() == 5,
    "saturday_recruiting": (
        saturday_bundle.recruitment if saturday_bundle else None
    ),
    "saturday_positions": saturday_positions,
    "saturday_coverage": saturday_coverage,
    "saturday_shift_start": (
        saturday_bundle.recruitment.shift_start.strftime("%H:%M")
        if saturday_bundle else shift_config.configured_shift_start_for(d).strftime("%H:%M")
    ),
    "saturday_shift_end": (
        saturday_bundle.recruitment.shift_end.strftime("%H:%M")
        if saturday_bundle else shift_config.configured_shift_end_for(d).strftime("%H:%M")
    ),
    "saturday_deadline_label": (
        sr.format_deadline(saturday_deadline) if saturday_deadline else ""
    ),
}
```

Merge `**saturday_context` into the existing `staffing.html`
`TemplateResponse` dictionary and pass the selected `saturday_commitments` into
`build_staffing_bays()` as specified in Task 5.

- [ ] **Step 4: Build the panel and assets**

```jinja2
{% if day_is_saturday %}
<section id="saturday-recruiting" data-day="{{ day }}"
         data-status="{{ saturday_recruiting.status if saturday_recruiting else 'closed' }}">
  <div class="saturday-recruiting__heading">
    <div><span class="eyebrow">Saturday Work</span>
      <h2>{% if saturday_recruiting and saturday_recruiting.status == 'cancelled' %}Plant closed — Saturday cancelled{% elif saturday_recruiting %}{{ saturday_coverage.total }} of {{ saturday_coverage.requested }} openings filled{% else %}Plant closed{% endif %}</h2>
    </div>
    {% if saturday_recruiting %}<span class="status-pill">{{ saturday_recruiting.status|upper }}</span>{% endif %}
  </div>
  <template id="saturday-opening-draft-template">
    <div class="saturday-opening-draft">
      <select data-opening-wc aria-label="Requested position">
        {% for position in saturday_positions %}
          <option value="{{ position.wc_id }}">{{ position.wc_name }}</option>
        {% endfor %}
      </select>
      <input data-opening-count type="number" min="1" step="1" value="1"
             aria-label="People needed">
    </div>
  </template>
  <div id="saturday-opening-rows">
    {% if saturday_recruiting %}
      {% for opening in saturday_coverage.openings %}
        <div class="saturday-opening-row" data-wc-id="{{ opening.wc_id }}">
          <span>{{ opening.wc_name }}</span>
          <strong>{{ opening.filled }} / {{ opening.requested }}</strong>
          <button type="button" data-opening-action="decrease"
                  {% if saturday_recruiting.status not in ('recruiting', 'closed') or opening.filled >= opening.requested %}disabled{% endif %}
                  aria-label="Decrease {{ opening.wc_name }}">−</button>
          <button type="button" data-opening-action="increase"
                  {% if saturday_recruiting.status != 'recruiting' %}disabled{% endif %}
                  aria-label="Increase {{ opening.wc_name }}">+</button>
        </div>
      {% endfor %}
    {% else %}
      {% if saturday_positions %}
        <div class="saturday-opening-draft">
          <select data-opening-wc aria-label="Requested position">
            {% for position in saturday_positions %}
              <option value="{{ position.wc_id }}">{{ position.wc_name }}</option>
            {% endfor %}
          </select>
          <input data-opening-count type="number" min="1" step="1" value="1"
                 aria-label="People needed">
        </div>
      {% else %}
        <p class="saturday-config-error">No work centers have required skills configured.
          <a href="/settings">Configure required skills in Settings.</a>
        </p>
      {% endif %}
    {% endif %}
  </div>
  <p>Saturday shift:
    <input data-shift-start type="time" step="1800" value="{{ saturday_shift_start }}"
           {% if saturday_recruiting and (saturday_recruiting.status != 'recruiting' or saturday_coverage.total > 0) %}disabled{% endif %}>
    –
    <input data-shift-end type="time" step="1800" value="{{ saturday_shift_end }}"
           {% if saturday_recruiting and (saturday_recruiting.status != 'recruiting' or saturday_coverage.total > 0) %}disabled{% endif %}>
  </p>
  <p>Employee response and cancellation deadline: <strong>{{ saturday_deadline_label }}</strong></p>
  <p id="saturday-recruiting-error" role="alert" hidden></p>
  <div class="saturday-actions">
    {% if not saturday_recruiting %}
      <button type="button" data-saturday-action="add-draft-opening">Add position</button>
      <button type="button" data-saturday-action="activate" {% if not saturday_positions %}disabled{% endif %}>Activate recruiting</button>
    {% elif saturday_recruiting.status == 'recruiting' %}
      <button type="button" data-saturday-action="add-opening">Add position</button>
      <button type="button" class="danger" data-saturday-action="cancel">Cancel Saturday</button>
    {% elif saturday_recruiting.status in ('closed', 'published') %}
      <button type="button" class="danger" data-saturday-action="cancel">Cancel Saturday</button>
    {% endif %}
  </div>
</section>
{% endif %}
```

The JS disables mutation buttons until the fetch resolves, shows returned
errors inline, and reloads the same date on success. Full cancellation confirms
with committed names and warns that management must directly contact anyone
who may not tap the timeclock again.

- [ ] **Step 5: Register the router, run tests, and commit**

Add the router import/include in `app.py` beside Staffing.

Run: `pytest tests/test_saturday_recruiting_manager_routes.py tests/test_saturday_recruiting_static.py tests/test_staffing_static.py -v`

Expected: PASS.

```bash
git add src/zira_dashboard/routes/saturday_recruiting.py src/zira_dashboard/templates/_saturday_recruiting_panel.html src/zira_dashboard/static/saturday-recruiting.js src/zira_dashboard/static/saturday-recruiting.css src/zira_dashboard/routes/staffing.py src/zira_dashboard/staffing.py src/zira_dashboard/templates/staffing.html src/zira_dashboard/app.py tests/test_saturday_recruiting_manager_routes.py tests/test_saturday_recruiting_static.py tests/test_staffing_static.py
git commit -m "feat: add Saturday recruiting manager panel"
```

---

### Task 5: Derive Saturday Off/Unassigned and validate publication

**Files:**
- Modify: `src/zira_dashboard/saturday_recruiting.py`
- Modify: `src/zira_dashboard/saturday_recruiting_store.py`
- Modify: `src/zira_dashboard/staffing_view.py`
- Modify: `src/zira_dashboard/routes/staffing.py`
- Modify: `src/zira_dashboard/templates/staffing.html`
- Modify: `src/zira_dashboard/static/staffing.js`
- Create: `tests/test_staffing_saturday_recruiting.py`
- Modify: `tests/test_staffing_view.py`
- Modify: `tests/test_staffing_schedule_metadata.py`

**Interfaces:**
- Changes: `build_staffing_bays(..., saturday_commitments: Mapping[str, dict] | None = None)`.
- Produces: `off`, `saturday_availability_by_name`, and `is_saturday_recruiting`.
- Produces: `validate_publish(bundle, assignments, people_by_name, full_day_off_names) -> list[str]`.
- Produces: Staffing context keys `saturday_publish_locked` and `saturday_publish_lock_message`.

- [ ] **Step 1: Add failing Saturday roster tests**

```python
def test_only_commitments_enter_saturday_unassigned(patch_wcs):
    model = staffing_view.build_staffing_bays(
        roster=[_person("Ana", Repair=3), _person("Bob", Repair=2), _person("Cara", Repair=3)],
        sched=_sched({}), time_off_entries=[], publish_blocked=0,
        saturday_commitments={
            "Ana": {"start": time(6, 0), "end": time(12, 0)},
            "Bob": {"start": time(7, 0), "end": time(11, 30)},
        },
    )
    assert model["unassigned"] == ["Ana", "Bob"]
    assert model["off"] == ["Cara"]
    assert model["saturday_availability_by_name"]["Bob"] == "7:00 AM–11:30 AM"


def test_closed_plant_saturday_puts_every_active_person_off(patch_wcs):
    model = staffing_view.build_staffing_bays(
        roster=[_person("Ana", Repair=3), _person("Bob", Repair=2)],
        sched=_sched({"Repair 1": ["Ana"]}), time_off_entries=[], publish_blocked=0,
        saturday_commitments={},
    )
    assert model["unassigned"] == []
    assert model["off"] == ["Ana", "Bob"]
    assert all(
        a["name"] != "Ana"
        for bay in model["bays"]
        for row in bay["rows"]
        for a in row["assigned"]
    )


def test_full_day_time_off_wins_over_commitment(patch_wcs):
    model = staffing_view.build_staffing_bays(
        roster=[_person("Ana", Repair=3)], sched=_sched({}),
        time_off_entries=[{"name": "Ana", "hours": None}], publish_blocked=0,
        saturday_commitments={"Ana": {"start": time(6, 0), "end": time(12, 0)}},
    )
    assert model["unassigned"] == []
    assert model["off"] == []
    assert model["time_off_names"] == ["Ana"]
```

- [ ] **Step 2: Implement the optional roster filter and UI**

```python
if saturday_commitments is None:
    off = []
else:
    committed = set(saturday_commitments)
    assignments = {
        wc_name: [name for name in names if name in committed]
        for wc_name, names in (sched.assignments or {}).items()
    }
    assigned_today = {name for names in assignments.values() for name in names}
    unassigned = [p.name for p in active_people if not p.reserve
                  and p.name in committed and p.name not in assigned_today
                  and p.name not in time_off_set]
    off = [p.name for p in active_people if not p.reserve
           and p.name not in committed and p.name not in assigned_today
           and p.name not in time_off_set]
```

Use this filtered `assignments` mapping throughout bay construction whenever
`saturday_commitments` is not `None`; do not read `sched.assignments` again in
that branch. This keeps old draft placements from showing a non-volunteer as
scheduled. The activation conflict tells the manager to use the existing
**Clear schedule** control before activating; it never deletes draft work
implicitly.

Filter each work-center picker to committed people plus already-assigned safety
net names. Render an Off section only for recruiting Saturdays. Add a distinct,
non-clearable availability badge beside each committed person in both
Unassigned and assigned work-center rows; do not reuse partial-time-off clear
controls. Assert a partial commitment keeps `7:00 AM–11:30 AM` after moving
from Unassigned into Repair.
In the Staffing route, pass `saturday_commitments={}` for an unpublished
Saturday with no recruitment or a Cancelled recruitment, and pass committed
rows only for Recruiting, Closed, or Published. Continue passing `None` on
normal weekdays so their roster derivation is unchanged.

- [ ] **Step 3: Add failing publish tests**

```python
def test_publish_before_deadline_is_blocked(monkeypatch):
    monkeypatch.setattr(staffing_routes.saturday_recruiting_store, "get", lambda day: OPEN_BUNDLE)
    response = staffing_routes._staffing_save_work(
        SimpleNamespace(headers={}), SATURDAY, 0,
        FormData([("action", "publish"), ("loc__Repair 1", "Ana")]),
    )
    assert "publish_blocked=1" in response.headers["location"]


def test_publish_requires_commitments_and_requested_coverage():
    reasons = sr.validate_publish(BUNDLE, {"Repair 1": ["Ana"]}, PEOPLE, set())
    assert "Bob committed to Saturday but is not assigned." in reasons
    assert "Dismantle requires 1 qualified operator — currently 0." in reasons
```

Add `validate_publish` tests for each exact blocker:

- a duplicate name → `"Ana is assigned more than once."`;
- an assigned noncommitted name → `"Cara is not committed to Saturday."`;
- a post-commitment skill drop → `"Ana is no longer qualified for Repair."`;
- an inactive committed employee → `"Ana is inactive."`;
- committed full-day time off → `"Ana has approved full-day time off."`.

- [ ] **Step 4: Implement and merge Saturday publish reasons**

```python
if action == "publish" and saturday_bundle:
    if plant_now() < saturday_bundle.recruitment.response_deadline:
        saturday_block = [
            "Saturday recruiting stays open until "
            f"{sr.format_deadline(saturday_bundle.recruitment.response_deadline)}."
        ]
    else:
        saturday_block = sr.validate_publish(
            saturday_bundle, assignments, people_by_name, full_day_off_names,
        )
    publish_block = [*saturday_block, *_publish_shortages(assignments)]
```

After successful schedule save, call `store.mark_published(day, plant_now())`.
On reads, display Published whenever `staffing.load_schedule(day).published` is
already true, even if that marker update previously failed.

Set `saturday_publish_locked` only when a noncancelled recruitment exists and
`plant_now()` is before its persisted deadline. Replace the existing publish
button with:

```jinja2
{% if saturday_publish_locked %}
  <span id="saturday-publish-lock" class="save-block">
    {{ saturday_publish_lock_message }}
  </span>
{% endif %}
<button type="submit" name="action" value="publish"
        class="publish-btn publish-submit" aria-busy="false"
        {% if saturday_publish_locked %}disabled aria-describedby="saturday-publish-lock"{% endif %}>
  {{ 'Re-publish' if published else 'Publish' }}
</button>
```

The lock message is exactly `Saturday recruiting stays open until
{persisted deadline}.` The server-side check above remains authoritative for
stale pages and direct requests.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_staffing_view.py tests/test_staffing_saturday_recruiting.py tests/test_staffing_schedule_metadata.py -v`

Expected: PASS.

```bash
git add src/zira_dashboard/saturday_recruiting.py src/zira_dashboard/saturday_recruiting_store.py src/zira_dashboard/staffing_view.py src/zira_dashboard/routes/staffing.py src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js tests/test_staffing_saturday_recruiting.py tests/test_staffing_view.py tests/test_staffing_schedule_metadata.py
git commit -m "feat: populate Saturday crew from commitments"
```

---

### Task 6: Add the employee banner, decision flow, and status card

**Files:**
- Create: `src/zira_dashboard/routes/timeclock_saturday.py`
- Create: `src/zira_dashboard/templates/timeclock_saturday_offer.html`
- Create: `src/zira_dashboard/templates/timeclock_saturday_partial.html`
- Create: `src/zira_dashboard/templates/timeclock_saturday_confirm.html`
- Create: `src/zira_dashboard/templates/_timeclock_saturday_status.html`
- Modify: `src/zira_dashboard/routes/timeclock.py`
- Modify: `src/zira_dashboard/templates/timeclock_home.html`
- Modify: `src/zira_dashboard/templates/timeclock_dashboard.html`
- Modify: `src/zira_dashboard/templates/timeclock_base.html`
- Modify: `src/zira_dashboard/timeclock_i18n.py`
- Modify: `src/zira_dashboard/app.py`
- Create: `tests/test_timeclock_saturday_recruiting.py`

**Interfaces:**
- Produces: token-gated offer, partial, confirm, commit, decline, later, and cancel routes.
- Produces: `saturday_banner` on shared home and `saturday_commitment` on dashboard.

- [ ] **Step 1: Add failing banner and route-priority tests**

```python
def test_home_shows_bilingual_banner_with_deadline(monkeypatch):
    monkeypatch.setattr(store, "home_banner", lambda now: BANNER)
    response = client.get("/timeclock")
    assert "Saturday Work Available" in response.text
    assert "Trabajo disponible el sábado" in response.text
    assert "Friday, July 24 at 7:00 AM" in response.text


def test_name_tap_routes_eligible_employee_to_offer(monkeypatch):
    monkeypatch.setattr(employee_notifications, "has_unacknowledged", lambda oid: False)
    monkeypatch.setattr(store, "offer_for_person", lambda person_id, now: OFFER)
    response = client.get("/timeclock/start/1", follow_redirects=False)
    assert "/timeclock/saturday/" in response.headers["location"]


def test_notifications_keep_priority(monkeypatch):
    monkeypatch.setattr(employee_notifications, "has_unacknowledged", lambda oid: True)
    response = client.get("/timeclock/start/1", follow_redirects=False)
    assert "/timeclock/notifications/" in response.headers["location"]
```

Add `test_name_tap_without_offer_continues_to_dashboard`,
`test_salaried_name_tap_never_checks_saturday_offer`,
`test_offer_lookup_failure_logs_and_continues_to_dashboard`, and
`test_home_hides_banner_at_full_capacity`. Assert the first three return the
existing dashboard redirect and the last response omits both banner languages.

- [ ] **Step 2: Add failing full/partial/decline/later/cancel route tests**

```python
partial = client.post(
    f"/timeclock/saturday/partial/{token}",
    data={"availability_start": "07:00", "availability_end": "11:30"},
)
assert "Confirm your commitment" in partial.text
assert "7:00 AM–11:30 AM" in partial.text

committed = client.post(
    f"/timeclock/saturday/commit/{fresh_token}",
    data={"day": "2026-07-25", "availability_start": "07:00", "availability_end": "11:30"},
    follow_redirects=False,
)
assert "/timeclock/dashboard/" in committed.headers["location"]
```

Add these named route tests:

- `test_yes_opens_confirmation_before_commit` → no store `commit()` call and the
  response includes date, full hours, deadline, and “firm commitment”.
- `test_partial_options_use_thirty_minute_steps` → includes `07:00`, `07:30`,
  and `11:30`, never `07:15`.
- `test_tampered_partial_minutes_return_422` → posting `07:15` returns the same
  partial screen with the 30-minute error.
- `test_stale_commit_returns_409_without_scheduling` → “That opening was just
  filled. You have not been scheduled.”
- `test_decline_suppresses_and_returns_to_punch_flow` → store status `declined`.
- `test_later_reserves_nothing_and_returns_to_punch_flow` → store status `later`.
- `test_cancel_before_cutoff_returns_person_to_dashboard` → store status `cancelled`.
- `test_cancel_after_cutoff_shows_contact_manager` → no mutation and “Contact a
  manager to make a change.”

- [ ] **Step 3: Implement priority and token-gated routes**

```python
if p.get("wage_type") != "monthly":
    try:
        offer = saturday_recruiting_store.offer_for_person(person_id, plant_now())
    except Exception:
        _log.exception("Saturday offer lookup failed for person %s", person_id)
        offer = None
    if offer is not None:
        return RedirectResponse(
            url=f"/timeclock/saturday/{_mint_token(person_id)}", status_code=303
        )
```

The new router imports token/person helpers from `routes.timeclock`, as
`timeclock_time_off` does. Every screen mints a fresh token and adds
`**timeclock_i18n.context_for_person(person)`.

Shape the raw store offer once for every offer/partial/confirmation template:

```python
def _offer_context(offer: store.Offer) -> dict:
    return {
        "day": offer.day.isoformat(),
        "day_label": f"{offer.day.strftime('%A, %B')} {offer.day.day}",
        "shift_start": offer.shift_start.isoformat(timespec="minutes"),
        "shift_end": offer.shift_end.isoformat(timespec="minutes"),
        "shift_label": sr.format_time_range(offer.shift_start, offer.shift_end),
        "deadline_label": sr.format_deadline(offer.response_deadline),
    }
```

Pass this dictionary as `offer`; never recalculate shift hours or deadline from
current Settings after activation.

- [ ] **Step 4: Implement the exact offer and confirmation hierarchy**

```jinja2
<h1>{{ t("Can you work Saturday, {date}?", date=offer.day_label) }}</h1>
<p>{{ offer.shift_label }}</p>
<div class="k-warning">
  {{ t("Respond by {deadline}.", deadline=offer.deadline_label) }}<br>
  {{ t("Openings may fill before the deadline.") }}
</div>
<div class="saturday-choice-grid">
  <form method="post" action="/timeclock/saturday/confirm/{{ token }}">
    <input type="hidden" name="day" value="{{ offer.day }}">
    <input type="hidden" name="availability_start" value="{{ offer.shift_start }}">
    <input type="hidden" name="availability_end" value="{{ offer.shift_end }}">
    <button class="k-btn" type="submit">{{ t("Yes") }}</button>
  </form>
  <form method="post" action="/timeclock/saturday/decline/{{ token }}">
    <input type="hidden" name="day" value="{{ offer.day }}">
    <button class="k-btn secondary" type="submit">{{ t("No") }}</button>
  </form>
  <form method="post" action="/timeclock/saturday/later/{{ token }}">
    <input type="hidden" name="day" value="{{ offer.day }}">
    <button class="k-btn secondary" type="submit">{{ t("Decide later") }}</button>
  </form>
</div>
<a class="saturday-partial-link" href="/timeclock/saturday/partial/{{ token }}">
  {{ t("I can work only part of the shift") }}
</a>
```

Use the same confirmation template for full and partial choices:

```jinja2
<h1>{{ t("Confirm your commitment") }}</h1>
<h2>{{ offer.day_label }} · {{ selected_hours }}</h2>
<p>{{ t("By confirming, you commit to work Saturday from {hours}.", hours=selected_hours) }}</p>
<p>{{ t("You may cancel until {deadline}.", deadline=offer.deadline_label) }}</p>
<p>{{ t("After that, contact a manager.") }}</p>
<form method="post" action="/timeclock/saturday/commit/{{ token }}">
  <input type="hidden" name="day" value="{{ offer.day }}">
  <input type="hidden" name="availability_start" value="{{ availability_start }}">
  <input type="hidden" name="availability_end" value="{{ availability_end }}">
  <button class="k-btn success" type="submit">{{ t("Confirm your commitment") }}</button>
</form>
```

Generate partial `<select>` options server-side at 30-minute steps; do not
accept arbitrary free-text times.

On the unidentified shared home, change only that page's header to a three-cell
grid so the banner is geometrically centered between the prompt and brand.
Render it from persisted values only:

```jinja2
<div class="k-header k-header-home">
  <span class="k-header-prompt">Tap your name to clock in or out
    <span class="k-es">Toca tu nombre para marcar entrada o salida</span>
  </span>
  {% if saturday_banner %}
    <div class="saturday-home-banner" role="status">
      <strong>Saturday Work Available</strong>
      <span class="k-es">Trabajo disponible el sábado</span>
      <small>Respond by {{ saturday_banner.deadline_label }} · Openings may fill</small>
    </div>
  {% else %}
    <span></span>
  {% endif %}
  <div class="k-header-actions">
    <a href="/timeclock/whos-out" class="k-icon-btn" aria-label="Who's Out" title="Who's Out">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
        <line x1="16" y1="2" x2="16" y2="6"></line>
        <line x1="8" y1="2" x2="8" y2="6"></line>
        <line x1="3" y1="10" x2="21" y2="10"></line>
      </svg>
    </a>
    {% include "_timeclock_brand.html" %}
  </div>
</div>
```

Add these rules to `timeclock_base.html`:

```css
.k-header-home { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); }
.k-header-actions { justify-self:end; display:flex; align-items:center; gap:1rem; }
.saturday-home-banner { justify-self:center; display:flex; flex-direction:column; align-items:center; text-align:center; color:#92400e; }
.saturday-home-banner strong { font-size:1.05rem; }
.saturday-home-banner small { font-size:.72rem; font-weight:600; margin-top:.2rem; }
.saturday-choice-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1rem; }
.saturday-choice-grid .k-btn { width:100%; min-width:0; }
.saturday-partial-link { display:block; margin:1rem auto 0; width:max-content; font-size:.85rem; color:#64748b; }
```

In `test_timeclock_saturday_recruiting.py`, assert the banner occurs between
`k-header-prompt` and `k-header-actions` in the rendered HTML, in addition to
the visibility tests from Step 1.

- [ ] **Step 5: Add banner/status context and translations**

Import `plant_now` with `from ..plant_day import now as plant_now` in the new
route and the existing Timeclock route. Load banner/commitment context
best-effort and never block punching:

```python
def _saturday_banner_context() -> dict | None:
    try:
        banner = saturday_recruiting_store.home_banner(plant_now())
    except Exception:
        _log.exception("Saturday home banner lookup failed")
        return None
    if banner is None:
        return None
    return {
        "day": banner.day.isoformat(),
        "deadline_label": sr.format_deadline(banner.response_deadline),
        "remaining_count": banner.remaining_count,
    }


def _saturday_commitment_context(person_id: int) -> dict | None:
    try:
        status = saturday_recruiting_store.commitment_for_person(
            person_id, plant_now()
        )
    except Exception:
        _log.exception("Saturday commitment lookup failed for person %s", person_id)
        return None
    if status is None:
        return None
    return {
        "day": status.day.isoformat(),
        "day_label": f"{status.day.strftime('%A, %B')} {status.day.day}",
        "hours": sr.format_time_range(
            status.availability_start, status.availability_end
        ),
        "deadline_label": sr.format_deadline(status.response_deadline),
        "can_employee_cancel": status.can_employee_cancel,
    }
```

Pass `_saturday_banner_context()` as `saturday_banner` from `timeclock_home()`
and `_saturday_commitment_context(person_id)` as `saturday_commitment` from
`timeclock_dashboard()`. The status partial renders the Saturday date, exact
hours, and exact cancellation deadline; it shows **Cancel Saturday commitment**
only when `can_employee_cancel`, otherwise **Contact a manager to make a
change**.

Add exact English/Spanish glossary keys:

```python
"Saturday Work Available": "Trabajo disponible el sábado",
"Can you work Saturday, {date}?": "¿Puedes trabajar el sábado {date}?",
"Respond by {deadline}.": "Responde antes de {deadline}.",
"Openings may fill before the deadline.": "Los lugares pueden llenarse antes de la fecha límite.",
"Yes": "Sí",
"No": "No",
"Decide later": "Decidir después",
"I can work only part of the shift": "Solo puedo trabajar parte del turno",
"Confirm your commitment": "Confirma tu compromiso",
"By confirming, you commit to work Saturday from {hours}.": "Al confirmar, te comprometes a trabajar el sábado de {hours}.",
"You may cancel until {deadline}.": "Puedes cancelar hasta {deadline}.",
"After that, contact a manager.": "Después de esa hora, habla con un gerente.",
"Your Saturday commitment": "Tu compromiso del sábado",
"Cancel Saturday commitment": "Cancelar compromiso del sábado",
"Contact a manager to make a change.": "Habla con un gerente para hacer un cambio.",
```

- [ ] **Step 6: Register, test, and commit**

Register `timeclock_saturday.router` beside the other timeclock routers.

Run: `pytest tests/test_timeclock_saturday_recruiting.py tests/test_timeclock_dashboard_static.py tests/test_timeclock_notifications_routes.py tests/test_timeclock_time_off_only.py -v`

Expected: PASS.

```bash
git add src/zira_dashboard/routes/timeclock_saturday.py src/zira_dashboard/templates/timeclock_saturday_offer.html src/zira_dashboard/templates/timeclock_saturday_partial.html src/zira_dashboard/templates/timeclock_saturday_confirm.html src/zira_dashboard/templates/_timeclock_saturday_status.html src/zira_dashboard/routes/timeclock.py src/zira_dashboard/templates/timeclock_home.html src/zira_dashboard/templates/timeclock_dashboard.html src/zira_dashboard/templates/timeclock_base.html src/zira_dashboard/timeclock_i18n.py src/zira_dashboard/app.py tests/test_timeclock_saturday_recruiting.py
git commit -m "feat: collect Saturday commitments in timeclock"
```

---

### Task 7: Add cancellation notifications and one-time punch-out reminders

**Files:**
- Modify: `src/zira_dashboard/employee_notifications.py`
- Modify: `src/zira_dashboard/templates/timeclock_notifications.html`
- Create: `src/zira_dashboard/saturday_work_reminder.py`
- Modify: `src/zira_dashboard/routes/timeclock.py`
- Modify: `src/zira_dashboard/templates/timeclock_success.html`
- Modify: `src/zira_dashboard/timeclock_i18n.py`
- Modify: `tests/test_employee_notifications.py`
- Modify: `tests/test_timeclock_notifications_routes.py`
- Create: `tests/test_saturday_work_reminder.py`

**Interfaces:**
- Produces: `create_saturday_cancelled(person_odoo_id, day)`.
- Produces: `claim_for_person(person_id, today, now) -> dict | None`.

- [ ] **Step 1: Add failing notification tests**

```python
def test_create_saturday_cancelled_is_idempotent(fake_db):
    en.create_saturday_cancelled(5, date(2026, 7, 25))
    sql, params = fake_db["executes"][0]
    assert "saturday_day" in sql
    assert "ON CONFLICT" in sql
    assert params[:3] == (5, "saturday_work_cancelled", date(2026, 7, 25))
```

Extend `list_unacknowledged()` to select `saturday_day`. Add route rendering
assertions for “Saturday work cancelled” and “Do not report to work,” including
Spanish-first level 3.

- [ ] **Step 2: Implement idempotent cancellation notification**

```python
def create_saturday_cancelled(person_odoo_id, day):
    db.execute(
        "INSERT INTO employee_notifications "
        "(person_odoo_id,kind,saturday_day,title,body) VALUES (%s,%s,%s,%s,%s) "
        "ON CONFLICT (person_odoo_id,saturday_day,kind) "
        "WHERE saturday_day IS NOT NULL DO NOTHING",
        (person_odoo_id, "saturday_work_cancelled", day,
         "Saturday work cancelled", "Saturday work was cancelled. Do not report to work."),
    )
```

The full-cancel manager route collects committed Odoo ids, commits cancellation,
then best-effort creates notifications. Return any notification failures as a
manager warning with names requiring direct contact.

- [ ] **Step 3: Add failing reminder claim tests**

```python
def test_claim_returns_partial_hours_and_marks_once(fake_cursor):
    fake_cursor.rows = [{
        "day": date(2026, 7, 25), "availability_start": time(7, 0),
        "availability_end": time(11, 30), "wc_name": None,
    }]
    card = reminder.claim_for_person(12, date(2026, 7, 24), NOW)
    assert card["day_label"] == "Saturday, July 25"
    assert card["hours"] == "7:00 AM–11:30 AM"
    assert card["work_center"] is None
    assert "punch_reminder_shown_at" in fake_cursor.executed_update
```

Add `test_cancelled_commitment_returns_none`, `test_already_shown_returns_none`,
`test_day_before_deadline_returns_none`, and
`test_published_assignment_returns_work_center_name`. The first three return
`None`; the last returns the published work-center name and still marks the
reminder timestamp once.

- [ ] **Step 4: Implement atomic claim and clock-out integration**

`claim_for_person()` uses `SELECT ... FOR UPDATE`, requires a committed response,
noncancelled recruitment, null reminder timestamp, and `today` equal to the
persisted deadline date. It marks the timestamp before returning.

```python
"time_off_reminder": time_off_reminder_card,
"saturday_work_reminder": saturday_reminder_card,
```

Render both cards and suppress the three-second redirect when either exists.
Transfers and automatic lunch remain untouched.

- [ ] **Step 5: Add translations, test, and commit**

Add these exact glossary entries:

```python
"Saturday work cancelled": "Trabajo del sábado cancelado",
"Saturday work was cancelled. Do not report to work.": "El trabajo del sábado fue cancelado. No te presentes a trabajar.",
"Saturday work reminder": "Recordatorio de trabajo del sábado",
"You are scheduled for {day}.": "Estás programado para {day}.",
"Scheduled hours: {hours}": "Horario programado: {hours}",
"Work area: {work_center}": "Área de trabajo: {work_center}",
"Work area: check with your supervisor.": "Área de trabajo: consulta con tu supervisor.",
```

Run: `pytest tests/test_employee_notifications.py tests/test_timeclock_notifications_routes.py tests/test_saturday_work_reminder.py tests/test_time_off_reminder.py -v`

Expected: PASS.

```bash
git add src/zira_dashboard/employee_notifications.py src/zira_dashboard/templates/timeclock_notifications.html src/zira_dashboard/saturday_work_reminder.py src/zira_dashboard/routes/timeclock.py src/zira_dashboard/templates/timeclock_success.html src/zira_dashboard/timeclock_i18n.py tests/test_employee_notifications.py tests/test_timeclock_notifications_routes.py tests/test_saturday_work_reminder.py
git commit -m "feat: remind and notify Saturday crews"
```

---

### Task 8: Persist automatic closure and run the regression gate

**Files:**
- Modify: `src/zira_dashboard/app.py`
- Create: `tests/test_saturday_recruiting_tick.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `saturday_recruiting_store.close_due(now)`.
- Produces: a one-minute closure worker; read/write correctness still enforces the deadline independently.

- [ ] **Step 1: Add the failing worker test**

```python
def test_tick_closes_due_recruitments(monkeypatch):
    seen = []
    monkeypatch.setattr(store, "close_due", lambda now: seen.append(now) or 1)
    asyncio.run(app_module._tick_saturday_recruiting())
    assert len(seen) == 1
```

- [ ] **Step 2: Register the worker**

```python
async def _tick_saturday_recruiting():
    from . import saturday_recruiting_store
    await asyncio.to_thread(saturday_recruiting_store.close_due, datetime.now(UTC))


# _WARMERS
("Saturday recruiting", _tick_saturday_recruiting, 60),
```

- [ ] **Step 3: Add concise README manager instructions**

Add a `### Optional Saturday recruiting` README section with this operational
sequence: choose position counts on the Saturday Staffing page; activate and
confirm the snapshotted shift/deadline; let qualified hourly employees commit
in Timeclock; wait for automatic closure at the nearest prior workday's start;
assign everyone from Unassigned; resolve every qualification shortage; publish.
State that partial commitments use 30-minute increments and exact Spanish skill
level 3 renders personalized Timeclock screens Spanish-first.

- [ ] **Step 4: Run focused and adjacent tests**

Run: `pytest tests/test_saturday_recruiting.py tests/test_saturday_recruiting_store.py tests/test_saturday_recruiting_manager_routes.py tests/test_saturday_recruiting_static.py tests/test_staffing_saturday_recruiting.py tests/test_timeclock_saturday_recruiting.py tests/test_saturday_work_reminder.py tests/test_employee_notifications.py tests/test_timeclock_notifications_routes.py -v`

Expected: PASS; DB tests SKIP only without `DATABASE_URL`.

Run: `pytest tests/test_staffing_view.py tests/test_staffing_schedule_metadata.py tests/test_staffing_static.py tests/test_shift_config_saturday.py tests/test_timeclock_*.py tests/test_time_off_reminder.py tests/test_saturday_recruiting_tick.py -v`

Expected: PASS.

- [ ] **Step 5: Run full verification**

Run: `pytest -v`

Expected: PASS with only documented environment/debt skips.

Run: `ruff check src/zira_dashboard tests`

Expected: PASS.

Run: `git diff --check && git status --short && git diff --stat`

Expected: no whitespace errors and no unrelated files.

- [ ] **Step 6: Commit closure and docs**

```bash
git add src/zira_dashboard/app.py tests/test_saturday_recruiting_tick.py README.md
git commit -m "docs: explain optional Saturday recruiting"
```

---

## Completion Gate

- Concurrent final-slot tests prove only one acceptance.
- Saturday Off, Unassigned, assigned, and Time Off lists match commitments.
- Every employee message shows the persisted deadline.
- Employee and manager cancellation work on the correct side of cutoff.
- Pre-cutoff and qualification-invalid publication is blocked.
- Full and partial reminders appear once without affecting the punch.
- Cancelled-Saturday notifications are idempotent.
- Exact Spanish level 3 is Spanish-first on every new personalized screen.
- Weekday Staffing and existing Timeclock tests remain green.
