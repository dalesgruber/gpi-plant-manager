# Running Late and Breakdown Presence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Record an absent employee’s expected arrival time and prevent machine-breakdown cards from treating scheduled-but-unpunched people as operators.

**Architecture:** Add an explicit expected-arrival table, separate from generic late snoozes. The late-report snapshot suppresses actionable late rows while an expected arrival is active and emits one muted Running Late follow-up. Machine-breakdown logic derives presence only from an open attendance window at the actual work center, and closes any incident without one.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, vanilla JavaScript, PostgreSQL, pytest.

## Global Constraints

- Expected arrivals are UTC timestamps; managers choose an HH:MM time in shift_config.SITE_TZ.
- A chosen time must be strictly later than the current plant-local time.
- Generic 30-minute Snooze and full-day Absence behavior do not change.
- A breakdown operator requires an open attendance window at the work center.
- Do not change production thresholds, create timeclock punches, or change the Odoo attendance source.
- Create each test first, run it red, then write the smallest implementation to turn it green.

---

## File structure

| File | Responsibility |
| --- | --- |
| src/zira_dashboard/_schema.py | Bootstrap expected-arrival storage. |
| src/zira_dashboard/late_report.py | Expected-arrival data helpers. |
| src/zira_dashboard/routes/late_report.py | Validate and save the manager action. |
| src/zira_dashboard/routes/staffing.py | Generate the running_late snapshot section. |
| src/zira_dashboard/exception_inbox.py | Render it as a muted inbox follow-up. |
| src/zira_dashboard/templates/exceptions.html | Time picker and controls. |
| src/zira_dashboard/static/exceptions.js | Client-side interaction and POST. |
| src/zira_dashboard/machine_breakdown.py | Strict present operator detection and closure. |
| tests/test_late_expected_arrivals.py | Postgres store tests. |
| tests/test_late_report_running_late.py | Endpoint and snapshot tests. |
| tests/test_exception_inbox.py | Inbox mapping test. |
| tests/test_exception_inbox_breakdown_template.py | Server-rendered control test. |
| tests/test_exception_inbox_breakdown_js.py | JavaScript contract test. |
| tests/test_machine_breakdown_rows.py | Presence and lifecycle tests. |

## Interfaces

~~~
def set_expected_arrival(day, emp_id: str, name: str, expected_at_utc: datetime) -> None: ...
def active_expected_arrivals(day) -> list[dict]: ...
def clear_expected_arrival(day, emp_id: str) -> None: ...
def _running_late_sync(body: dict) -> JSONResponse: ...
def _present_operators_on_wc(wc_name: str, day: date, now: datetime | None = None) -> list[str]: ...
~~~

### Task 1: Store expected late-arrival times

**Files:**
- Modify: src/zira_dashboard/_schema.py immediately after late_snoozes.
- Modify: src/zira_dashboard/late_report.py after snooze helpers.
- Create: tests/test_late_expected_arrivals.py.

**Consumes:** db.execute, db.query, UTC-aware datetime values.

**Produces:** Store helpers consumed by Task 2.

- [ ] **Step 1: Write failing database tests**

~~~
import os
from datetime import date, datetime, timedelta, timezone

import pytest

from zira_dashboard import db, late_report

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


@pytest.fixture
def day():
    value = date(2099, 7, 13)
    db.bootstrap_schema()
    db.execute("DELETE FROM late_expected_arrivals WHERE day = %s", (value,))
    yield value
    db.execute("DELETE FROM late_expected_arrivals WHERE day = %s", (value,))


def test_expected_arrival_upserts_and_lists_only_future_row(day):
    arrival = datetime.now(timezone.utc) + timedelta(minutes=45)
    late_report.set_expected_arrival(day, "7", "Jesus Galindo", arrival)
    late_report.set_expected_arrival(day, "7", "Jesus G.", arrival + timedelta(minutes=15))

    assert late_report.active_expected_arrivals(day) == [{
        "emp_id": "7",
        "name": "Jesus G.",
        "expected_at_utc": arrival + timedelta(minutes=15),
    }]


def test_clear_expected_arrival_removes_employee(day):
    late_report.set_expected_arrival(
        day, "7", "Jesus Galindo", datetime.now(timezone.utc) + timedelta(minutes=45)
    )

    late_report.clear_expected_arrival(day, "7")

    assert late_report.active_expected_arrivals(day) == []
~~~

- [ ] **Step 2: Verify the store tests fail**

Run: pytest tests/test_late_expected_arrivals.py -v

Expected: FAIL because the table or set_expected_arrival helper does not exist.

- [ ] **Step 3: Create the schema and helpers**

~~~
CREATE TABLE IF NOT EXISTS late_expected_arrivals (
  day             DATE NOT NULL,
  emp_id          TEXT NOT NULL,
  name            TEXT NOT NULL,
  expected_at_utc TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (day, emp_id)
);
CREATE INDEX IF NOT EXISTS late_expected_arrivals_day_idx
  ON late_expected_arrivals(day);
~~~

~~~
def set_expected_arrival(day, emp_id: str, name: str, expected_at_utc: datetime) -> None:
    db.execute(
        """
        INSERT INTO late_expected_arrivals (day, emp_id, name, expected_at_utc)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (day, emp_id) DO UPDATE SET
          name = EXCLUDED.name,
          expected_at_utc = EXCLUDED.expected_at_utc,
          created_at = now()
        """,
        (day, str(emp_id), name, expected_at_utc),
    )


def active_expected_arrivals(day) -> list[dict]:
    return db.query(
        """
        SELECT emp_id, name, expected_at_utc
        FROM late_expected_arrivals
        WHERE day = %s AND expected_at_utc > now()
        ORDER BY expected_at_utc ASC
        """,
        (day,),
    )


def clear_expected_arrival(day, emp_id: str) -> None:
    db.execute(
        "DELETE FROM late_expected_arrivals WHERE day = %s AND emp_id = %s",
        (day, str(emp_id)),
    )
~~~

- [ ] **Step 4: Verify store tests pass**

Run: pytest tests/test_late_expected_arrivals.py -v

Expected: PASS, or SKIPPED without DATABASE_URL.

- [ ] **Step 5: Commit**

~~~
git add src/zira_dashboard/_schema.py src/zira_dashboard/late_report.py tests/test_late_expected_arrivals.py
git commit -m "feat: store expected late arrivals"
~~~

### Task 2: Validate Running Late and publish it in the late snapshot

**Files:**
- Modify: src/zira_dashboard/routes/late_report.py around _snooze_sync.
- Modify: src/zira_dashboard/routes/staffing.py inside late_report_payload.
- Create: tests/test_late_report_running_late.py.

**Consumes:** Task 1 helper methods, plant_today, plant_now, and _bust_caches.

**Produces:** POST /api/late-report/running-late and a non-actionable running_late payload section.

- [ ] **Step 1: Write the failing action tests**

~~~
import json
from datetime import UTC, date, datetime
from unittest.mock import MagicMock

from zira_dashboard import shift_config
from zira_dashboard.routes import late_report as late_report_routes


def test_running_late_rejects_time_that_is_not_after_now(monkeypatch):
    monkeypatch.setattr(late_report_routes, "plant_today", lambda: date(2026, 7, 13))
    monkeypatch.setattr(
        late_report_routes, "plant_now",
        lambda: datetime(2026, 7, 13, 8, 30, tzinfo=shift_config.SITE_TZ),
    )

    response = late_report_routes._running_late_sync({
        "emp_id": "7", "name": "Jesus Galindo", "expected_time": "08:30"
    })

    assert response.status_code == 400
    assert "later than now" in json.loads(response.body)["error"]


def test_running_late_saves_utc_time_and_busts_caches(monkeypatch):
    save = MagicMock()
    bust = MagicMock()
    monkeypatch.setattr(late_report_routes, "plant_today", lambda: date(2026, 7, 13))
    monkeypatch.setattr(
        late_report_routes, "plant_now",
        lambda: datetime(2026, 7, 13, 8, 30, tzinfo=shift_config.SITE_TZ),
    )
    monkeypatch.setattr(late_report_routes.late_report, "set_expected_arrival", save)
    monkeypatch.setattr(late_report_routes, "_bust_caches", bust)

    response = late_report_routes._running_late_sync({
        "emp_id": "7", "name": "Jesus Galindo", "expected_time": "09:15"
    })

    assert response.status_code == 200
    assert save.call_args.args[:3] == (date(2026, 7, 13), "7", "Jesus Galindo")
    assert save.call_args.args[3] == datetime(
        2026, 7, 13, 9, 15, tzinfo=shift_config.SITE_TZ
    ).astimezone(UTC)
    bust.assert_called_once()
~~~

- [ ] **Step 2: Run red**

Run: pytest tests/test_late_report_running_late.py -v

Expected: FAIL because _running_late_sync is not defined.

- [ ] **Step 3: Implement time parsing and the endpoint**

~~~
from datetime import UTC, datetime, time as dt_time
from ..plant_day import now as plant_now, today as plant_today


def _running_late_sync(body: dict) -> JSONResponse:
    emp_id = str(body.get("emp_id") or "").strip()
    name = str(body.get("name") or "").strip()
    selected = _parse_clock_time(body.get("expected_time"))
    if not emp_id or not name:
        return JSONResponse({"ok": False, "error": "emp_id and name required"}, status_code=400)
    if selected is None:
        return JSONResponse({"ok": False, "error": "expected_time must be HH:MM"}, status_code=400)

    expected_local = datetime.combine(plant_today(), selected, tzinfo=shift_config.SITE_TZ)
    if expected_local <= plant_now():
        return JSONResponse(
            {"ok": False, "error": "expected time must be later than now"}, status_code=400
        )
    late_report.set_expected_arrival(
        plant_today(), emp_id, name, expected_local.astimezone(UTC)
    )
    _bust_caches()
    return JSONResponse({"ok": True, "expected_at": expected_local.isoformat()})


@router.post("/api/late-report/running-late")
async def late_report_running_late(request: Request):
    return await asyncio.to_thread(_running_late_sync, await request.json())
~~~

- [ ] **Step 4: Write and run a failing snapshot test**

~~~
def test_late_payload_emits_running_late_and_suppresses_no_punch_action(monkeypatch):
    from zira_dashboard.routes import staffing as staffing_routes
    from zira_dashboard import attendance, staffing

    expected = datetime(2026, 7, 13, 14, 15, tzinfo=UTC)
    staffing_routes._LATE_REPORT_CACHE["value"] = None
    staffing_routes._LATE_REPORT_CACHE["expires_at"] = 0.0
    monkeypatch.setattr(staffing_routes, "plant_today", lambda: date(2026, 7, 13))
    monkeypatch.setattr(
        staffing_routes, "plant_now",
        lambda: datetime(2026, 7, 13, 9, 0, tzinfo=shift_config.SITE_TZ),
    )
    monkeypatch.setattr(
        staffing, "load_schedule", lambda day: type("Schedule", (), {
            "assignments": {"Repair 1": ["Jesus Galindo"]}
        })(),
    )
    monkeypatch.setattr(staffing_routes, "_safe_attendance", lambda *args: {
        "by_id": {"7": {"status": "no_punch"}},
        "scheduled_ids": ["7"],
        "name_to_id": {"Jesus Galindo": "7"},
    })
    monkeypatch.setattr(
        staffing, "load_roster", lambda: [type("Person", (), {
            "name": "Jesus Galindo", "wage_type": "hourly", "is_flexible": False
        })()],
    )
    monkeypatch.setattr(attendance, "person_id_to_name", lambda names: {"7": "Jesus Galindo"})
    monkeypatch.setattr(staffing_routes.late_report, "absent_emp_ids_for_day", lambda day: set())
    monkeypatch.setattr(staffing_routes.late_report, "late_arrivals_for_day", lambda day: set())
    monkeypatch.setattr(
        staffing_routes.late_report, "active_expected_arrivals",
        lambda day: [{"emp_id": "7", "name": "Jesus Galindo", "expected_at_utc": expected}],
    )
    monkeypatch.setattr(staffing_routes.late_report, "active_snoozes", lambda day: [])

    payload = staffing_routes.late_report_payload(force=True)

    assert payload["scheduled_late"] == []
    assert payload["running_late"][0]["name"] == "Jesus Galindo"
    assert payload["running_late"][0]["expected_label"] == "9:15 AM"
    assert payload["count"] == 0
~~~

Run: pytest tests/test_late_report_running_late.py -v

Expected: FAIL because running_late is not emitted.

- [ ] **Step 5: Implement snapshot composition**

~~~
# Add to the late_report_payload default dictionary.
"running_late": [],

# Before late_people_for_day_v2:
expected_arrivals = late_report.active_expected_arrivals(today)
expected_ids = {str(row["emp_id"]) for row in expected_arrivals}
snoozed_ids = {str(row["emp_id"]) for row in late_report.active_snoozes(today)} | expected_ids

# After normal actionable-section mapping and before computing count:
now_utc = datetime.now(UTC)
for row in expected_arrivals:
    emp_id = str(row["emp_id"])
    if (by_id.get(emp_id) or {}).get("status") != "no_punch":
        late_report.clear_expected_arrival(today, emp_id)
        continue
    expected_local = row["expected_at_utc"].astimezone(shift_config.SITE_TZ)
    out["running_late"].append({
        "emp_id": emp_id,
        "name": row["name"],
        "until_iso": row["expected_at_utc"].isoformat(),
        "expected_label": expected_local.strftime("%-I:%M %p"),
        "mins_remaining": max(
            0, int((row["expected_at_utc"] - now_utc).total_seconds() // 60)
        ),
    })
~~~

Use the project’s existing os.name time-format branch from machine_breakdown._local_time_label so the Windows format is %#I instead of %-I. Keep the count expression limited to scheduled_late, unscheduled_late, and needs_reason.

- [ ] **Step 6: Run green**

Run: pytest tests/test_late_report_running_late.py tests/test_late_report.py tests/test_late_report_absence_odoo.py -v

Expected: PASS.

- [ ] **Step 7: Commit**

~~~
git add src/zira_dashboard/routes/late_report.py src/zira_dashboard/routes/staffing.py tests/test_late_report_running_late.py
git commit -m "feat: add running late expected arrival"
~~~

### Task 3: Render the muted follow-up and time picker

**Files:**
- Modify: src/zira_dashboard/exception_inbox.py after the snoozed-row loop.
- Modify: src/zira_dashboard/templates/exceptions.html in the late_absence action branch.
- Modify: src/zira_dashboard/static/exceptions.js before the generic Snooze handler.
- Modify: tests/test_exception_inbox.py, tests/test_exception_inbox_breakdown_template.py, tests/test_exception_inbox_breakdown_js.py.

**Consumes:** Task 2 payload and endpoint.

**Produces:** Visible Running Late action, and one muted inbox follow-up after save.

- [ ] **Step 1: Write failing inbox/UI contract tests**

~~~
def test_build_snapshot_maps_running_late_to_muted_follow_up(monkeypatch):
    monkeypatch.setattr(exception_inbox.plant_day, "today", lambda: date(2026, 7, 13))
    monkeypatch.setattr(staffing_routes, "assignments_todo_payload", lambda: {"count": 0})
    monkeypatch.setattr(staffing_routes, "late_report_payload", lambda: {
        "count": 0, "scheduled_late": [], "unscheduled_late": [],
        "needs_reason": [], "snoozed": [],
        "running_late": [{
            "emp_id": "7", "name": "Jesus Galindo",
            "until_iso": "2026-07-13T14:15:00+00:00", "expected_label": "9:15 AM",
        }],
    })
    monkeypatch.setattr(missing_wc, "current_rows", lambda: [])
    monkeypatch.setattr(missed_punch_out, "current_rows", lambda: [])
    monkeypatch.setattr(machine_breakdown, "current_rows", lambda: [])
    monkeypatch.setattr(exception_inbox, "_work_center_names", lambda: [])
    monkeypatch.setattr(exception_inbox, "_pending_time_off", lambda day: (0, []))
    snap = exception_inbox.build_snapshot()
    late = next(section for section in snap["sections"] if section["id"] == "late")

    assert late["rows"][-1]["label"] == "Running Late"
    assert late["rows"][-1]["detail"] == "Expected by 9:15 AM"
    assert late["rows"][-1]["priority"] == "muted"
    assert late["rows"][-1]["action"] is None


def test_late_absence_row_renders_running_late_controls(monkeypatch):
    monkeypatch.setattr(exceptions_route.exception_inbox, "build_snapshot", _late_snapshot)
    response = TestClient(app).get("/exceptions")

    assert "js-running-late-open" in response.text
    assert "js-running-late-time" in response.text
    assert "js-running-late-save" in response.text


def test_exceptions_js_has_running_late_handler():
    js = (STATIC_DIR / "exceptions.js").read_text(encoding="utf-8")
    assert "js-running-late-open" in js
    assert "js-running-late-save" in js
    assert "/api/late-report/running-late" in js
~~~

- [ ] **Step 2: Run red**

Run: pytest tests/test_exception_inbox.py tests/test_exception_inbox_breakdown_template.py tests/test_exception_inbox_breakdown_js.py -v

Expected: FAIL for missing mapping and controls.

- [ ] **Step 3: Add the mapping and controls**

~~~
# exception_inbox.py
for item in late.get("running_late") or []:
    late_rows.append({
        "name": item.get("name"),
        "label": "Running Late",
        "detail": f"Expected by {item.get('expected_label')}",
        "priority": "muted",
        "badge": "Follow-up",
        "row_key": _row_key("running_late", item.get("emp_id"), item.get("until_iso")),
        "item_key": inbox_keys.late(item.get("emp_id"), today.isoformat()),
        "action": None,
    })
~~~

~~~
<button type="button" class="row-btn js-running-late-open">Running Late</button>
<input type="time" class="inline-input time js-running-late-time"
       aria-label="Expected arrival time" hidden>
<button type="button" class="row-btn primary js-running-late-save" hidden>Confirm</button>
~~~

~~~
if (rowBtn.classList.contains('js-running-late-open')) {
  row.querySelector('.js-running-late-time').hidden = false;
  row.querySelector('.js-running-late-save').hidden = false;
  row.querySelector('.js-running-late-time').focus();
  return;
}

if (rowBtn.classList.contains('js-running-late-save')) {
  var expectedTime = row.querySelector('.js-running-late-time').value;
  if (!empId || !personName || !expectedTime) {
    failRow(row, expectedTime ? 'Missing employee id.' : 'Choose an expected arrival time.');
    return;
  }
  setBusy(row, true);
  rowStatus(row, 'Saving expected arrival...', false);
  postJson('/api/late-report/running-late', {
    emp_id: empId, name: personName, expected_time: expectedTime
  }).then(function (resp) {
    if (resp && resp.ok) resolveRow(row, 'Running late');
    else failRow(row, (resp && resp.error) || 'Could not save expected arrival.');
  }).catch(function () { failRow(row, 'Network error.'); });
  return;
}
~~~

Update follow_up_total to include the running_late list. Do not add those rows to total, urgent_total, or the actionable late count.

- [ ] **Step 4: Run green**

Run: pytest tests/test_exception_inbox.py tests/test_exception_inbox_breakdown_template.py tests/test_exception_inbox_breakdown_js.py -v

Expected: PASS.

- [ ] **Step 5: Commit**

~~~
git add src/zira_dashboard/exception_inbox.py src/zira_dashboard/templates/exceptions.html src/zira_dashboard/static/exceptions.js tests/test_exception_inbox.py tests/test_exception_inbox_breakdown_template.py tests/test_exception_inbox_breakdown_js.py
git commit -m "feat: show running late inbox follow-up"
~~~

### Task 4: Require an open punch for breakdown detection and display

**Files:**
- Modify: src/zira_dashboard/machine_breakdown.py in _operators_on_wc, _station_signals, run_detect_tick, _maybe_auto_resolve, current_rows, and report_manual.
- Modify: tests/test_machine_breakdown_rows.py.

**Consumes:** timeclock_windows.attendance_windows_for_day(day), whose tuple values are work center, start UTC, and end UTC or None.

**Produces:** Current-only presence and automatic handling of empty incidents.

- [ ] **Step 1: Write failing presence and lifecycle tests**

~~~
def test_present_operators_requires_open_punch_at_this_work_center(monkeypatch):
    from zira_dashboard import timeclock_windows
    now = _now()
    monkeypatch.setattr(timeclock_windows, "attendance_windows_for_day", lambda day: {
        "Jesus Galindo": [("Repair 1", now - timedelta(hours=1), now - timedelta(minutes=1))],
        "Juan": [("Repair 1", now - timedelta(hours=1), None)],
        "Ana": [("Repair 2", now - timedelta(hours=1), None)],
    })

    assert machine_breakdown._present_operators_on_wc(
        "Repair 1", date(2026, 7, 8), now
    ) == ["Juan"]


def test_current_rows_hides_incident_without_present_operator(monkeypatch):
    incident = {
        "id": 1, "wc_name": "Repair 1", "day": date(2026, 7, 8),
        "detected_stop_utc": _now() - timedelta(minutes=25), "source": "auto",
    }
    monkeypatch.setattr(machine_breakdown, "all_open_incidents", lambda day: [incident])
    monkeypatch.setattr(
        machine_breakdown, "_present_operators_on_wc", lambda wc, day, now=None: []
    )

    assert machine_breakdown.current_rows(day=date(2026, 7, 8), now=_now()) == []


def test_detect_tick_handles_incident_after_final_operator_leaves(monkeypatch):
    incident = {
        "id": 1, "wc_name": "Repair 1", "day": date(2026, 7, 8),
        "detected_stop_utc": _now() - timedelta(minutes=25), "source": "auto",
    }
    monkeypatch.setattr(machine_breakdown, "all_open_incidents", lambda day: [incident])
    monkeypatch.setattr(
        machine_breakdown, "_present_operators_on_wc", lambda wc, day, now=None: []
    )
    monkeypatch.setattr(machine_breakdown, "_station_signals", lambda day, now: [])
    handled = []
    monkeypatch.setattr(
        machine_breakdown, "resolve_incident",
        lambda incident_id, resolution, resume_utc=None: handled.append((incident_id, resolution)),
    )

    machine_breakdown.run_detect_tick(day=date(2026, 7, 8), now=_now())

    assert handled == [(1, "handled")]
~~~

- [ ] **Step 2: Run red**

Run: pytest tests/test_machine_breakdown_rows.py -v

Expected: FAIL because _present_operators_on_wc is absent and current_rows includes the header.

- [ ] **Step 3: Implement strict presence and apply it consistently**

~~~
def _present_operators_on_wc(
    wc_name: str, day: date, now: datetime | None = None
) -> list[str]:
    """Names with an open attendance window at this work center at now."""
    now = now or datetime.now(UTC)
    return sorted({
        person
        for person, windows in _punch_windows_for_day(day).items()
        for punched_wc, start, end in windows
        if punched_wc == wc_name and start <= now and end is None
    })
~~~

Replace the active-breakdown uses of _operators_on_wc with _present_operators_on_wc:
- station-signal has_operator;
- new incident attributions;
- recovery caps;
- current_rows operator list;
- manual report attributions and its empty self-resolution.

Keep _cap_departed_operators iterating the punch-window names, so it can cap a breakdown attribution for someone whose final attendance window is now closed. At the beginning of each open-incident pass in run_detect_tick, add:

~~~
if not _present_operators_on_wc(incident["wc_name"], day, now):
    resolve_incident(incident["id"], "handled")
    continue
~~~

In current_rows, calculate present before adding the header. If present is empty, continue; otherwise loop over present. This is a defensive display guard in case a stale incident remains between detection passes.

- [ ] **Step 4: Run green**

Run: pytest tests/test_machine_breakdown_rows.py tests/test_machine_breakdown_store.py tests/test_exception_inbox_breakdown_template.py tests/test_exception_inbox_breakdown_js.py -v

Expected: PASS, with database-only tests possibly SKIPPED without DATABASE_URL.

- [ ] **Step 5: Commit**

~~~
git add src/zira_dashboard/machine_breakdown.py tests/test_machine_breakdown_rows.py
git commit -m "fix: require punched-in operators for breakdown alerts"
~~~

### Task 5: Verify the integrated feature

**Files:**
- Test: all files listed in Tasks 1 through 4.

**Consumes:** Tasks 1 through 4.

**Produces:** Verified, scope-contained behavior.

- [ ] **Step 1: Run the full test suite**

Run: pytest -v

Expected: PASS, except explicit Postgres tests may be SKIPPED without DATABASE_URL.

- [ ] **Step 2: Run static analysis**

Run: ruff check src/zira_dashboard/_schema.py src/zira_dashboard/late_report.py src/zira_dashboard/routes/late_report.py src/zira_dashboard/routes/staffing.py src/zira_dashboard/exception_inbox.py src/zira_dashboard/machine_breakdown.py

Expected: All checks passed!

- [ ] **Step 3: Inspect scope and workspace state**

Run: git diff main~4..HEAD --stat && git status --short

Expected: Feature commits contain only expected-arrival, inbox, and breakdown-presence work; do not stage unrelated workspace changes.

