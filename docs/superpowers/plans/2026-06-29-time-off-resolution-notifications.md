# Time-Off Resolution Notifications & Day-Before Reminder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell kiosk employees what happened to their time off — a tap-to-acknowledge popup at next sign-in when a request is approved/denied/cancelled, plus a "time off tomorrow" reminder when they clock out on the last working day before approved leave.

**Architecture:** A new `employee_notifications` table holds one row per resolution popup. Generation rides the existing time-off poller's state-change detection in `time_off_sync._upsert_one` (so re-polling can't re-fire). Display is a sign-in interstitial inserted at the kiosk's single entry chokepoint (`kiosk_start`). The day-before reminder is computed live at clock-out from `time_off_requests` + a weekend-skip "next working day" rule — no stored row.

**Tech Stack:** Python 3.12, FastAPI, Jinja2 templates, PostgreSQL via `psycopg2` (`db.query`/`db.execute`), pytest with `monkeypatch` (DB and Odoo are stubbed — tests never hit a live DB).

**Conventions observed in this repo:**
- Run tests with: `ZIRA_API_KEY=test .venv/bin/python -m pytest <path> -v`
- Unit tests stub `db.query`/`db.execute` via `monkeypatch.setattr(<module>.db, ...)`; route tests use `TestClient(app)` (conftest sets `AUTH_DISABLED=1`) and monkeypatch the route module's helpers.
- Windows/POSIX strftime: use `%#d`/`%#I` on `os.name == "nt"`, else `%-d`/`%-I` (existing pattern in `routes/timeclock.py`).
- Commit after each task.

**Note on two refinements vs. the spec (deliberate, both documented here):**
1. The spec named `acknowledge(notification_id, person_odoo_id)`; the UI uses a single "Got it" button, so we implement person-scoped `acknowledge_all(person_odoo_id)` instead (still owner-safe — only the signing-in person's rows are cleared).
2. The spec said "next working day via resource calendar." `odoo_client.fetch_resource_calendar` collapses across weekdays and doesn't expose which days a person works, and `work_schedule_store` only holds calendars with rounding overrides. Since this is a Mon–Fri plant, we use a **weekend-skip** rule (skip Sat/Sun) for "next working day." No Odoo call on the clock-out hot path; trivially testable. Per-person calendars can refine this later.

---

## File Structure

**New files:**
- `src/zira_dashboard/employee_notifications.py` — the table's data access (create/list/has/ack), message rendering, the resolution-state→notification rule (`maybe_notify_resolution`), and the shared `notifications_enabled()` kill-switch.
- `src/zira_dashboard/time_off_reminder.py` — live day-before reminder: `next_working_day()` (pure) + `reminder_for_person()` + message rendering.
- `src/zira_dashboard/templates/timeclock_notifications.html` — the sign-in interstitial.
- `tests/test_schema_employee_notifications.py`
- `tests/test_employee_notifications.py`
- `tests/test_time_off_reminder.py`
- `tests/test_timeclock_notifications_routes.py`

**Modified files:**
- `src/zira_dashboard/_schema.py` — add the `employee_notifications` table + indexes to `SCHEMA_DDL`.
- `src/zira_dashboard/time_off_sync.py` — call `employee_notifications.maybe_notify_resolution(...)` on state change and on insert-already-validated.
- `src/zira_dashboard/routes/timeclock.py` — sign-in interstitial hook in `kiosk_start`, two new routes, clock-out reminder wiring.
- `src/zira_dashboard/templates/timeclock_success.html` — render the reminder card; suppress auto-redirect when a reminder is present.
- `tests/test_time_off_sync.py` — assert `_upsert_one` invokes `maybe_notify_resolution`.

---

## Task 1: Schema — `employee_notifications` table

**Files:**
- Modify: `src/zira_dashboard/_schema.py` (inside the `SCHEMA_DDL` string, after the `time_off_balances` table block, ~line 709)
- Test: `tests/test_schema_employee_notifications.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_schema_employee_notifications.py`:

```python
from zira_dashboard._schema import SCHEMA_DDL


def test_schema_defines_employee_notifications_table():
    assert "CREATE TABLE IF NOT EXISTS employee_notifications" in SCHEMA_DDL
    for col in (
        "person_odoo_id", "kind", "time_off_request_id", "odoo_leave_id",
        "title", "body", "leave_date_from", "leave_date_to",
        "created_at", "acknowledged_at",
    ):
        assert col in SCHEMA_DDL, f"missing column {col}"


def test_schema_has_employee_notifications_indexes():
    # Hard dedupe backstop: one notification per (request, kind).
    assert "employee_notifications_dedupe" in SCHEMA_DDL
    assert "(time_off_request_id, kind)" in SCHEMA_DDL
    # Fast unacknowledged lookup at sign-in.
    assert "employee_notifications_unack" in SCHEMA_DDL
    assert "WHERE acknowledged_at IS NULL" in SCHEMA_DDL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_schema_employee_notifications.py -v`
Expected: FAIL — `assert "CREATE TABLE IF NOT EXISTS employee_notifications" in SCHEMA_DDL` is False.

- [ ] **Step 3: Add the table to `SCHEMA_DDL`**

In `src/zira_dashboard/_schema.py`, immediately after the `time_off_balances` table + its statements (after line ~709, before the `scheduler_moves` comment at ~711), insert this DDL **inside the `SCHEMA_DDL` triple-quoted string**:

```sql
-- Employee-facing kiosk notifications. One row = one thing to tell an
-- employee at their next time-clock sign-in. Currently sourced only from
-- time-off resolutions (approved/denied/cancelled). `acknowledged_at`
-- records the "Got it" tap so a notification never shows twice. Leave
-- dates are snapshotted so the message stays correct even if the source
-- time_off_requests row later changes or is deleted.
CREATE TABLE IF NOT EXISTS employee_notifications (
  id                   BIGSERIAL PRIMARY KEY,
  person_odoo_id       INTEGER NOT NULL,
  kind                 TEXT NOT NULL,
  time_off_request_id  BIGINT,
  odoo_leave_id        INTEGER,
  title                TEXT NOT NULL,
  body                 TEXT NOT NULL,
  leave_date_from      DATE,
  leave_date_to        DATE,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  acknowledged_at      TIMESTAMPTZ
);
-- Hard dedupe backstop: generation only fires on observed transitions, but
-- this guarantees at most one notification per (request, kind) even if a
-- poll double-processes a row.
CREATE UNIQUE INDEX IF NOT EXISTS employee_notifications_dedupe
  ON employee_notifications (time_off_request_id, kind);
-- Sign-in hot path: "does this person have anything to show?"
CREATE INDEX IF NOT EXISTS employee_notifications_unack
  ON employee_notifications (person_odoo_id) WHERE acknowledged_at IS NULL;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_schema_employee_notifications.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/_schema.py tests/test_schema_employee_notifications.py
git commit -m "feat(notifications): add employee_notifications table"
```

---

## Task 2: `employee_notifications` module — data access, rendering, kill-switch

**Files:**
- Create: `src/zira_dashboard/employee_notifications.py`
- Test: `tests/test_employee_notifications.py`

This task builds everything except `maybe_notify_resolution` (Task 3 adds that into the same module).

- [ ] **Step 1: Write the failing test**

Create `tests/test_employee_notifications.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from zira_dashboard import employee_notifications as en


@pytest.fixture
def fake_db(monkeypatch):
    captured: dict = {"queries": [], "executes": []}

    def fake_query(sql, params=None):
        captured["queries"].append((sql, params))
        return captured.get("query_result", [])

    def fake_execute(sql, params=None):
        captured["executes"].append((sql, params))

    monkeypatch.setattr(en.db, "query", fake_query)
    monkeypatch.setattr(en.db, "execute", fake_execute)
    return captured


def test_notifications_enabled_default_on(monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    assert en.notifications_enabled() is True


def test_notifications_enabled_off_when_zero(monkeypatch):
    monkeypatch.setenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", "0")
    assert en.notifications_enabled() is False


def test_create_inserts_with_on_conflict_do_nothing(fake_db):
    req = {
        "id": 7, "person_odoo_id": 5, "odoo_leave_id": 88,
        "date_from": date(2026, 7, 1), "date_to": date(2026, 7, 3),
    }
    en.create_time_off_notification(5, "time_off_approved", req)

    assert len(fake_db["executes"]) == 1
    sql, params = fake_db["executes"][0]
    assert "INSERT INTO employee_notifications" in sql
    assert "ON CONFLICT (time_off_request_id, kind) DO NOTHING" in sql
    # person, kind, request id, odoo leave id are all carried through.
    assert params[0] == 5
    assert params[1] == "time_off_approved"
    assert 7 in params and 88 in params


def test_render_messages_distinct_per_kind():
    req = {"date_from": date(2026, 7, 1), "date_to": date(2026, 7, 1)}
    approved_title, approved_body = en._render("time_off_approved", req)
    denied_title, denied_body = en._render("time_off_denied", req)
    cancelled_title, cancelled_body = en._render("time_off_cancelled", req)
    assert "approved" in approved_body.lower()
    assert "denied" in denied_body.lower()
    assert "cancelled" in cancelled_body.lower()
    # Single-day leaves render one date, not a span.
    assert "–" not in approved_body


def test_render_multi_day_shows_span():
    req = {"date_from": date(2026, 7, 1), "date_to": date(2026, 7, 3)}
    _, body = en._render("time_off_approved", req)
    assert "–" in body  # "Jul 1 – Jul 3"


def test_has_unacknowledged_true_when_rows(fake_db):
    fake_db["query_result"] = [{"?column?": 1}]
    assert en.has_unacknowledged(5) is True
    sql, params = fake_db["queries"][0]
    assert "acknowledged_at IS NULL" in sql
    assert params == (5,)


def test_has_unacknowledged_false_when_empty(fake_db):
    fake_db["query_result"] = []
    assert en.has_unacknowledged(5) is False


def test_list_unacknowledged_filters_by_person_and_unacked(fake_db):
    fake_db["query_result"] = [{"id": 1, "title": "t", "body": "b"}]
    out = en.list_unacknowledged(5)
    assert out == [{"id": 1, "title": "t", "body": "b"}]
    sql, params = fake_db["queries"][0]
    assert "acknowledged_at IS NULL" in sql
    assert "ORDER BY created_at" in sql
    assert params == (5,)


def test_acknowledge_all_is_person_scoped(fake_db):
    en.acknowledge_all(5)
    sql, params = fake_db["executes"][0]
    assert "UPDATE employee_notifications SET acknowledged_at = now()" in sql
    assert "person_odoo_id = %s" in sql
    assert "acknowledged_at IS NULL" in sql
    assert params == (5,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_employee_notifications.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'zira_dashboard.employee_notifications'`.

- [ ] **Step 3: Create the module**

Create `src/zira_dashboard/employee_notifications.py`:

```python
"""Employee-facing kiosk notifications.

One row in ``employee_notifications`` == one thing to tell an employee at
their next time-clock sign-in. The only source today is time-off
resolutions (approved / denied / cancelled). ``acknowledged_at`` records
the "Got it" tap so a notification never shows twice.

Generation (``maybe_notify_resolution``) rides the time-off poller's
state-change detection in ``time_off_sync._upsert_one`` — see that module.
Display is the kiosk sign-in interstitial in ``routes/timeclock.py``.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any

from . import db, shift_config

_NOTIFY_ENV = "KIOSK_TIME_OFF_NOTIFY_ENABLED"


def notifications_enabled() -> bool:
    """Kill-switch. Default ON; set KIOSK_TIME_OFF_NOTIFY_ENABLED=0 to disable
    both the resolution popups and the day-before reminder without touching
    the rest of the time-off feature."""
    return os.environ.get(_NOTIFY_ENV, "1").strip().lower() not in (
        "0", "false", "no",
    )


def _plant_today() -> date:
    return datetime.now(timezone.utc).astimezone(shift_config.SITE_TZ).date()


def _md(d: date) -> str:
    """'Jul 1' — no leading zero on the day. Windows needs %#d for that."""
    return d.strftime("%b %#d") if os.name == "nt" else d.strftime("%b %-d")


def _date_span_label(date_from: date, date_to: date | None) -> str:
    if date_to and date_to != date_from:
        return f"{_md(date_from)} – {_md(date_to)}"
    return _md(date_from)


def _render(kind: str, req: dict[str, Any]) -> tuple[str, str]:
    """Return (title, body) for a resolution notification."""
    span = _date_span_label(req["date_from"], req.get("date_to"))
    if kind == "time_off_approved":
        return ("Time off approved",
                f"Your time off for {span} was approved. ✅")
    if kind == "time_off_denied":
        return ("Time off denied",
                f"Your time off request for {span} was denied. ❌ "
                "See a supervisor if you have questions.")
    return ("Time off cancelled",
            f"Your approved time off for {span} was cancelled. ⚠️ "
            "See a supervisor if you have questions.")


def create_time_off_notification(
    person_odoo_id: int, kind: str, req: dict[str, Any],
) -> None:
    """Insert one notification. The unique (time_off_request_id, kind) index
    + ON CONFLICT DO NOTHING make this idempotent if a poll re-processes the
    same transition."""
    title, body = _render(kind, req)
    db.execute(
        "INSERT INTO employee_notifications "
        "(person_odoo_id, kind, time_off_request_id, odoo_leave_id, "
        " title, body, leave_date_from, leave_date_to) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (time_off_request_id, kind) DO NOTHING",
        (person_odoo_id, kind, req.get("id"), req.get("odoo_leave_id"),
         title, body, req.get("date_from"), req.get("date_to")),
    )


def has_unacknowledged(person_odoo_id: int) -> bool:
    rows = db.query(
        "SELECT 1 FROM employee_notifications "
        "WHERE person_odoo_id = %s AND acknowledged_at IS NULL LIMIT 1",
        (person_odoo_id,),
    )
    return bool(rows)


def list_unacknowledged(person_odoo_id: int) -> list[dict]:
    return db.query(
        "SELECT id, kind, title, body, leave_date_from, leave_date_to, "
        "created_at FROM employee_notifications "
        "WHERE person_odoo_id = %s AND acknowledged_at IS NULL "
        "ORDER BY created_at",
        (person_odoo_id,),
    )


def acknowledge_all(person_odoo_id: int) -> None:
    """Mark every unacknowledged notification for this person as seen. The
    single 'Got it' button clears the whole stack; person-scoped so a stale
    token can only ever clear its own person's rows."""
    db.execute(
        "UPDATE employee_notifications SET acknowledged_at = now() "
        "WHERE person_odoo_id = %s AND acknowledged_at IS NULL",
        (person_odoo_id,),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_employee_notifications.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/employee_notifications.py tests/test_employee_notifications.py
git commit -m "feat(notifications): employee_notifications data access + rendering"
```

---

## Task 3: Generation rule + wire into the poller

**Files:**
- Modify: `src/zira_dashboard/employee_notifications.py` (add `maybe_notify_resolution`)
- Modify: `src/zira_dashboard/time_off_sync.py` (import + two call sites in `_upsert_one`, ~lines 497–544)
- Test: `tests/test_employee_notifications.py` (add cases), `tests/test_time_off_sync.py` (add one wiring test)

The existing scheduler `cascade_on_state_change` only fires forward into `validate` and reverse out of `validate` — so it **misses** the common `confirm → refuse` denial (a pending request denied before it was ever approved). Generation therefore lives in its own rule, not inside that cascade.

- [ ] **Step 1: Write the failing tests for the rule**

Add to `tests/test_employee_notifications.py`:

```python
def _req(state, date_to=date(2026, 7, 3), **extra):
    base = {
        "id": 7, "person_odoo_id": 5, "odoo_leave_id": 88, "state": state,
        "date_from": date(2026, 7, 1), "date_to": date(2026, 7, 3),
    }
    base.update(extra)
    base["date_to"] = date_to
    return base


def test_notify_on_approve(fake_db, monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    en.maybe_notify_resolution(_req("confirm"), _req("validate"),
                               today=date(2026, 6, 29))
    inserts = [e for e in fake_db["executes"]
               if "INSERT INTO employee_notifications" in e[0]]
    assert len(inserts) == 1
    assert "time_off_approved" in inserts[0][1]


def test_notify_on_deny_from_confirm(fake_db, monkeypatch):
    # The case the scheduler cascade misses: deny a never-approved request.
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    en.maybe_notify_resolution(_req("confirm"), _req("refuse"),
                               today=date(2026, 6, 29))
    inserts = [e for e in fake_db["executes"]
               if "INSERT INTO employee_notifications" in e[0]]
    assert len(inserts) == 1
    assert "time_off_denied" in inserts[0][1]


def test_no_notify_on_self_cancel_pushed_as_refuse(fake_db, monkeypatch):
    # Employee cancelled their own approved request -> Odoo records 'refuse'
    # from local 'draft_cancel'. Not a denial: suppress.
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    en.maybe_notify_resolution(_req("draft_cancel"), _req("refuse"),
                               today=date(2026, 6, 29))
    assert not [e for e in fake_db["executes"]
                if "INSERT INTO employee_notifications" in e[0]]


def test_no_notify_on_self_cancel_to_cancel(fake_db, monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    en.maybe_notify_resolution(_req("draft_cancel"), _req("cancel"),
                               today=date(2026, 6, 29))
    assert not [e for e in fake_db["executes"]
                if "INSERT INTO employee_notifications" in e[0]]


def test_no_notify_for_past_leave(fake_db, monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    en.maybe_notify_resolution(
        _req("confirm", date_to=date(2026, 6, 20)),
        _req("validate", date_to=date(2026, 6, 20)),
        today=date(2026, 6, 29),
    )
    assert not [e for e in fake_db["executes"]
                if "INSERT INTO employee_notifications" in e[0]]


def test_no_notify_for_non_resolution_transition(fake_db, monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    en.maybe_notify_resolution(_req("draft"), _req("confirm"),
                               today=date(2026, 6, 29))
    assert not fake_db["executes"]


def test_no_notify_when_disabled(fake_db, monkeypatch):
    monkeypatch.setenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", "0")
    en.maybe_notify_resolution(_req("confirm"), _req("validate"),
                               today=date(2026, 6, 29))
    assert not fake_db["executes"]
```

- [ ] **Step 2: Run to verify failure**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_employee_notifications.py -k notify -v`
Expected: FAIL — `AttributeError: module 'zira_dashboard.employee_notifications' has no attribute 'maybe_notify_resolution'`.

- [ ] **Step 3: Add `maybe_notify_resolution` to the module**

In `src/zira_dashboard/employee_notifications.py`, add the kind map near the top (after `_NOTIFY_ENV`):

```python
# Odoo/local state a request lands in -> the notification we raise.
_RESOLUTION_KIND = {
    "validate": "time_off_approved",
    "refuse": "time_off_denied",
    "cancel": "time_off_cancelled",
}
```

And add this function (e.g. after `create_time_off_notification`):

```python
def maybe_notify_resolution(
    old: dict[str, Any], new: dict[str, Any], today: date | None = None,
) -> None:
    """Raise a resolution notification when a request transitions into an
    approved/denied/cancelled state. Called from ``time_off_sync._upsert_one``
    on every observed state change and on insert-already-validated.

    Suppressed when:
      - the feature is off,
      - the new state isn't a resolution,
      - the change is the employee's own cancellation (local prior state
        ``draft_cancel`` — Odoo records that as a refuse/cancel, which is not
        a denial),
      - the leave is entirely in the past (date_to < today).
    """
    if not notifications_enabled():
        return
    kind = _RESOLUTION_KIND.get(new.get("state"))
    if kind is None:
        return
    if old.get("state") == "draft_cancel":
        return
    date_to = new.get("date_to")
    today = today or _plant_today()
    if date_to is None or date_to < today:
        return
    create_time_off_notification(new["person_odoo_id"], kind, new)
```

- [ ] **Step 4: Run the rule tests to verify pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_employee_notifications.py -v`
Expected: PASS (all, including the new `notify` cases).

- [ ] **Step 5: Write the failing wiring test**

Add to `tests/test_time_off_sync.py` (it already has the `fake_db` fixture and imports `time_off_sync`):

```python
def test_upsert_update_calls_notify_on_state_change(monkeypatch, fake_db):
    from unittest.mock import MagicMock
    existing = {
        "id": 1, "person_odoo_id": 5, "state": "confirm", "shape": "full_day",
        "date_from": date(2026, 7, 1), "date_to": date(2026, 7, 3),
        "hour_from": None, "hour_to": None, "odoo_leave_id": 88,
    }
    leave = {
        "id": 88, "state": "validate",
        "employee_id": (5, "X"), "holiday_status_id": (1, "PTO"),
        "request_date_from": "2026-07-01", "request_date_to": "2026-07-03",
        "number_of_days": 3, "request_unit_hours": False,
        "request_hour_from": False, "request_hour_to": False,
        "name": "PTO",
    }
    notify = MagicMock()
    monkeypatch.setattr(time_off_sync.employee_notifications,
                        "maybe_notify_resolution", notify)
    # Don't let the scheduler cascade run real DB writes here.
    monkeypatch.setattr(time_off_sync, "cascade_on_state_change",
                        MagicMock())

    time_off_sync._upsert_one(leave, existing)

    notify.assert_called_once()
    old_arg, new_arg = notify.call_args[0][0], notify.call_args[0][1]
    assert old_arg["state"] == "confirm"
    assert new_arg["state"] == "validate"


def test_upsert_insert_validate_calls_notify(monkeypatch, fake_db):
    from unittest.mock import MagicMock
    leave = {
        "id": 99, "state": "validate",
        "employee_id": (5, "X"), "holiday_status_id": (1, "PTO"),
        "request_date_from": "2026-07-01", "request_date_to": "2026-07-03",
        "number_of_days": 3, "request_unit_hours": False,
        "request_hour_from": False, "request_hour_to": False,
        "name": "PTO",
    }
    # The re-SELECT after INSERT returns the new row.
    fake_db["query_result"] = [{
        "id": 2, "person_odoo_id": 5, "state": "validate", "shape": "full_day",
        "date_from": date(2026, 7, 1), "date_to": date(2026, 7, 3),
        "hour_from": None, "hour_to": None, "odoo_leave_id": 99,
    }]
    notify = MagicMock()
    monkeypatch.setattr(time_off_sync.employee_notifications,
                        "maybe_notify_resolution", notify)
    monkeypatch.setattr(time_off_sync, "cascade_on_state_change", MagicMock())

    time_off_sync._upsert_one(leave, None)

    notify.assert_called_once()
```

- [ ] **Step 6: Run to verify failure**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_sync.py -k notify -v`
Expected: FAIL — `AttributeError: module 'zira_dashboard.time_off_sync' has no attribute 'employee_notifications'`.

- [ ] **Step 7: Wire into `time_off_sync._upsert_one`**

In `src/zira_dashboard/time_off_sync.py`, add the import near the other `from . import ...` lines:

```python
from . import employee_notifications
```

In `_upsert_one`, the UPDATE branch currently ends with:

```python
        if existing["state"] != state:
            cascade_on_state_change(existing, new_row)
```

Change it to:

```python
        if existing["state"] != state:
            cascade_on_state_change(existing, new_row)
            employee_notifications.maybe_notify_resolution(existing, new_row)
```

In the INSERT branch, the existing block is:

```python
        if state == "validate":
            # New HR-entered leave already approved → trigger cascade
            new_rows = db.query(
                "SELECT * FROM time_off_requests WHERE odoo_leave_id = %s",
                (odoo_leave_id,),
            )
            if new_rows:
                cascade_on_state_change({"state": "draft"}, new_rows[0])
```

Change the inner `if new_rows:` body to:

```python
            if new_rows:
                cascade_on_state_change({"state": "draft"}, new_rows[0])
                employee_notifications.maybe_notify_resolution(
                    {"state": "draft"}, new_rows[0])
```

- [ ] **Step 8: Run the wiring tests + full module tests**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_sync.py tests/test_employee_notifications.py -v`
Expected: PASS (all).

- [ ] **Step 9: Commit**

```bash
git add src/zira_dashboard/employee_notifications.py src/zira_dashboard/time_off_sync.py tests/test_employee_notifications.py tests/test_time_off_sync.py
git commit -m "feat(notifications): generate resolution notifications from the time-off poller"
```

---

## Task 4: Day-before reminder module

**Files:**
- Create: `src/zira_dashboard/time_off_reminder.py`
- Test: `tests/test_time_off_reminder.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_time_off_reminder.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from zira_dashboard import time_off_reminder as tor


@pytest.fixture
def fake_db(monkeypatch):
    captured: dict = {"queries": []}

    def fake_query(sql, params=None):
        captured["queries"].append((sql, params))
        return captured.get("query_result", [])

    monkeypatch.setattr(tor.db, "query", fake_query)
    return captured


def test_next_working_day_skips_weekend():
    # Fri 2026-07-03 -> Mon 2026-07-06 (skip Sat/Sun).
    assert tor.next_working_day(date(2026, 7, 3)) == date(2026, 7, 6)


def test_next_working_day_midweek():
    # Mon 2026-06-29 -> Tue 2026-06-30.
    assert tor.next_working_day(date(2026, 6, 29)) == date(2026, 6, 30)


def test_reminder_full_day(fake_db, monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    fake_db["query_result"] = [{
        "shape": "full_day", "date_from": date(2026, 6, 30),
        "date_to": date(2026, 6, 30), "hour_from": None, "hour_to": None,
    }]
    out = tor.reminder_for_person(5, today=date(2026, 6, 29))
    assert out is not None
    assert "tomorrow" in out["body"].lower()
    # Query asks for an approved leave covering the next working day.
    sql, params = fake_db["queries"][0]
    assert "state = 'validate'" in sql
    assert params == (5, date(2026, 6, 30), date(2026, 6, 30))


def test_reminder_partial_midday_gap(fake_db, monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    fake_db["query_result"] = [{
        "shape": "midday_gap", "date_from": date(2026, 6, 30),
        "date_to": date(2026, 6, 30), "hour_from": 11.0, "hour_to": 13.5,
    }]
    out = tor.reminder_for_person(5, today=date(2026, 6, 29))
    assert out is not None
    assert "11:00" in out["body"] and "1:30" in out["body"]


def test_reminder_none_when_no_leave(fake_db, monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    fake_db["query_result"] = []
    assert tor.reminder_for_person(5, today=date(2026, 6, 29)) is None


def test_reminder_none_when_disabled(fake_db, monkeypatch):
    monkeypatch.setenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", "0")
    fake_db["query_result"] = [{
        "shape": "full_day", "date_from": date(2026, 6, 30),
        "date_to": date(2026, 6, 30), "hour_from": None, "hour_to": None,
    }]
    assert tor.reminder_for_person(5, today=date(2026, 6, 29)) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_reminder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'zira_dashboard.time_off_reminder'`.

- [ ] **Step 3: Create the module**

Create `src/zira_dashboard/time_off_reminder.py`:

```python
"""Day-before time-off reminder, computed live at clock-out.

When an employee clocks out on the last working day before approved time
off, the clock-out confirmation shows a "time off tomorrow" card. Nothing
is stored — this is recomputed on each clock-out. Only the real clock-out
endpoint calls this; transfers and auto-lunch sign-outs use other code
paths, so they never trigger it.

"Next working day" uses a simple weekend-skip rule (this is a Mon–Fri
plant). Per-person Odoo working calendars aren't cleanly available without
extra Odoo calls; this covers the plant's schedule and keeps the clock-out
hot path DB/Odoo-cheap.
"""
from __future__ import annotations

import os
from datetime import date, time as _time, timedelta
from typing import Any

from . import db
from .employee_notifications import notifications_enabled


def next_working_day(d: date) -> date:
    """The next Mon–Fri after ``d`` (skips Sat=5 / Sun=6)."""
    nxt = d + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _fmt_hour(h: float | None) -> str:
    """0.0–24.0 float hour -> '9:30 AM'. None -> ''."""
    if h is None:
        return ""
    h = float(h)
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm == 60:
        hh, mm = hh + 1, 0
    t = _time(hh % 24, mm)
    fmt = "%#I:%M %p" if os.name == "nt" else "%-I:%M %p"
    return t.strftime(fmt)


def _day_label(target: date, today: date) -> str:
    wd = target.strftime("%A")
    md = target.strftime("%b %#d") if os.name == "nt" else target.strftime("%b %-d")
    label = f"{wd}, {md}"
    if target == today + timedelta(days=1):
        return f"tomorrow ({label})"
    return label


def _render_reminder(row: dict[str, Any], target: date, today: date) -> dict:
    day = _day_label(target, today)
    shape = row.get("shape")
    if shape == "full_day":
        return {
            "title": "Time off reminder 🌴",
            "body": f"Heads up — you have approved time off {day}. Enjoy!",
        }
    hf = _fmt_hour(row.get("hour_from"))
    ht = _fmt_hour(row.get("hour_to"))
    if shape == "late_arrival":
        detail = f"you're not due in until {ht}" if ht else "you have a late arrival"
    elif shape == "early_leave":
        detail = f"you can leave at {hf}" if hf else "you have an early leave"
    else:  # midday_gap (and any partial we can't classify)
        detail = (f"you're off from {hf} to {ht}"
                  if hf and ht else "you have partial time off")
    return {
        "title": "Time off reminder ⏰",
        "body": f"Heads up — {day}, {detail} (approved).",
    }


def reminder_for_person(person_odoo_id: int, today: date) -> dict | None:
    """Return a reminder card dict ({'title', 'body'}) if this person has
    approved time off (full or partial) on their next working day, else None.
    """
    if not notifications_enabled():
        return None
    target = next_working_day(today)
    rows = db.query(
        "SELECT shape, date_from, date_to, hour_from, hour_to "
        "FROM time_off_requests "
        "WHERE person_odoo_id = %s AND state = 'validate' "
        "AND date_from <= %s AND date_to >= %s "
        "ORDER BY date_from LIMIT 1",
        (person_odoo_id, target, target),
    )
    if not rows:
        return None
    return _render_reminder(rows[0], target, today)
```

- [ ] **Step 4: Run to verify pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_time_off_reminder.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/time_off_reminder.py tests/test_time_off_reminder.py
git commit -m "feat(notifications): live day-before time-off reminder"
```

---

## Task 5: Sign-in interstitial — routes, template, kiosk_start hook

**Files:**
- Create: `src/zira_dashboard/templates/timeclock_notifications.html`
- Modify: `src/zira_dashboard/routes/timeclock.py` (import; `kiosk_start` ~line 427; add two routes after `timeclock_dashboard` ~line 511)
- Test: `tests/test_timeclock_notifications_routes.py`

- [ ] **Step 1: Write the failing route tests**

Create `tests/test_timeclock_notifications_routes.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from zira_dashboard import employee_notifications
from zira_dashboard.app import app
from zira_dashboard.routes import timeclock

client = TestClient(app)

PERSON = {"id": 1, "name": "Test Person", "odoo_id": 5,
          "wage_type": "hourly", "spanish_speaker": False}


def test_start_redirects_to_notifications_when_unacked(monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON)
    monkeypatch.setattr(employee_notifications, "has_unacknowledged",
                        lambda oid: True)

    resp = client.get("/timeclock/start/1", follow_redirects=False)

    assert resp.status_code == 303
    assert "/timeclock/notifications/" in resp.headers["location"]


def test_start_goes_to_dashboard_when_none(monkeypatch):
    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON)
    monkeypatch.setattr(employee_notifications, "has_unacknowledged",
                        lambda oid: False)
    monkeypatch.setattr(timeclock, "_time_off_redirect_if_salaried",
                        lambda p, pid: None)

    resp = client.get("/timeclock/start/1", follow_redirects=False)

    assert resp.status_code == 303
    assert "/timeclock/dashboard/" in resp.headers["location"]


def test_notifications_screen_lists_cards(monkeypatch):
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON)
    monkeypatch.setattr(
        employee_notifications, "list_unacknowledged",
        lambda oid: [
            {"id": 1, "kind": "time_off_approved",
             "title": "Time off approved", "body": "Your time off was approved."},
            {"id": 2, "kind": "time_off_denied",
             "title": "Time off denied", "body": "Your request was denied."},
        ],
    )
    token = timeclock._mint_token(1)

    resp = client.get(f"/timeclock/notifications/{token}")

    assert resp.status_code == 200
    assert "Time off approved" in resp.text
    assert "Your request was denied." in resp.text
    assert f"/timeclock/notifications/ack/{token}" in resp.text


def test_notifications_screen_skips_to_dashboard_when_empty(monkeypatch):
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON)
    monkeypatch.setattr(employee_notifications, "list_unacknowledged",
                        lambda oid: [])
    token = timeclock._mint_token(1)

    resp = client.get(f"/timeclock/notifications/{token}",
                      follow_redirects=False)

    assert resp.status_code == 303
    assert "/timeclock/dashboard/" in resp.headers["location"]


def test_ack_acknowledges_and_redirects(monkeypatch):
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON)
    seen = {}
    monkeypatch.setattr(employee_notifications, "acknowledge_all",
                        lambda oid: seen.setdefault("oid", oid))
    token = timeclock._mint_token(1)

    resp = client.post(f"/timeclock/notifications/ack/{token}",
                       follow_redirects=False)

    assert resp.status_code == 303
    assert seen["oid"] == 5  # the signing-in person's odoo id, not anyone else's
    assert "/timeclock/dashboard/" in resp.headers["location"]


def test_notifications_screen_rejects_bad_token():
    resp = client.get("/timeclock/notifications/not-a-real-token",
                      follow_redirects=False)
    assert resp.status_code == 303
    assert "/timeclock" in resp.headers["location"]
```

- [ ] **Step 2: Run to verify failure**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_timeclock_notifications_routes.py -v`
Expected: FAIL — `test_start_redirects_to_notifications_when_unacked` redirects to `/dashboard/` (hook not added) and the `/notifications/` routes 404.

- [ ] **Step 3: Add the import**

In `src/zira_dashboard/routes/timeclock.py`, with the other `from .. import ...` lines, add:

```python
from .. import employee_notifications, time_off_reminder
```

- [ ] **Step 4: Add the `kiosk_start` hook**

Replace the body of `kiosk_start` (lines ~427–440) so the notification check runs before the salaried bounce (salaried staff get popups too):

```python
@router.get("/timeclock/start/{person_id}")
def kiosk_start(person_id: int):
    """Mint a fresh session token for `person_id` and bounce to the
    dashboard. No PIN check — picking your name from the home list is
    the auth (intentional design, not a Phase-0 shortcut)."""
    p = _person_by_id(person_id)
    if not p:
        return RedirectResponse(url="/timeclock", status_code=303)
    # Resolution popups take priority over everything else, including the
    # salaried time-off bounce — the employee must not be able to tap past
    # an approval/denial/cancellation.
    if (employee_notifications.notifications_enabled()
            and p.get("odoo_id")
            and employee_notifications.has_unacknowledged(p["odoo_id"])):
        token = _mint_token(person_id)
        return RedirectResponse(
            url=f"/timeclock/notifications/{token}", status_code=303)
    salaried = _time_off_redirect_if_salaried(p, person_id)
    if salaried:
        return salaried
    token = _mint_token(person_id)
    return RedirectResponse(
        url=f"/timeclock/dashboard/{token}", status_code=303
    )
```

- [ ] **Step 5: Add the two routes**

In `src/zira_dashboard/routes/timeclock.py`, after `timeclock_dashboard` (ends ~line 511), add:

```python
@router.get("/timeclock/notifications/{token}", response_class=HTMLResponse)
def timeclock_notifications(request: Request, token: str):
    """Interstitial shown at sign-in when the employee has unacknowledged
    resolution popups. A single 'Got it' clears the stack."""
    person_id = _verify_token(token)
    if person_id is None:
        return _expired_redirect(request)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/timeclock", status_code=303)
    notes = employee_notifications.list_unacknowledged(p["odoo_id"])
    if not notes:
        # Raced/empty (acked elsewhere) — continue to the dashboard.
        return RedirectResponse(
            url=f"/timeclock/dashboard/{_mint_token(person_id)}",
            status_code=303)
    return templates.TemplateResponse(
        request,
        "timeclock_notifications.html",
        {
            "person": p,
            "token": _mint_token(person_id),
            "notifications": notes,
            "bilingual": bool(p.get("spanish_speaker")),
        },
    )


@router.post("/timeclock/notifications/ack/{token}", response_class=HTMLResponse)
def timeclock_notifications_ack(request: Request, token: str):
    """Mark all of this person's notifications acknowledged, then continue to
    the dashboard (which itself bounces salaried staff to the time-off flow)."""
    person_id = _verify_token(token)
    if person_id is None:
        return _expired_redirect(request)
    p = _person_by_id(person_id)
    if not p or not p.get("odoo_id"):
        return RedirectResponse(url="/timeclock", status_code=303)
    employee_notifications.acknowledge_all(p["odoo_id"])
    return RedirectResponse(
        url=f"/timeclock/dashboard/{_mint_token(person_id)}", status_code=303)
```

- [ ] **Step 6: Create the template**

Create `src/zira_dashboard/templates/timeclock_notifications.html`. Match the existing kiosk look (`timeclock_success.html` uses `.k-main` and inline styles; the `t()` helper provides bilingual strings):

```html
{% extends "timeclock_base.html" %}
{% block title %}Updates{% endblock %}
{% block content %}
<div class="k-main" style="align-items: center; justify-content: center;">
  <h1 style="font-size: 2.5rem; margin-bottom: 1.5rem;">{{ person.name }}</h1>
  <div style="display: flex; flex-direction: column; gap: 1rem; width: 100%; max-width: 680px;">
    {% for n in notifications %}
      <div style="padding: 1.25rem 1.5rem; background: #fff; border-radius: 12px;
                  box-shadow: 0 1px 4px rgba(0,0,0,.12);
                  border-left: 10px solid
                  {% if n.kind == 'time_off_approved' %}#16a34a
                  {% elif n.kind == 'time_off_denied' %}#dc2626
                  {% else %}#d97706{% endif %};">
        <div style="font-size: 1.6rem; font-weight: 700; color: #0f172a;">{{ n.title }}</div>
        <div style="font-size: 1.3rem; color: #334155; margin-top: .35rem;">{{ n.body }}</div>
      </div>
    {% endfor %}
  </div>
  <form method="post" action="/timeclock/notifications/ack/{{ token }}"
        style="margin-top: 2.5rem;">
    <button type="submit"
      style="font-size: 1.75rem; padding: 1rem 3rem; border: none;
             border-radius: 12px; background: #2563eb; color: #fff;
             font-weight: 700; cursor: pointer;">
      {{ t("Got it") }}
    </button>
  </form>
</div>
{% endblock %}
```

> If `timeclock_base.html` defines a primary-button CSS class, use it instead of the inline button styles above to stay consistent.

- [ ] **Step 7: Run the route tests to verify pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_timeclock_notifications_routes.py -v`
Expected: PASS (all).

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/routes/timeclock.py src/zira_dashboard/templates/timeclock_notifications.html tests/test_timeclock_notifications_routes.py
git commit -m "feat(notifications): sign-in interstitial for time-off resolutions"
```

---

## Task 6: Clock-out reminder wiring + success template

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock.py` (`kiosk_clock_out` ~lines 587–620)
- Modify: `src/zira_dashboard/templates/timeclock_success.html`
- Test: `tests/test_timeclock_notifications_routes.py` (add a clock-out case)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_timeclock_notifications_routes.py`:

```python
def test_clock_out_shows_reminder_card(monkeypatch):
    from datetime import datetime, timezone
    from zira_dashboard import time_off_reminder, timeclock_sync, auto_lunch

    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON)
    monkeypatch.setattr(timeclock, "_time_off_redirect_if_salaried",
                        lambda p, pid: None)
    monkeypatch.setattr(
        timeclock, "_open_log_row",
        lambda *a, **k: (1, datetime(2026, 6, 29, 22, 0, tzinfo=timezone.utc)))
    monkeypatch.setattr(auto_lunch, "note_employee_clock_out", lambda oid: None)
    monkeypatch.setattr(timeclock_sync, "sync_one_by_id", lambda lid: None)
    monkeypatch.setattr(
        time_off_reminder, "reminder_for_person",
        lambda oid, today: {"title": "Time off reminder 🌴",
                            "body": "Heads up — you have approved time off "
                                    "tomorrow (Tuesday, Jun 30). Enjoy!"})
    token = timeclock._mint_token(1)

    resp = client.post(f"/timeclock/clock-out/{token}")

    assert resp.status_code == 200
    assert "approved time off" in resp.text
    # Reminder present -> no 3s auto-redirect script.
    assert "location.href = '/timeclock'" not in resp.text


def test_clock_out_no_reminder_keeps_auto_redirect(monkeypatch):
    from datetime import datetime, timezone
    from zira_dashboard import time_off_reminder, timeclock_sync, auto_lunch

    monkeypatch.delenv("KIOSK_TIME_OFF_NOTIFY_ENABLED", raising=False)
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON)
    monkeypatch.setattr(timeclock, "_time_off_redirect_if_salaried",
                        lambda p, pid: None)
    monkeypatch.setattr(
        timeclock, "_open_log_row",
        lambda *a, **k: (1, datetime(2026, 6, 29, 22, 0, tzinfo=timezone.utc)))
    monkeypatch.setattr(auto_lunch, "note_employee_clock_out", lambda oid: None)
    monkeypatch.setattr(timeclock_sync, "sync_one_by_id", lambda lid: None)
    monkeypatch.setattr(time_off_reminder, "reminder_for_person",
                        lambda oid, today: None)
    token = timeclock._mint_token(1)

    resp = client.post(f"/timeclock/clock-out/{token}")

    assert resp.status_code == 200
    assert "location.href = '/timeclock'" in resp.text
```

- [ ] **Step 2: Run to verify failure**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_timeclock_notifications_routes.py -k clock_out -v`
Expected: FAIL — the reminder text isn't in the response (not wired) and the auto-redirect script is always present.

- [ ] **Step 3: Wire the reminder into `kiosk_clock_out`**

In `src/zira_dashboard/routes/timeclock.py`, the tail of `kiosk_clock_out` currently is:

```python
    background_tasks.add_task(timeclock_sync.sync_one_by_id, log_id)
    return templates.TemplateResponse(
        request,
        "timeclock_success.html",
        {
            "person": p,
            "message": "Clocked out",
            "time": _fmt_time(rounded_at),
            "bilingual": bool(p.get("spanish_speaker")),
        },
    )
```

Replace it with:

```python
    background_tasks.add_task(timeclock_sync.sync_one_by_id, log_id)
    # Day-before reminder: if today is the last working day before approved
    # time off, the success screen shows a "time off tomorrow" card and skips
    # the auto-redirect so they have to tap past it. Never block the clock-out
    # on a reminder lookup failure.
    time_off_reminder_card = None
    if employee_notifications.notifications_enabled():
        try:
            time_off_reminder_card = time_off_reminder.reminder_for_person(
                odoo_id, plant_today())
        except Exception:
            _log.exception("time-off reminder lookup failed for %s", odoo_id)
    return templates.TemplateResponse(
        request,
        "timeclock_success.html",
        {
            "person": p,
            "message": "Clocked out",
            "time": _fmt_time(rounded_at),
            "bilingual": bool(p.get("spanish_speaker")),
            "time_off_reminder": time_off_reminder_card,
        },
    )
```

> `plant_today` and `_log` are already imported/defined in this module. `odoo_id` is the local variable set earlier in `kiosk_clock_out`.

- [ ] **Step 4: Update the success template**

In `src/zira_dashboard/templates/timeclock_success.html`, replace the tail of the file — from the returning-home `<p>` (line 14) through the final `{% endblock %}` (line 21), i.e. the `<p>…Returning home…</p>`, the closing `</div>`, the `<script>` block, **and** the `{% endblock %}` — with the block below. (The `{% if sync_error %}…{% endif %}` block above line 14 stays as-is.)

```html
  {% if time_off_reminder %}
    <div style="margin-top: 2rem; max-width: 680px; padding: 1.25rem 1.5rem;
                background: #fffbeb; border-radius: 12px;
                border-left: 10px solid #d97706; text-align: left;">
      <div style="font-size: 1.5rem; font-weight: 700; color: #92400e;">
        {{ time_off_reminder.title }}</div>
      <div style="font-size: 1.25rem; color: #334155; margin-top: .35rem;">
        {{ time_off_reminder.body }}</div>
    </div>
    <a href="/timeclock"
       style="margin-top: 2rem; font-size: 1.5rem; padding: .85rem 2.5rem;
              border-radius: 12px; background: #2563eb; color: #fff;
              font-weight: 700; text-decoration: none;">{{ t("Got it") }}</a>
  {% else %}
    <p style="font-size: 1rem; color: #64748b; margin-top: 2rem;">
      {{ t("Returning home…") }}
    </p>
  {% endif %}
</div>
{% if not time_off_reminder %}
<script>
  setTimeout(function(){ location.href = '/timeclock'; }, 3000);
</script>
{% endif %}
{% endblock %}
```

> The replacement block ends with its own `{% endblock %}`, so make sure you removed the original one (line 21) — there must be exactly one `{% endblock %}` at the end. `time_off_reminder` is undefined (falsy) on the clock-in and transfer success screens, so their behavior is unchanged.

- [ ] **Step 5: Run the clock-out tests to verify pass**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_timeclock_notifications_routes.py -v`
Expected: PASS (all, including the two clock-out cases).

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/timeclock.py src/zira_dashboard/templates/timeclock_success.html tests/test_timeclock_notifications_routes.py
git commit -m "feat(notifications): day-before reminder on clock-out"
```

---

## Final verification

- [ ] **Run the full new-feature suite**

Run:
```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest \
  tests/test_schema_employee_notifications.py \
  tests/test_employee_notifications.py \
  tests/test_time_off_reminder.py \
  tests/test_timeclock_notifications_routes.py \
  tests/test_time_off_sync.py -v
```
Expected: all PASS.

- [ ] **Run the broader suite to confirm no regressions**

Run: `ZIRA_API_KEY=test .venv/bin/python -m pytest -q`
Expected: no new failures (DB/Odoo-gated tests skip as usual per `conftest.py`).

- [ ] **Manual smoke (optional, on a real DB/Odoo):**
  - Approve a pending request in Odoo → within ~60s a row appears in `employee_notifications`; the employee's next kiosk sign-in shows the approved popup; "Got it" clears it.
  - Deny a pending request → denied popup appears.
  - Employee cancels their own approved request → **no** denied popup.
  - Clock out the working day before approved time off → reminder card on the confirmation screen.

---

## Notes for the implementer

- **No new background job.** Generation rides the existing 60s time-off poller via `_upsert_one`. Nothing to register in `app.py`.
- **Backfill safety.** UPDATE-path notifications only fire on a real observed transition (the local mirror already holds prior state), so a deploy doesn't re-notify existing approved leaves. The future-only guard (`date_to >= today`) is the explicit backstop for both paths.
- **Latency.** Approvals/denials surface within ~1–60s of the Odoo change (poller cadence).
- **Kill-switch.** `KIOSK_TIME_OFF_NOTIFY_ENABLED=0` disables generation, the sign-in interstitial, and the clock-out reminder. Default on.
