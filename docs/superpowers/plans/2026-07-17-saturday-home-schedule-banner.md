# Saturday Home Schedule Banner Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Keep the timeclock Saturday header visible after recruiting closes, give it accurate tomorrow/today copy through the Saturday shift, and allow employees to view only the published Saturday schedule.

**Architecture:** Extend the recruiting-store home-banner value with a server-calculated phase and its snapshotted shift hours. The timeclock route resolves a published schedule or preserved posted snapshot into a display-only payload; the home template renders planned-state buttons and an accessible modal.

**Tech Stack:** Python 3.11, FastAPI, PostgreSQL, Jinja2, vanilla JavaScript, pytest.

## Global Constraints

- All clock comparisons use plant-local SITE_TZ; no browser clock decides the banner state.
- available retains the existing bilingual copy and requires remaining openings.
- tomorrow and today never mention openings or a response deadline.
- Planned popups expose only published assignments or published_snapshot assignments; drafts never appear.
- Cancelled recruitments and times at or after the snapshotted Saturday shift end produce no banner.
- Preserve all unrelated workspace changes and stage only files named by the task being committed.

---

## File Structure

| File | Responsibility |
| --- | --- |
| src/zira_dashboard/saturday_recruiting_store.py | Select and classify the active Saturday banner state. |
| tests/test_saturday_recruiting_store.py | Database-backed banner lifecycle regressions. |
| src/zira_dashboard/routes/timeclock.py | Build safe, published-only schedule context. |
| tests/test_timeclock_saturday_recruiting.py | Home-route rendering and confidentiality checks. |
| src/zira_dashboard/templates/timeclock_home.html | Planned header, modal markup, and modal behavior. |
| src/zira_dashboard/templates/timeclock_base.html | Kiosk planned-banner and schedule-modal styles. |
| tests/test_timeclock_home_static.py | Static modal accessibility checks. |

### Task 1: Classify the active Saturday banner

**Files:**

- Modify: src/zira_dashboard/saturday_recruiting_store.py:53-59,463-481
- Modify: tests/test_saturday_recruiting_store.py:331-345

**Interfaces:**

- Produces: HomeBanner(day, response_deadline, remaining_count, phase, shift_start, shift_end), with phase exactly available, tomorrow, or today.

- [ ] **Step 1: Write the failing lifecycle tests**

    def test_home_banner_becomes_tomorrow_plan_at_the_response_deadline():
        _activate(requested_counts={910101: 1})
        assert store.home_banner(DEADLINE) == store.HomeBanner(
            SATURDAY, DEADLINE, 0, "tomorrow", time(6), time(12)
        )

    def test_home_banner_becomes_today_plan_until_the_snapshotted_shift_ends():
        _activate(requested_counts={910101: 1})
        assert store.home_banner(datetime(2026, 7, 25, 11, 59, tzinfo=SITE_TZ)) == store.HomeBanner(
            SATURDAY, DEADLINE, 0, "today", time(6), time(12)
        )
        assert store.home_banner(datetime(2026, 7, 25, 12, 0, tzinfo=SITE_TZ)) is None

    def test_home_banner_never_shows_a_cancelled_saturday():
        _activate(requested_counts={910101: 1})
        store.cancel_recruitment(SATURDAY, "manager@gruberpallets.com", DEADLINE)
        assert store.home_banner(datetime(2026, 7, 24, 8, tzinfo=SITE_TZ)) is None

Update the existing pre-deadline capacity-reopen test to assert phase == "available" and remaining_count == 1.

- [ ] **Step 2: Run the new test file and verify RED**

Run: pytest tests/test_saturday_recruiting_store.py -q

Expected: database-enabled execution fails because HomeBanner has no phase/shift data and home_banner excludes closed records. With no DATABASE_URL, pytest skips this module; rerun it in the configured Postgres environment before continuing.

- [ ] **Step 3: Implement minimal server-side classification**

Add timedelta to the datetime imports, expand HomeBanner, and replace home_banner with this logic:

    @dataclass(frozen=True)
    class HomeBanner:
        day: date
        response_deadline: datetime
        remaining_count: int
        phase: str
        shift_start: time
        shift_end: time

    def home_banner(now: datetime) -> HomeBanner | None:
        from . import db
        local_now = now.astimezone(sr.SITE_TZ)
        with db.cursor() as cur:
            cur.execute(
                "SELECT day FROM saturday_recruitments "
                "WHERE status <> 'cancelled' AND day >= %s ORDER BY day",
                (local_now.date(),),
            )
            for row in cur.fetchall():
                bundle = _load_bundle(cur, row["day"])
                assert bundle is not None
                recruitment = bundle.recruitment
                shift_end = datetime.combine(recruitment.day, recruitment.shift_end, tzinfo=sr.SITE_TZ)
                if recruitment.day == local_now.date():
                    if local_now >= shift_end:
                        continue
                    return HomeBanner(recruitment.day, recruitment.response_deadline, 0, "today", recruitment.shift_start, recruitment.shift_end)
                if local_now < recruitment.response_deadline and recruitment.status == "recruiting":
                    remaining = _remaining_count(bundle)
                    if remaining > 0:
                        return HomeBanner(recruitment.day, recruitment.response_deadline, remaining, "available", recruitment.shift_start, recruitment.shift_end)
                    continue
                if recruitment.day == local_now.date() + timedelta(days=1):
                    return HomeBanner(recruitment.day, recruitment.response_deadline, 0, "tomorrow", recruitment.shift_start, recruitment.shift_end)
            return None

This makes exactly Friday 7:00 AM a planned state even before the minute-based close tick runs.

- [ ] **Step 4: Run the store test file and verify GREEN**

Run: pytest tests/test_saturday_recruiting_store.py -q

Expected: all database-enabled tests pass; without a configured DB, the module is skipped only.

- [ ] **Step 5: Commit the completed task**

Run: git add src/zira_dashboard/saturday_recruiting_store.py tests/test_saturday_recruiting_store.py && git commit -m "feat: keep Saturday home banner through shift end"

### Task 2: Build a published-only timeclock payload

**Files:**

- Modify: src/zira_dashboard/routes/timeclock.py:450-465
- Modify: tests/test_timeclock_saturday_recruiting.py:1-37

**Interfaces:**

- Consumes: HomeBanner.phase and staffing.load_schedule(day).
- Produces: planned-banner published and assignments fields, each safe to render to any kiosk user.

- [ ] **Step 1: Write failing route tests**

Add the staffing import and update the current available HomeBanner construction with "available", time(7), time(12):

    PLANNED_BANNER = HomeBanner(
        OFFER.day, OFFER.response_deadline, 0, "tomorrow", time(7), time(12)
    )

    def test_home_shows_tomorrow_plan_and_only_published_assignments(monkeypatch):
        monkeypatch.setattr(timeclock.db, "query", lambda *_args: [])
        monkeypatch.setattr(timeclock.saturday_recruiting_store, "home_banner", lambda _now: PLANNED_BANNER)
        monkeypatch.setattr(timeclock.staffing, "load_schedule", lambda _day: staffing.Schedule(
            OFFER.day, published=True, assignments={"Repair 1": ["Ana", "Bob"]}
        ))
        response = client.get("/timeclock")
        assert "Saturday planned for tomorrow" in response.text
        assert "Repair 1" in response.text and "Ana" in response.text and "Bob" in response.text
        assert "Saturday Work Available" not in response.text

    def test_home_uses_posted_snapshot_and_never_exposes_draft_assignments(monkeypatch):
        monkeypatch.setattr(timeclock.db, "query", lambda *_args: [])
        monkeypatch.setattr(timeclock.saturday_recruiting_store, "home_banner", lambda _now: PLANNED_BANNER)
        monkeypatch.setattr(timeclock.staffing, "load_schedule", lambda _day: staffing.Schedule(
            OFFER.day, published=False, assignments={"Draft WC": ["Draft Person"]},
            published_snapshot={"assignments": {"Posted WC": ["Posted Person"]}},
        ))
        response = client.get("/timeclock")
        assert "Posted WC" in response.text and "Posted Person" in response.text
        assert "Draft WC" not in response.text and "Draft Person" not in response.text

- [ ] **Step 2: Run targeted route tests and verify RED**

Run: pytest tests/test_timeclock_saturday_recruiting.py -q

Expected: planned-state assertions fail because the existing route only emits deadline and opening data.

- [ ] **Step 3: Implement published-only context helpers**

Add the following above _saturday_banner_context and retain its broad error guard:

    def _published_schedule_assignments(day) -> tuple[bool, list[dict[str, object]]]:
        schedule = staffing.load_schedule(day)
        if schedule.published:
            assignments = schedule.assignments or {}
        elif schedule.published_snapshot:
            assignments = schedule.published_snapshot.get("assignments") or {}
        else:
            return False, []
        return True, [
            {"work_center": name, "people": list(names or [])}
            for name, names in assignments.items() if names
        ]

    def _saturday_plan_context(banner, sr) -> dict:
        published, assignments = _published_schedule_assignments(banner.day)
        return {
            "phase": banner.phase, "day": banner.day.isoformat(),
            "day_label": f"{banner.day.strftime('%A, %B')} {banner.day.day}",
            "shift_label": sr.format_time_range(banner.shift_start, banner.shift_end),
            "published": published, "assignments": assignments,
        }

For available, return the current mapping unchanged. For tomorrow and today, return _saturday_plan_context(banner, sr).

- [ ] **Step 4: Run the route tests and verify GREEN**

Run: pytest tests/test_timeclock_saturday_recruiting.py -q

Expected: all tests pass, including bilingual availability and draft-confidentiality coverage.

- [ ] **Step 5: Commit the completed task**

Run: git add src/zira_dashboard/routes/timeclock.py tests/test_timeclock_saturday_recruiting.py && git commit -m "feat: expose published Saturday schedule to timeclock"

### Task 3: Render the planned header and accessible schedule modal

**Files:**

- Modify: src/zira_dashboard/templates/timeclock_home.html:4-10,61-69
- Modify: src/zira_dashboard/templates/timeclock_base.html:20-27,133-151
- Create: tests/test_timeclock_home_static.py

**Interfaces:**

- Consumes: planned saturday_banner context from Task 2.
- Produces: #saturday-schedule-trigger and #saturday-schedule-modal with keyboard, backdrop, and focus-return behavior.

- [ ] **Step 1: Write the failing static test**

    from pathlib import Path

    def test_planned_saturday_header_opens_an_accessible_schedule_modal():
        html = Path("src/zira_dashboard/templates/timeclock_home.html").read_text()
        assert 'id="saturday-schedule-trigger"' in html
        assert 'aria-haspopup="dialog"' in html
        assert 'id="saturday-schedule-modal"' in html
        assert 'role="dialog"' in html and 'aria-modal="true"' in html
        assert 'Saturday schedule has not been published yet.' in html
        assert "event.key === 'Escape'" in html
        assert "scheduleTrigger.focus()" in html

- [ ] **Step 2: Run the static test and verify RED**

Run: pytest tests/test_timeclock_home_static.py -q

Expected: FAIL because the existing home template contains only the availability banner.

- [ ] **Step 3: Implement modal markup, behavior, and styles**

Preserve the exact existing available markup. For planned phases, render:

    <button type="button" id="saturday-schedule-trigger" class="saturday-home-banner saturday-home-plan"
            aria-haspopup="dialog" aria-controls="saturday-schedule-modal">
      <strong>Saturday planned for {{ saturday_banner.phase }}</strong>
      <small>{{ saturday_banner.day_label }} · {{ saturday_banner.shift_label }}</small>
    </button>

Render a planned-only saturday-schedule-modal using k-modal-overlay, a dialog card with role dialog and aria-modal true, a Close button, date/hours, assignment.work_center and assignment.people joined by commas, and the exact unavailable text Saturday schedule has not been published yet. when published is false.

Add an IIFE that uses scheduleTrigger, scheduleModal, and scheduleClose: focus Close when opened; set hidden true on Close, backdrop click, or event.key === 'Escape'; remove the keydown listener; then call scheduleTrigger.focus().

In timeclock_base.html, add button-reset rules for saturday-home-plan and list/card rules for saturday-schedule-card and saturday-schedule-assignment. Use max-height: min(70vh, 620px); overflow-y: auto; so long assignment lists work on kiosks.

- [ ] **Step 4: Run static and route tests and verify GREEN**

Run: pytest tests/test_timeclock_home_static.py tests/test_timeclock_saturday_recruiting.py -q

Expected: PASS.

- [ ] **Step 5: Commit the completed task**

Run: git add src/zira_dashboard/templates/timeclock_home.html src/zira_dashboard/templates/timeclock_base.html tests/test_timeclock_home_static.py && git commit -m "feat: add Saturday schedule popup to timeclock"

### Task 4: Verify the complete feature

**Files:**

- Verify: all files in Tasks 1–3.

- [ ] **Step 1: Run the focused suite**

Run: pytest tests/test_saturday_recruiting_store.py tests/test_timeclock_saturday_recruiting.py tests/test_timeclock_home_static.py -q

Expected: no failures. A missing DATABASE_URL may skip only the PostgreSQL store module; rerun it in the database-backed environment.

- [ ] **Step 2: Run all tests and lint**

Run: pytest -q && ruff check src/zira_dashboard tests

Expected: pytest exits 0 with no failures and Ruff prints All checks passed!.

- [ ] **Step 3: Inspect exact changes before handoff**

Run: git status --short && git diff HEAD~3..HEAD --check

Expected: no whitespace errors; unrelated pre-existing changes remain unmodified.
