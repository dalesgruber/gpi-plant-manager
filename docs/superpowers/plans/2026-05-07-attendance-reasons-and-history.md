# Attendance Reasons + Player-Card History — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Late/absence report covers both scheduled AND unscheduled no-punch people; auto-detects late arrivals and prompts for a reason; player card grows an Attendance section showing per-day Absent / Late history with inline-editable reasons.

**Architecture:** Schema additions are additive (ALTER TABLE adds nullable `reason` column; new `late_arrivals` table). The late-report API expands its response to four sections (`scheduled_late`, `unscheduled_late`, `needs_reason`, `snoozed`) — all derived from existing data. The footer popup gets a third actionable group (reason editor + Save). The player card grows an Attendance table with two new stat tiles and inline `contenteditable` reason cells.

**Tech Stack:** Python 3.12 / FastAPI / Jinja2 / vanilla JS / Postgres (psycopg2). Test runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-05-07-attendance-reasons-and-history-design.md`

---

## File map

| File | Change |
|---|---|
| `src/zira_dashboard/db.py` | Schema DDL: ALTER `manual_absences` ADD `reason`; CREATE `late_arrivals`. |
| `src/zira_dashboard/late_report.py` | `declare_absent` accepts `reason`; new `save_late_arrival`, `late_arrivals_for_day`, `late_arrivals_history_for_name`, `absences_history_for_name`. |
| `src/zira_dashboard/routes/staffing.py` | `_safe_attendance` extends fetch to unscheduled active non-reserve people; `/api/late-report` returns four sections; `/api/late-report/declare-absent` accepts optional `reason`; new `/api/late-report/save-late-arrival`. |
| `src/zira_dashboard/routes/people.py` | Query attendance history; pass `attendance_rows`, `total_absent_days`, `total_late_days` to template. New endpoint `/api/staffing/people/{name}/attendance/reason`. |
| `src/zira_dashboard/templates/_footer.html` | Popup renders four sections; reason editor on Declare-Absent and needs-reason rows; new save handler for late-arrival reason. |
| `src/zira_dashboard/templates/player_card.html` | Two new stat tiles; new Attendance table; inline-edit JS for reason cells. |
| `tests/test_late_report.py` (extend) | Reason column; save_late_arrival upsert; late_arrivals_for_day; history queries. |
| `tests/test_player_card.py` (extend) | Attendance section render + stat tiles. |
| `CHANGELOG.md` | Entry for the deploy. |

---

### Task 1: Schema — `reason` column + `late_arrivals` table

**Files:**
- Modify: `src/zira_dashboard/db.py`

The schema DDL block at the bottom of `db.py` runs on every app boot via `bootstrap_schema()`. Both changes use `IF NOT EXISTS` so re-running is safe.

- [ ] **Step 1: Add the DDL**

Open `src/zira_dashboard/db.py`. Find the `_SCHEMA_DDL` string (around line 136). Inside the triple-quoted block, after the existing `manual_absences` table definition (around line 313), add:

```sql
ALTER TABLE manual_absences ADD COLUMN IF NOT EXISTS reason TEXT;

CREATE TABLE IF NOT EXISTS late_arrivals (
  day            DATE NOT NULL,
  emp_id         TEXT NOT NULL,
  name           TEXT NOT NULL,
  reason         TEXT,
  declared_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, emp_id)
);
CREATE INDEX IF NOT EXISTS late_arrivals_day_idx ON late_arrivals(day);
```

(Place it adjacent to the existing `manual_absences` block so future maintainers see them together.)

- [ ] **Step 2: Verify the SQL parses**

```
.venv/Scripts/python.exe -c "
import zira_dashboard.db as db
ddl = db._SCHEMA_DDL
assert 'CREATE TABLE IF NOT EXISTS late_arrivals' in ddl
assert 'ALTER TABLE manual_absences ADD COLUMN IF NOT EXISTS reason' in ddl
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/db.py
git commit -m "schema: add reason to manual_absences + late_arrivals table

Additive, idempotent. ALTER TABLE … ADD COLUMN IF NOT EXISTS plus
CREATE TABLE IF NOT EXISTS so re-running bootstrap_schema is safe.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `late_report` data-layer additions

**Files:**
- Modify: `src/zira_dashboard/late_report.py`
- Modify: `tests/test_late_report.py`

Adds: optional `reason` arg on `declare_absent`; new `save_late_arrival`, `late_arrivals_for_day`, `absences_history_for_name`, `late_arrivals_history_for_name`.

- [ ] **Step 1: Write failing tests**

If `tests/test_late_report.py` doesn't exist yet, create it. Otherwise append. The DB-backed tests will be skipped automatically without `DATABASE_URL` — that's fine; the function shape is the contract.

```python
# tests/test_late_report.py — append at the end
import os
import pytest

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; this test needs Postgres.",
)


@requires_db
def test_declare_absent_accepts_optional_reason():
    from datetime import date
    from zira_dashboard import db, late_report

    d = date(2026, 5, 7)
    db.execute("DELETE FROM manual_absences WHERE day = %s", (d,))
    late_report.declare_absent(d, "999", "Test Person", reason="sick")
    rows = db.query(
        "SELECT name, reason FROM manual_absences WHERE day = %s AND emp_id = %s",
        (d, "999"),
    )
    assert rows == [{"name": "Test Person", "reason": "sick"}]
    db.execute("DELETE FROM manual_absences WHERE day = %s AND emp_id = %s", (d, "999"))


@requires_db
def test_declare_absent_reason_defaults_to_none():
    from datetime import date
    from zira_dashboard import db, late_report

    d = date(2026, 5, 7)
    db.execute("DELETE FROM manual_absences WHERE day = %s", (d,))
    late_report.declare_absent(d, "998", "No-Reason Person")
    rows = db.query(
        "SELECT reason FROM manual_absences WHERE day = %s AND emp_id = %s",
        (d, "998"),
    )
    assert rows == [{"reason": None}]
    db.execute("DELETE FROM manual_absences WHERE day = %s AND emp_id = %s", (d, "998"))


@requires_db
def test_save_late_arrival_upserts():
    from datetime import date
    from zira_dashboard import db, late_report

    d = date(2026, 5, 7)
    db.execute("DELETE FROM late_arrivals WHERE day = %s AND emp_id = %s", (d, "777"))
    late_report.save_late_arrival(d, "777", "Late Person", reason="car issues")
    rows = db.query(
        "SELECT name, reason FROM late_arrivals WHERE day = %s AND emp_id = %s",
        (d, "777"),
    )
    assert rows == [{"name": "Late Person", "reason": "car issues"}]
    # Second call updates.
    late_report.save_late_arrival(d, "777", "Late Person", reason="overslept")
    rows = db.query(
        "SELECT reason FROM late_arrivals WHERE day = %s AND emp_id = %s",
        (d, "777"),
    )
    assert rows == [{"reason": "overslept"}]
    db.execute("DELETE FROM late_arrivals WHERE day = %s AND emp_id = %s", (d, "777"))


@requires_db
def test_late_arrivals_for_day_returns_emp_id_set():
    from datetime import date
    from zira_dashboard import db, late_report

    d = date(2026, 5, 7)
    db.execute("DELETE FROM late_arrivals WHERE day = %s", (d,))
    late_report.save_late_arrival(d, "100", "A", reason=None)
    late_report.save_late_arrival(d, "200", "B", reason=None)
    out = late_report.late_arrivals_for_day(d)
    assert out == {"100", "200"}
    db.execute("DELETE FROM late_arrivals WHERE day = %s", (d,))


@requires_db
def test_history_for_name_returns_absent_and_late_rows():
    from datetime import date
    from zira_dashboard import db, late_report

    d1 = date(2026, 5, 5)
    d2 = date(2026, 5, 6)
    name = "Test History"
    db.execute("DELETE FROM manual_absences WHERE name = %s", (name,))
    db.execute("DELETE FROM late_arrivals WHERE name = %s", (name,))
    late_report.declare_absent(d1, "555", name, reason="sick")
    late_report.save_late_arrival(d2, "555", name, reason="overslept")

    abs_rows = late_report.absences_history_for_name(name, d1, d2)
    late_rows = late_report.late_arrivals_history_for_name(name, d1, d2)
    assert abs_rows == [{"day": d1, "reason": "sick"}]
    assert late_rows == [{"day": d2, "reason": "overslept"}]

    db.execute("DELETE FROM manual_absences WHERE name = %s", (name,))
    db.execute("DELETE FROM late_arrivals WHERE name = %s", (name,))
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_late_report.py -v -k "reason or save_late or late_arrivals_for_day or history_for_name"
```

Without `DATABASE_URL`: tests SKIP (which is fine — the contract test below will catch shape errors at import time). With `DATABASE_URL`: tests FAIL (functions don't exist).

- [ ] **Step 3: Implement the new functions**

Open `src/zira_dashboard/late_report.py`. Replace the existing `declare_absent` body and append the new helpers:

```python
def declare_absent(day, emp_id: str, name: str, reason: str | None = None) -> None:
    db.execute(
        """
        INSERT INTO manual_absences (day, emp_id, name, reason)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (day, emp_id) DO UPDATE SET
          name = EXCLUDED.name,
          reason = EXCLUDED.reason
        """,
        (day, str(emp_id), name, reason),
    )


def save_late_arrival(day, emp_id: str, name: str, reason: str | None = None) -> None:
    """Record a late-arrival event for `day` + `emp_id`. Idempotent — a
    second save with a different reason overwrites the first."""
    db.execute(
        """
        INSERT INTO late_arrivals (day, emp_id, name, reason)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (day, emp_id) DO UPDATE SET
          name = EXCLUDED.name,
          reason = EXCLUDED.reason
        """,
        (day, str(emp_id), name, reason),
    )


def late_arrivals_for_day(day) -> set[str]:
    """Set of emp_ids that already have a late-arrival record for `day`.
    Used by /api/late-report to suppress 'needs reason' rows once
    they've been handled."""
    rows = db.query(
        "SELECT emp_id FROM late_arrivals WHERE day = %s",
        (day,),
    )
    return {r["emp_id"] for r in rows}


def absences_history_for_name(name: str, start_d, end_d) -> list[dict]:
    """Per-day absence history for `name` within [start_d, end_d].
    Newest first. Each row: {day, reason}."""
    rows = db.query(
        """
        SELECT day, reason
        FROM manual_absences
        WHERE name = %s AND day BETWEEN %s AND %s
        ORDER BY day DESC
        """,
        (name, start_d, end_d),
    )
    return [{"day": r["day"], "reason": r["reason"]} for r in rows]


def late_arrivals_history_for_name(name: str, start_d, end_d) -> list[dict]:
    """Per-day late-arrival history for `name` within [start_d, end_d].
    Newest first. Each row: {day, reason}."""
    rows = db.query(
        """
        SELECT day, reason
        FROM late_arrivals
        WHERE name = %s AND day BETWEEN %s AND %s
        ORDER BY day DESC
        """,
        (name, start_d, end_d),
    )
    return [{"day": r["day"], "reason": r["reason"]} for r in rows]
```

The existing `declare_absent` had a 3-arg signature; the new one's `reason` defaults to `None` so all current call sites stay valid.

- [ ] **Step 4: Verify the function shapes import**

```
.venv/Scripts/python.exe -c "
from zira_dashboard.late_report import (
    declare_absent, save_late_arrival, late_arrivals_for_day,
    absences_history_for_name, late_arrivals_history_for_name,
)
import inspect
sig = inspect.signature(declare_absent)
assert 'reason' in sig.parameters
assert sig.parameters['reason'].default is None
sig = inspect.signature(save_late_arrival)
assert 'reason' in sig.parameters
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Run the tests (DB or skip)**

```
.venv/Scripts/python.exe -m pytest tests/test_late_report.py -v
```

Without `DATABASE_URL`: tests SKIP. With it: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/late_report.py tests/test_late_report.py
git commit -m "feat(late_report): reason on declare_absent + save_late_arrival + history

declare_absent gains an optional reason kwarg (defaults to None for
back-compat with existing call sites). New save_late_arrival mirrors
the same upsert pattern against the late_arrivals table.

Two history helpers — absences_history_for_name and
late_arrivals_history_for_name — feed the upcoming player-card
Attendance section. late_arrivals_for_day returns the set of
emp_ids already recorded so the /api/late-report 'needs reason'
list can suppress them.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Extend `_safe_attendance` to also fetch unscheduled people

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`

Currently `_safe_attendance` only calls `attendance_for_day(d, scheduled_ids)`. To detect both unscheduled-late and needs-reason, we need attendance for active non-reserve unscheduled people too. The fetch is bounded by the roster size (~30 names) and runs once per /api/late-report poll.

- [ ] **Step 1: Read the current `_safe_attendance`**

It's around `routes/staffing.py:53-95`. Note the existing structure: builds `scheduled_names`, maps to ids via `name_to_emp_id_map`, calls `attendance_for_day(d, scheduled_ids)`, returns `{by_name, by_id, name_to_id, scheduled_ids}`.

- [ ] **Step 2: Add `unscheduled_ids` to the attendance fetch**

Replace the `_safe_attendance` body. The change: build the union of scheduled ids + unscheduled active-non-reserve ids; fetch attendance for the union; preserve `scheduled_ids` in the return shape and add `unscheduled_ids`. Existing callers ignore the new key.

```python
def _safe_attendance(d, sched, today):
    """Wrap StratusTime attendance lookup. Returns
    {by_name, by_id, name_to_id, scheduled_ids, unscheduled_ids}.

    Returns empty dicts on any error or when attendance isn't applicable
    (not today, or before shift start). by_name keys are roster names;
    by_id keys are StratusTime EmpIdentifiers (used by late_report).

    Fetches attendance for both scheduled people AND active non-reserve
    people who weren't assigned to a WC today — so the Late/Absence
    Report can flag both groups.
    """
    empty = {
        "by_name": {}, "by_id": {}, "name_to_id": {},
        "scheduled_ids": [], "unscheduled_ids": [],
    }
    if d != today:
        return empty
    try:
        now_local = datetime.now(timezone.utc).astimezone(shift_config.SITE_TZ)
        shift_start_local = datetime.combine(
            d, shift_config.shift_start_for(d), tzinfo=shift_config.SITE_TZ
        )
        if now_local < shift_start_local:
            return empty
        name_to_id = stratustime_client.name_to_emp_id_map()
        scheduled_names: set[str] = set()
        for ops in sched.assignments.values():
            for n in (ops or []):
                if n:
                    scheduled_names.add(n)
        scheduled_ids = [name_to_id[n] for n in scheduled_names if n in name_to_id]

        # Unscheduled = active non-reserve people not in scheduled_names
        # (matches the /staffing left-rail "Unscheduled" definition).
        roster = staffing.load_roster()
        unscheduled_names = [
            p.name for p in roster
            if p.active and not p.reserve and p.name not in scheduled_names
        ]
        unscheduled_ids = [name_to_id[n] for n in unscheduled_names if n in name_to_id]

        all_ids = list({*scheduled_ids, *unscheduled_ids})
        id_to_name = {v: k for k, v in name_to_id.items()}
        attendance_by_id = stratustime_client.attendance_for_day(d, all_ids)
        by_name: dict[str, dict] = {}
        for emp_id, info in attendance_by_id.items():
            name = id_to_name.get(emp_id)
            if name:
                by_name[name] = info
        return {
            "by_name": by_name,
            "by_id": attendance_by_id,
            "name_to_id": name_to_id,
            "scheduled_ids": scheduled_ids,
            "unscheduled_ids": unscheduled_ids,
        }
    except Exception:
        return empty
```

- [ ] **Step 3: Smoke-test the import**

```
.venv/Scripts/python.exe -c "from zira_dashboard.routes import staffing; print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "refactor(staffing): _safe_attendance also fetches unscheduled active people

Adds unscheduled_ids alongside scheduled_ids and merges the two id
lists into a single attendance_for_day call. Existing callers see no
breaking change — the return dict gains a key but loses none.

Sets up the next task: /api/late-report can now report on both
groups in one pass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `late_report.late_people_for_day` — three sections

**Files:**
- Modify: `src/zira_dashboard/late_report.py`
- Modify: `tests/test_late_report.py`

Replace the existing `late_people_for_day` with one that returns a structured dict (`scheduled_late`, `unscheduled_late`, `needs_reason`). Keep a thin back-compat wrapper that returns just the old `late` list shape so any straggler call site doesn't break.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_late_report.py`:

```python
def test_late_people_for_day_three_sections():
    """The expanded helper returns dict with scheduled_late, unscheduled_late,
    needs_reason — derived from the same attendance dict."""
    from datetime import date, datetime, timezone
    from zira_dashboard import shift_config, late_report

    d = date(2026, 5, 7)
    # 12:00 UTC = 7:00 AM local for CDT (UTC-5).
    shift_start_local = datetime(2026, 5, 7, 7, 0, tzinfo=shift_config.SITE_TZ)
    now_local = datetime(2026, 5, 7, 9, 0, tzinfo=shift_config.SITE_TZ)  # 2h past start

    attendance = {
        # Scheduled, no_punch — counts as scheduled_late
        "111": {"status": "no_punch", "minutes_late": 0},
        # Scheduled, late punch — counts as needs_reason
        "222": {"status": "late", "minutes_late": 31},
        # Scheduled, on time — appears nowhere
        "333": {"status": "on_time", "minutes_late": 0},
        # Unscheduled, no_punch — counts as unscheduled_late
        "444": {"status": "no_punch", "minutes_late": 0},
        # Unscheduled, late — counts as needs_reason
        "555": {"status": "late", "minutes_late": 18},
    }
    out = late_report.late_people_for_day_v2(
        day=d,
        scheduled_emp_ids=["111", "222", "333"],
        unscheduled_emp_ids=["444", "555"],
        attendance=attendance,
        now_local=now_local,
        shift_start_local=shift_start_local,
        absent_ids=set(),
        snoozed_ids=set(),
        already_recorded_late_ids=set(),
    )

    assert {r["emp_id"] for r in out["scheduled_late"]} == {"111"}
    assert {r["emp_id"] for r in out["unscheduled_late"]} == {"444"}
    assert {r["emp_id"] for r in out["needs_reason"]} == {"222", "555"}


def test_late_people_for_day_v2_suppresses_already_recorded():
    """Once a late_arrivals row exists for an emp_id, that emp_id no
    longer appears in needs_reason."""
    from datetime import date, datetime
    from zira_dashboard import shift_config, late_report

    d = date(2026, 5, 7)
    shift_start_local = datetime(2026, 5, 7, 7, 0, tzinfo=shift_config.SITE_TZ)
    now_local = datetime(2026, 5, 7, 9, 0, tzinfo=shift_config.SITE_TZ)

    attendance = {"222": {"status": "late", "minutes_late": 31}}
    out = late_report.late_people_for_day_v2(
        day=d,
        scheduled_emp_ids=["222"],
        unscheduled_emp_ids=[],
        attendance=attendance,
        now_local=now_local,
        shift_start_local=shift_start_local,
        absent_ids=set(),
        snoozed_ids=set(),
        already_recorded_late_ids={"222"},
    )
    assert out["needs_reason"] == []


def test_late_people_for_day_v2_skips_absent_and_snoozed():
    from datetime import date, datetime
    from zira_dashboard import shift_config, late_report

    d = date(2026, 5, 7)
    shift_start_local = datetime(2026, 5, 7, 7, 0, tzinfo=shift_config.SITE_TZ)
    now_local = datetime(2026, 5, 7, 9, 0, tzinfo=shift_config.SITE_TZ)

    attendance = {
        "111": {"status": "no_punch", "minutes_late": 0},
        "222": {"status": "no_punch", "minutes_late": 0},
    }
    out = late_report.late_people_for_day_v2(
        day=d,
        scheduled_emp_ids=["111", "222"],
        unscheduled_emp_ids=[],
        attendance=attendance,
        now_local=now_local,
        shift_start_local=shift_start_local,
        absent_ids={"111"},
        snoozed_ids={"222"},
        already_recorded_late_ids=set(),
    )
    assert out["scheduled_late"] == []
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_late_report.py -v -k late_people_for_day_v2
```

Expected: 3 FAIL with `AttributeError: module ... has no attribute 'late_people_for_day_v2'`.

- [ ] **Step 3: Add the new function**

In `src/zira_dashboard/late_report.py`, add `late_people_for_day_v2` (leaving the existing `late_people_for_day` intact for back-compat):

```python
def late_people_for_day_v2(
    day,
    scheduled_emp_ids: Iterable[str],
    unscheduled_emp_ids: Iterable[str],
    attendance: dict,
    now_local: datetime,
    shift_start_local: datetime,
    absent_ids: set[str],
    snoozed_ids: set[str],
    already_recorded_late_ids: set[str],
    threshold_minutes: int = LATE_THRESHOLD_MINUTES,
) -> dict:
    """Three-section structured output for /api/late-report.

    Returns:
      {
        "scheduled_late":   [{emp_id, minutes_late}, ...],
        "unscheduled_late": [{emp_id}, ...],
        "needs_reason":     [{emp_id, minutes_late}, ...],
      }

    Args mirror late_people_for_day plus:
      - unscheduled_emp_ids: active non-reserve people not on today's
        schedule. They join scheduled_emp_ids in the no_punch check.
      - already_recorded_late_ids: emp_ids that already have a row in
        late_arrivals for `day`. Suppresses needs_reason entries once
        a manager has captured the reason.

    Pure: no DB calls, no cache lookups. Caller passes everything in.
    """
    scheduled = {str(e) for e in scheduled_emp_ids}
    unscheduled = {str(e) for e in unscheduled_emp_ids}
    if now_local <= shift_start_local + timedelta(minutes=threshold_minutes):
        return {"scheduled_late": [], "unscheduled_late": [], "needs_reason": []}

    minutes_past_start = int((now_local - shift_start_local).total_seconds() // 60)

    scheduled_late: list[dict] = []
    unscheduled_late: list[dict] = []
    needs_reason: list[dict] = []

    for emp_id, info in attendance.items():
        if emp_id in absent_ids or emp_id in snoozed_ids:
            continue
        status = info.get("status")
        if status == "no_punch":
            if emp_id in scheduled:
                scheduled_late.append({
                    "emp_id": emp_id,
                    "minutes_late": minutes_past_start,
                })
            elif emp_id in unscheduled:
                unscheduled_late.append({"emp_id": emp_id})
        elif status == "late":
            if emp_id in already_recorded_late_ids:
                continue
            if emp_id in scheduled or emp_id in unscheduled:
                needs_reason.append({
                    "emp_id": emp_id,
                    "minutes_late": int(info.get("minutes_late") or 0),
                })

    return {
        "scheduled_late": scheduled_late,
        "unscheduled_late": unscheduled_late,
        "needs_reason": needs_reason,
    }
```

- [ ] **Step 4: Run the tests**

```
.venv/Scripts/python.exe -m pytest tests/test_late_report.py -v -k late_people_for_day_v2
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/late_report.py tests/test_late_report.py
git commit -m "feat(late_report): late_people_for_day_v2 returns three sections

Pure helper (no DB / no clock). Returns scheduled_late,
unscheduled_late, needs_reason from a unified attendance dict and
explicit absent / snoozed / already-recorded sets passed in by the
caller. Existing late_people_for_day stays untouched for back-compat
during the rollout.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `/api/late-report` — three sections

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`

Wire `late_people_for_day_v2` into the route. Keep the existing `late` field as an alias for `scheduled_late` so any cached client JS doesn't break.

- [ ] **Step 1: Replace the route handler body**

Locate `late_report_json` in `routes/staffing.py` (around line 840). Replace its body:

```python
@router.get("/api/late-report")
def late_report_json():
    """JSON snapshot for the global Late/Absence Report badge + modal.

    Always for today. Returns four sections:
      scheduled_late:   scheduled people who haven't punched in past threshold
      unscheduled_late: active non-reserve people not assigned today + no_punch
      needs_reason:     people who punched in past threshold + no late_arrivals
                        record yet — manager fills in reason and saves
      snoozed:          silenced rows (no reason field; transient)

    `late` is an alias for `scheduled_late` for legacy clients.
    `count` is the badge number = sum of the three actionable sections.

    Cached in-process for 30 s. Polled by every page footer every 60 s.
    """
    from .. import late_report
    now_ts = time.time()
    cached = _LATE_REPORT_CACHE.get("value")
    if cached is not None and now_ts < _LATE_REPORT_CACHE.get("expires_at", 0):
        return JSONResponse(cached)

    today = datetime.now(timezone.utc).date()
    out: dict = {
        "count": 0,
        "today": today.isoformat(),
        "scheduled_late": [],
        "unscheduled_late": [],
        "needs_reason": [],
        "late": [],  # alias for scheduled_late
        "snoozed": [],
    }
    try:
        sched = staffing.load_schedule(today)
        attendance_pkg = _safe_attendance(today, sched, today)
        by_id = attendance_pkg.get("by_id") or {}
        if by_id:
            now_local = datetime.now(timezone.utc).astimezone(shift_config.SITE_TZ)
            shift_start_local = datetime.combine(
                today, shift_config.shift_start_for(today), tzinfo=shift_config.SITE_TZ
            )
            absent_ids = late_report.absent_emp_ids_for_day(today)
            snoozed_ids = {s["emp_id"] for s in late_report.active_snoozes(today)}
            already_recorded_late_ids = late_report.late_arrivals_for_day(today)

            sections = late_report.late_people_for_day_v2(
                day=today,
                scheduled_emp_ids=attendance_pkg.get("scheduled_ids") or [],
                unscheduled_emp_ids=attendance_pkg.get("unscheduled_ids") or [],
                attendance=by_id,
                now_local=now_local,
                shift_start_local=shift_start_local,
                absent_ids=absent_ids,
                snoozed_ids=snoozed_ids,
                already_recorded_late_ids=already_recorded_late_ids,
            )

            id_to_name = {v: k for k, v in (attendance_pkg.get("name_to_id") or {}).items()}
            full_map = stratustime_client._employee_id_to_name_map()

            def _resolve(emp_id):
                return id_to_name.get(emp_id) or full_map.get(emp_id) or f"Unknown ({emp_id})"

            for r in sections["scheduled_late"]:
                out["scheduled_late"].append({
                    "emp_id": r["emp_id"],
                    "name": _resolve(r["emp_id"]),
                    "minutes_late": r["minutes_late"],
                })
            for r in sections["unscheduled_late"]:
                out["unscheduled_late"].append({
                    "emp_id": r["emp_id"],
                    "name": _resolve(r["emp_id"]),
                })
            for r in sections["needs_reason"]:
                out["needs_reason"].append({
                    "emp_id": r["emp_id"],
                    "name": _resolve(r["emp_id"]),
                    "minutes_late": r["minutes_late"],
                })
            out["late"] = list(out["scheduled_late"])  # legacy alias

        # Snoozed list (independent of attendance).
        now_utc = datetime.now(timezone.utc)
        for s in late_report.active_snoozes(today):
            until = s["until_utc"]
            mins_remaining = max(0, int((until - now_utc).total_seconds() // 60))
            out["snoozed"].append({
                "emp_id": s["emp_id"],
                "name": s["name"],
                "until_iso": until.isoformat(),
                "mins_remaining": mins_remaining,
            })
        out["count"] = (
            len(out["scheduled_late"])
            + len(out["unscheduled_late"])
            + len(out["needs_reason"])
        )
    except Exception:
        pass
    _LATE_REPORT_CACHE["value"] = out
    _LATE_REPORT_CACHE["expires_at"] = now_ts + 30.0
    return JSONResponse(out)
```

- [ ] **Step 2: Smoke-test the import**

```
.venv/Scripts/python.exe -c "from zira_dashboard.routes import staffing; print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "feat(api): /api/late-report returns three actionable sections

scheduled_late + unscheduled_late + needs_reason, plus the existing
snoozed. count is the sum of the three actionable sections (drives
the nav-bar badge). late stays as an alias for scheduled_late so any
in-flight client JS doesn't break mid-deploy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: `/api/late-report/declare-absent` — optional reason

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`

- [ ] **Step 1: Update the route**

Find `late_report_declare_absent` (around line 922). Update the body:

```python
@router.post("/api/late-report/declare-absent")
async def late_report_declare_absent(request: Request):
    """Mark a person as Absent for today.

    Body (JSON): {emp_id, name, reason?}

    Reason is optional. Side effects: writes to manual_absences (with
    reason); clears any pending snooze; busts caches.
    """
    from .. import late_report
    body = await request.json()
    emp_id = str(body.get("emp_id") or "").strip()
    name = str(body.get("name") or "").strip()
    reason_raw = body.get("reason")
    reason = (str(reason_raw).strip() or None) if reason_raw is not None else None
    if not emp_id or not name:
        return JSONResponse({"ok": False, "error": "emp_id and name required"}, status_code=400)
    today = datetime.now(timezone.utc).date()
    try:
        late_report.declare_absent(today, emp_id, name, reason=reason)
        from .. import db as _db
        _db.execute(
            "DELETE FROM late_snoozes WHERE day = %s AND emp_id = %s",
            (today, emp_id),
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    _bust_after_mutation()
    return JSONResponse({"ok": True})
```

- [ ] **Step 2: Smoke-test**

```
.venv/Scripts/python.exe -c "from zira_dashboard.routes import staffing; print('OK')"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "feat(api): /api/late-report/declare-absent accepts optional reason

Body now: {emp_id, name, reason?}. Reason is forwarded to
late_report.declare_absent which UPSERTs into manual_absences with
the new reason column. Existing clients passing only {emp_id, name}
keep working.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: `/api/late-report/save-late-arrival` — new route

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`

- [ ] **Step 1: Add the new route**

Open `routes/staffing.py` and add (right after `late_report_declare_absent`):

```python
@router.post("/api/late-report/save-late-arrival")
async def late_report_save_late_arrival(request: Request):
    """Record a late-arrival event for today.

    Body (JSON): {emp_id, name, reason?}
    Side effects: writes to late_arrivals; busts the report cache so
    the row drops out of needs_reason on the next poll.
    """
    from .. import late_report
    body = await request.json()
    emp_id = str(body.get("emp_id") or "").strip()
    name = str(body.get("name") or "").strip()
    reason_raw = body.get("reason")
    reason = (str(reason_raw).strip() or None) if reason_raw is not None else None
    if not emp_id or not name:
        return JSONResponse({"ok": False, "error": "emp_id and name required"}, status_code=400)
    today = datetime.now(timezone.utc).date()
    try:
        late_report.save_late_arrival(today, emp_id, name, reason=reason)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    _bust_after_mutation()
    return JSONResponse({"ok": True})
```

- [ ] **Step 2: Smoke-test**

```
.venv/Scripts/python.exe -c "from zira_dashboard.app import app; print([r.path for r in app.routes if 'late-report' in r.path])"
```

Expected: list includes `/api/late-report/save-late-arrival`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "feat(api): /api/late-report/save-late-arrival route

Idempotent UPSERT into late_arrivals. Once recorded, the same
emp_id stops appearing in /api/late-report's needs_reason section
on the next poll.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Player card — Attendance section data

**Files:**
- Modify: `src/zira_dashboard/routes/people.py`
- Modify: `tests/test_player_card.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_player_card.py`:

```python
def test_player_card_renders_attendance_section_with_reasons():
    """The player card shows an Attendance section with absent/late
    rows and reasons when history exists in the range."""
    from datetime import date
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    abs_rows = [{"day": date(2026, 5, 6), "reason": "sick"}]
    late_rows = [{"day": date(2026, 5, 7), "reason": "car issues"}]

    with patch("zira_dashboard.production_history.attribution_per_day", return_value=[]), \
         patch("zira_dashboard.production_history.attribution_range", return_value={}), \
         patch("zira_dashboard.staffing.load_roster", return_value=[]), \
         patch("zira_dashboard.late_report.absences_history_for_name", return_value=abs_rows), \
         patch("zira_dashboard.late_report.late_arrivals_history_for_name", return_value=late_rows):
        client = TestClient(app)
        html = client.get("/staffing/people/Carlos?start=2026-05-01&end=2026-05-07").text

    assert "Attendance" in html
    assert "Days Absent" in html
    assert "Days Late" in html
    assert "sick" in html
    assert "car issues" in html
    # Date hyperlinks point to the recycling day-view.
    assert 'href="/recycling?start=2026-05-06&end=2026-05-06"' in html
    assert 'href="/recycling?start=2026-05-07&end=2026-05-07"' in html


def test_player_card_attendance_section_hidden_when_empty():
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    with patch("zira_dashboard.production_history.attribution_per_day", return_value=[]), \
         patch("zira_dashboard.production_history.attribution_range", return_value={}), \
         patch("zira_dashboard.staffing.load_roster", return_value=[]), \
         patch("zira_dashboard.late_report.absences_history_for_name", return_value=[]), \
         patch("zira_dashboard.late_report.late_arrivals_history_for_name", return_value=[]):
        client = TestClient(app)
        html = client.get("/staffing/people/Carlos?start=2026-05-01&end=2026-05-07").text

    # No section header.
    assert ">Attendance<" not in html
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_player_card.py -v
```

Expected: both new tests FAIL.

- [ ] **Step 3: Update the route**

In `src/zira_dashboard/routes/people.py`, extend `staffing_player_card` (after the existing `day_rows` build, before the `return templates.TemplateResponse(...)`):

```python
    # Attendance history — absences + late arrivals in the range.
    from .. import late_report
    abs_rows = late_report.absences_history_for_name(name, start_d, end_d)
    late_rows = late_report.late_arrivals_history_for_name(name, start_d, end_d)
    attendance_rows = (
        [{"date": r["day"].isoformat(), "type": "Absent", "reason": r["reason"] or ""}
         for r in abs_rows]
        + [{"date": r["day"].isoformat(), "type": "Late", "reason": r["reason"] or ""}
           for r in late_rows]
    )
    attendance_rows.sort(key=lambda r: (r["date"], r["type"]), reverse=True)
    total_absent_days = len(abs_rows)
    total_late_days = len(late_rows)
```

Add to the template context dict:

```python
"attendance_rows": attendance_rows,
"total_absent_days": total_absent_days,
"total_late_days": total_late_days,
```

- [ ] **Step 4: Run the tests (still fail — template not updated)**

```
.venv/Scripts/python.exe -m pytest tests/test_player_card.py -v
```

Expected: still FAIL — the route now passes the data but the template doesn't render it. Task 9 fixes this.

- [ ] **Step 5: Commit (route only)**

```bash
git add src/zira_dashboard/routes/people.py tests/test_player_card.py
git commit -m "feat(player_card): collect attendance history rows in route

Queries manual_absences and late_arrivals for the player's name in
the requested range, builds a flat list of {date, type, reason}
rows sorted newest-first, and passes attendance_rows /
total_absent_days / total_late_days to the template. The
render-side change is in the next commit; the new tests stay
failing until then.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Player card — Attendance template + stat tiles + inline edit JS

**Files:**
- Modify: `src/zira_dashboard/templates/player_card.html`
- Modify: `src/zira_dashboard/routes/people.py` (new endpoint for inline edit)

- [ ] **Step 1: Add two stat tiles to `.pc-totals`**

In `templates/player_card.html`, find the existing `.pc-totals` block (around line 50-54). Add the two new tiles inside the same div:

```jinja
<div class="pc-totals">
  <div class="stat"><div class="lab">Days worked</div><div class="v">{{ total_days }}</div></div>
  <div class="stat"><div class="lab">Total units (split)</div><div class="v">{{ '{:,.0f}'.format(total_units) }}</div></div>
  <div class="stat"><div class="lab">Total downtime (min)</div><div class="v">{{ '{:,.0f}'.format(total_downtime) }}</div></div>
  <div class="stat"><div class="lab">Days Absent</div><div class="v">{{ total_absent_days }}</div></div>
  <div class="stat"><div class="lab">Days Late</div><div class="v">{{ total_late_days }}</div></div>
</div>
```

- [ ] **Step 2: Add the Attendance table**

Append to `templates/player_card.html` after the existing `{% if day_rows %}…{% endif %}` block (and before the closing `{% endblock %}`):

```jinja
{% if attendance_rows %}
<h3 style="margin-top:1rem">Attendance</h3>
<table class="pc">
  <thead>
    <tr><th>Date</th><th>Type</th><th>Reason</th></tr>
  </thead>
  <tbody>
    {% for r in attendance_rows %}
    <tr data-attendance-name="{{ name }}"
        data-attendance-date="{{ r.date }}"
        data-attendance-type="{{ r.type|lower }}">
      <td><a href="/recycling?start={{ r.date }}&end={{ r.date }}">{{ r.date }}</a></td>
      <td>{{ r.type }}</td>
      <td class="attendance-reason"
          contenteditable="true"
          data-original="{{ r.reason }}"
          style="cursor:text;border:1px dashed transparent;padding:0.2rem 0.4rem;border-radius:4px"
          onfocus="this.style.borderColor='var(--border)'"
          onblur="window._saveAttendanceReason && window._saveAttendanceReason(this)"
        >{{ r.reason }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
<script>
window._saveAttendanceReason = function (cell) {
  const original = cell.dataset.original || '';
  const next = (cell.textContent || '').trim();
  cell.style.borderColor = 'transparent';
  if (next === original) return;
  const row = cell.closest('tr');
  if (!row) return;
  const name = row.dataset.attendanceName;
  const date = row.dataset.attendanceDate;
  const type = row.dataset.attendanceType;
  fetch('/api/staffing/people/' + encodeURIComponent(name) + '/attendance/reason', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({date: date, type: type, reason: next}),
  }).then(function (r) {
    if (r.ok) {
      cell.dataset.original = next;
    } else {
      cell.textContent = original;
      cell.style.borderColor = 'var(--bad)';
      setTimeout(function () { cell.style.borderColor = 'transparent'; }, 1500);
    }
  }).catch(function () {
    cell.textContent = original;
  });
};
</script>
{% endif %}
```

- [ ] **Step 3: Add the inline-edit endpoint**

In `src/zira_dashboard/routes/people.py`, append:

```python
@router.post("/api/staffing/people/{name}/attendance/reason")
async def update_attendance_reason(name: str, request: Request):
    """Inline-edit endpoint for the Attendance section's Reason cells.

    Body (JSON): {date: YYYY-MM-DD, type: "absent"|"late", reason: str}
    Updates the matching row in manual_absences or late_arrivals.
    """
    from .. import db
    body = await request.json()
    try:
        d = date.fromisoformat(str(body.get("date") or ""))
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad date"}, status_code=400)
    type_ = str(body.get("type") or "").strip().lower()
    if type_ not in ("absent", "late"):
        return JSONResponse({"ok": False, "error": "type must be absent or late"}, status_code=400)
    reason_raw = body.get("reason")
    reason = (str(reason_raw).strip() or None) if reason_raw is not None else None
    table = "manual_absences" if type_ == "absent" else "late_arrivals"
    db.execute(
        f"UPDATE {table} SET reason = %s WHERE day = %s AND name = %s",
        (reason, d, name),
    )
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Run the player-card tests**

```
.venv/Scripts/python.exe -m pytest tests/test_player_card.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/player_card.html src/zira_dashboard/routes/people.py
git commit -m "feat(player_card): Attendance table + 2 stat tiles + inline-edit reasons

Two new tiles in .pc-totals (Days Absent, Days Late). New table
below the per-day breakdown when attendance_rows is non-empty.
Reason cells use contenteditable + blur-saves to a new endpoint
/api/staffing/people/{name}/attendance/reason that UPDATEs
manual_absences or late_arrivals depending on the type. On save
failure the cell rolls back to its original value.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Footer popup — three sections + reason editor

**Files:**
- Modify: `src/zira_dashboard/templates/_footer.html`

The current popup renders just `late` + `snoozed`. We need to render `scheduled_late` (with reason editor on Declare Absent), `unscheduled_late` (same actions as scheduled), `needs_reason` (reason editor + Save), and `snoozed`.

- [ ] **Step 1: Replace `renderModal`**

In `_footer.html`, find the existing `function renderModal(d) { ... }` block (around line 580). Replace its body:

```javascript
function renderModal(d) {
  data = d;
  if (!modal) return;
  var body = modal.querySelector('.late-body');
  var html = '';

  function renderActionableRow(item, sectionKind) {
    // sectionKind: 'scheduled' | 'unscheduled' | 'needs_reason'
    var rowClass = 'late-item late-item-' + sectionKind;
    var minsHtml = '';
    if (sectionKind === 'scheduled') {
      minsHtml = '<span class="late-item-mins">' + item.minutes_late + ' min late</span>';
    } else if (sectionKind === 'needs_reason') {
      minsHtml = '<span class="late-item-mins">clocked in ' + item.minutes_late + ' min late</span>';
    }
    var actionsHtml;
    if (sectionKind === 'needs_reason') {
      actionsHtml = ''
        + '<div class="late-reason-row">'
        + '  <button type="button" class="late-quickpick" data-pick="Sick">Sick</button>'
        + '  <button type="button" class="late-quickpick" data-pick="Car issues">Car issues</button>'
        + '  <button type="button" class="late-quickpick" data-pick="Overslept">Overslept</button>'
        + '  <button type="button" class="late-quickpick" data-pick="">Other</button>'
        + '  <input type="text" class="late-reason-input" placeholder="Reason (optional)">'
        + '  <button type="button" class="late-save-late">Save</button>'
        + '</div>';
    } else {
      actionsHtml = ''
        + '<span class="late-item-actions">'
        + '  <button type="button" class="late-snooze">Snooze 30 min</button>'
        + '  <button type="button" class="late-declare">Declare Absent</button>'
        + '</span>'
        + '<div class="late-reason-row late-declare-reason" hidden>'
        + '  <button type="button" class="late-quickpick" data-pick="Sick">Sick</button>'
        + '  <button type="button" class="late-quickpick" data-pick="Car issues">Car issues</button>'
        + '  <button type="button" class="late-quickpick" data-pick="Overslept">Overslept</button>'
        + '  <button type="button" class="late-quickpick" data-pick="">Other</button>'
        + '  <input type="text" class="late-reason-input" placeholder="Reason (optional)">'
        + '  <button type="button" class="late-save-absent">Save</button>'
        + '</div>';
    }
    return ''
      + '<li class="' + rowClass + '" data-emp-id="' + escapeHtml(item.emp_id)
      +    '" data-name="' + escapeHtml(item.name) + '">'
      + '<span class="late-item-name">' + escapeHtml(item.name) + '</span>'
      + minsHtml
      + actionsHtml
      + '<span class="late-status" hidden></span>'
      + '</li>';
  }

  var anyActionable = false;

  if (d.scheduled_late && d.scheduled_late.length) {
    anyActionable = true;
    html += '<h4 class="late-section-title">Scheduled — haven\'t clocked in</h4>';
    html += '<ul class="late-list">';
    d.scheduled_late.forEach(function (item) {
      html += renderActionableRow(item, 'scheduled');
    });
    html += '</ul>';
  }

  if (d.unscheduled_late && d.unscheduled_late.length) {
    anyActionable = true;
    html += '<h4 class="late-section-title">Unscheduled — also haven\'t clocked in</h4>';
    html += '<ul class="late-list">';
    d.unscheduled_late.forEach(function (item) {
      html += renderActionableRow(item, 'unscheduled');
    });
    html += '</ul>';
  }

  if (d.needs_reason && d.needs_reason.length) {
    anyActionable = true;
    html += '<h4 class="late-section-title">Late arrivals — reason needed</h4>';
    html += '<ul class="late-list">';
    d.needs_reason.forEach(function (item) {
      html += renderActionableRow(item, 'needs_reason');
    });
    html += '</ul>';
  }

  if (!anyActionable) {
    html += '<p class="late-help">No one is currently flagged. Anyone scheduled today who hasn\'t clocked in by 15 min past shift-start, anyone unscheduled in the same situation, or anyone who clocked in late without a recorded reason, will appear here.</p>';
  }

  if (d.snoozed && d.snoozed.length) {
    html += '<h4 class="late-section-title">Snoozed</h4>';
    html += '<ul class="late-list">';
    d.snoozed.forEach(function (s) {
      html += '<li class="late-snoozed-item">';
      html += '<span class="late-snoozed-name">' + escapeHtml(s.name) + '</span>';
      html += '<span>re-checks in ' + s.mins_remaining + ' min</span>';
      html += '</li>';
    });
    html += '</ul>';
  }

  body.innerHTML = html;
  wireBodyHandlers();
}
```

- [ ] **Step 2: Replace `wireBodyHandlers`**

Below `renderModal`, replace `wireBodyHandlers`:

```javascript
function wireBodyHandlers() {
  if (!modal) return;
  var body = modal.querySelector('.late-body');

  // Quick-pick buttons populate the adjacent text input.
  body.querySelectorAll('.late-quickpick').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var input = btn.parentElement.querySelector('.late-reason-input');
      if (input) {
        input.value = btn.dataset.pick || '';
        input.focus();
      }
    });
  });

  // Snooze.
  body.querySelectorAll('.late-snooze').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var li = btn.closest('.late-item');
      doAction(li, '/api/late-report/snooze', {
        emp_id: li.dataset.empId,
        name: li.dataset.name,
        minutes: 30,
      });
    });
  });

  // Declare Absent — toggles the inline reason editor.
  body.querySelectorAll('.late-declare').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var li = btn.closest('.late-item');
      var editor = li.querySelector('.late-declare-reason');
      if (editor) {
        editor.hidden = false;
        var input = editor.querySelector('.late-reason-input');
        if (input) input.focus();
      }
    });
  });

  // Save (Declare Absent).
  body.querySelectorAll('.late-save-absent').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var li = btn.closest('.late-item');
      var input = li.querySelector('.late-reason-input');
      doAction(li, '/api/late-report/declare-absent', {
        emp_id: li.dataset.empId,
        name: li.dataset.name,
        reason: input ? input.value : '',
      });
    });
  });

  // Save (Late Arrival reason).
  body.querySelectorAll('.late-save-late').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var li = btn.closest('.late-item');
      var input = li.querySelector('.late-reason-input');
      doAction(li, '/api/late-report/save-late-arrival', {
        emp_id: li.dataset.empId,
        name: li.dataset.name,
        reason: input ? input.value : '',
      });
    });
  });
}

function doAction(li, url, payload) {
  var status = li.querySelector('.late-status');
  if (status) { status.hidden = false; status.textContent = 'Saving…'; }
  fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  }).then(function (r) { return r.json(); }).then(function (resp) {
    if (resp && resp.ok) {
      li.style.opacity = 0.5;
      setTimeout(function () { refreshCount(); }, 300);
    } else {
      if (status) { status.textContent = 'Failed: ' + ((resp && resp.error) || 'unknown'); }
    }
  }).catch(function () {
    if (status) { status.textContent = 'Network error.'; }
  });
}
```

The existing `refreshCount` function in `_footer.html` polls `/api/late-report` and re-renders if the modal is open. Make sure it's compatible — find `refreshCount` and verify it (it already exists; no changes needed if it just calls `renderModal(data)` when the modal is open).

- [ ] **Step 3: Smoke-test the template parses**

```
.venv/Scripts/python.exe -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'))
env.get_template('_footer.html')
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/_footer.html
git commit -m "feat(late_report): popup renders three sections + reason editors

renderModal now branches by section: scheduled_late and
unscheduled_late get the existing Snooze + Declare-Absent buttons,
plus an inline reason editor revealed by Declare-Absent.
needs_reason rows show the reason editor inline by default with a
Save button. Quick-pick buttons (Sick / Car issues / Overslept /
Other) populate the adjacent text input. wireBodyHandlers attaches
listeners for all four save flows.

Snoozed list unchanged.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Final test pass + CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the non-DB test suite**

```
.venv/Scripts/python.exe -m pytest tests/test_progress.py tests/test_deps_window_dates.py tests/test_share_route.py tests/test_results.py tests/test_zira_persist.py tests/test_slack_client.py tests/test_late_report.py tests/test_views_store.py tests/test_wc_attributions.py tests/test_leaderboards_avg.py tests/test_production_history.py tests/test_leaderboards_person_days.py tests/test_player_card.py -q
```

Expected: all PASS (DB-only tests skip without DATABASE_URL; logic tests including the new `late_people_for_day_v2` and player-card render tests pass).

- [ ] **Step 2: Get the current local time**

```
date "+%I:%M %p"
```

Note for the CHANGELOG entry below.

- [ ] **Step 3: Add CHANGELOG entry**

Insert at the top of today's section:

```markdown
### {time-from-step-2}

- **Late/Absence report covers unscheduled people, captures reasons, and the Player card grows an Attendance section** — three coupled improvements: (1) The popup now lists active non-reserve operators who didn't punch in even if they weren't on today's schedule (Gerardo Vergara would now show up alongside Isaac Miller). Same Snooze and Declare Absent buttons. (2) When someone clocks in past the late threshold, the popup auto-surfaces a "Late arrivals — reason needed" entry. Quick-pick buttons (Sick / Car issues / Overslept / Other) populate a short text field; click Save to record. Declare Absent now also has an inline reason editor (optional). (3) Each player's card at `/staffing/people/{name}` gains an Attendance table showing per-day Absent / Late history with reasons in the active range, plus two new tiles (Days Absent, Days Late). Reason cells are inline-editable so today's "(no reason)" entries can be filled in later from the card.
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat: attendance reasons + player-card history

End-to-end. Late/absence popup now has scheduled, unscheduled, and
late-arrivals-needs-reason sections. Reasons (with Sick / Car
issues / Overslept / Other quick-picks) flow into manual_absences
and the new late_arrivals table. Player card at
/staffing/people/{name} renders the per-day history with
inline-editable reason cells and two new stat tiles.

Schema changes are additive (ALTER TABLE manual_absences ADD
reason; CREATE TABLE late_arrivals).

Spec: docs/superpowers/specs/2026-05-07-attendance-reasons-and-history-design.md
Plan: docs/superpowers/plans/2026-05-07-attendance-reasons-and-history.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

---

## Self-review checklist

- [ ] Spec section 1 (Late report covers unscheduled + needs_reason) — covered by Tasks 3, 4, 5 ✓
- [ ] Spec section 2 (Reason field + quick-pick) — covered by Tasks 1, 2, 6, 7, 10 ✓
- [ ] Spec section 3 (Player card Attendance + 2 stat tiles + inline edit) — covered by Tasks 8, 9 ✓
- [ ] Schema changes additive — covered by Task 1 ✓
- [ ] All 6 testing items in spec — Tasks 2, 4, 8 cover them (DB-skips fine) ✓
- [ ] No placeholders / TODOs ✓
- [ ] All file paths exact, all code blocks complete ✓
- [ ] Type consistency: `late_arrivals` table column names match between Task 1 (DDL), Task 2 (queries), Task 9 (UPDATE) ✓
- [ ] `late_people_for_day_v2` signature consistent across Tasks 4 (def) and 5 (call) ✓
- [ ] data attribute names consistent: `data-emp-id` / `data-name` (Task 10) match what doAction reads ✓
