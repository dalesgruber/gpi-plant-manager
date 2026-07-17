# Reopen Cancelled Saturday Offer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-show an open Saturday offer at the employee's next timeclock login after they cancel a previous commitment, with the existing full- and partial-shift choices.

**Architecture:** Keep cancellation as an auditable `cancelled` response. Change only `offer_for_person()` so that status no longer suppresses a still-open compatible offer; the existing login router and offer/partial templates will consume that result unchanged.

**Tech Stack:** Python, FastAPI, PostgreSQL, pytest.

## Global Constraints

- Reoffer only while recruitment is `recruiting`, before `response_deadline`, and compatible capacity remains.
- `declined` and `committed` responses remain terminal for the same Saturday.
- Reuse the existing Yes / No / Decide later screen and half-hour partial-availability flow; do not create UI or schema.
- Do not modify unrelated dirty worktree changes.

---

### Task 1: Reopen a cancelled response in the recruiting store

**Files:**
- Modify: `src/zira_dashboard/saturday_recruiting_store.py:432-460`
- Modify: `tests/test_saturday_recruiting_store.py:331-344`

**Interfaces:**
- Consumes: `cancel_by_employee(day: date, person_id: int, now: datetime) -> DecisionResult` and `offer_for_person(person_id: int, now: datetime) -> Offer | None`.
- Produces: `offer_for_person()` returns an `Offer` for a cancelled response when its recruitment remains open and compatible capacity exists.

- [ ] **Step 1: Write the failing store regression test**

  Add this assertion immediately after the existing cancellation assertions:

  ```python
  assert store.offer_for_person(PERSON_ID, NOW + timedelta(hours=1)) == store.Offer(
      SATURDAY, time(6, 0), time(12, 0), DEADLINE, frozenset({910101})
  )
  ```

- [ ] **Step 2: Run the store test to verify it fails**

  Run: `pytest tests/test_saturday_recruiting_store.py::test_employee_cancel_before_cutoff_reopens_capacity -v`

  Expected: FAIL because `offer_for_person()` currently skips responses whose status is `cancelled`.

- [ ] **Step 3: Make the minimum store change**

  In `offer_for_person()`, change the terminal-status guard from:

  ```python
  if existing is not None and existing.status in {"declined", "committed", "cancelled"}:
      continue
  ```

  to:

  ```python
  if existing is not None and existing.status in {"declined", "committed"}:
      continue
  ```

  Keep the qualification and `_coverage_with_candidate()` checks unchanged so a reoffer still requires an eligible open position.

- [ ] **Step 4: Run the focused store tests**

  Run: `pytest tests/test_saturday_recruiting_store.py -v`

  Expected: PASS, including the new reoffer test and the existing cutoff, repeated-cancellation, decline, and later-Saturday tests.

- [ ] **Step 5: Commit the store behavior**

  ```bash
  git add src/zira_dashboard/saturday_recruiting_store.py tests/test_saturday_recruiting_store.py
  git commit -m "fix: reoffer Saturday work after cancellation"
  ```

### Task 2: Verify login routing and partial availability remain available

**Files:**
- Modify: `tests/test_timeclock_saturday_recruiting.py:47-88`

**Interfaces:**
- Consumes: `timeclock.saturday_recruiting_store.offer_for_person(person_id, now)` and `POST /timeclock/saturday/partial/{token}`.
- Produces: an eligible returning employee is redirected to `/timeclock/saturday/{token}`, and a valid partial range reaches the existing confirmation page.

- [ ] **Step 1: Write the route regressions**

  Add a name-tap test that names the cancelled state in the store stub and verifies it still returns `OFFER`:

  ```python
  def test_name_tap_after_cancel_routes_employee_back_to_offer(monkeypatch):
      _person(monkeypatch)
      monkeypatch.setattr(employee_notifications, "notifications_enabled", lambda: False)
      monkeypatch.setattr(
          timeclock.saturday_recruiting_store, "offer_for_person", lambda *_args: OFFER
      )

      response = client.get("/timeclock/start/1", follow_redirects=False)

      assert response.status_code == 303
      assert "/timeclock/saturday/" in response.headers["location"]
  ```

  Add a valid partial submission assertion to `test_partial_options_and_tampered_minutes`:

  ```python
  valid = client.post(
      f"/timeclock/saturday/partial/{token}",
      data={"availability_start": "07:30", "availability_end": "11:30"},
  )
  assert valid.status_code == 200
  assert "Confirm your commitment" in valid.text
  assert "7:30 AM–11:30 AM" in valid.text
  ```

- [ ] **Step 2: Run the route tests to verify the assertions pass with the store contract**

  Run: `pytest tests/test_timeclock_saturday_recruiting.py -v`

  Expected: PASS. The router already redirects whenever `offer_for_person()` yields `OFFER`, and the existing partial handler accepts valid half-hour ranges.

- [ ] **Step 3: Run the complete Saturday regression suite**

  Run: `pytest tests/test_saturday_recruiting_store.py tests/test_timeclock_saturday_recruiting.py tests/test_staffing_saturday_recruiting.py -v`

  Expected: PASS, or database-backed store tests are reported as skipped when `DATABASE_URL` is not configured.

- [ ] **Step 4: Commit route regression coverage**

  ```bash
  git add tests/test_timeclock_saturday_recruiting.py
  git commit -m "test: cover returning Saturday offer and partial availability"
  ```
