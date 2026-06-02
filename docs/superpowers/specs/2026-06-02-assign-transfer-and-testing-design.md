# Assign → department transfer + Testing window

**Date:** 2026-06-02
**Status:** Approved, ready for implementation plan

## Problem

When the app senses production at an unscheduled metered work center, it
surfaces the WC in the "Assignments to Do" modal so a manager can attribute
the work to the person who actually ran it. Two gaps:

1. **No department move.** Picking a person credits the production but does
   nothing in Odoo. If that person physically moved to a different department
   to run this WC, their Odoo attendance still shows them in the old
   department. Hours-by-department reports are wrong until someone fixes it by
   hand.

2. **No way to mark testing.** Some sensed production is just machine testing,
   not real output by a person. Today the only choice is to credit a real
   person for units they didn't truly produce, or leave the WC nagging in the
   to-do list. There's no way to say "8:50–9:30 was testing, credit no one,
   and here's who worked the rest."

## Goals

- On assign, automatically transfer the person to the WC's department in Odoo
  when they're currently in a different one — effective at the start of the
  window they worked. Reversible via Undo.
- Add a **Testing** button next to **Save** that lets the manager carve a
  testing window (credited to no one) out of the sensed window, then assign a
  real person for the remainder.

## Non-goals

- General time-windowed crediting for *scheduled* WCs. Crediting stays
  whole-WC-for-day except for the testing carve-out described below.
- Modeling multiple testing segments per WC, or multiple remainder people.
  One testing window + one remainder person per WC per assign action.
- A standalone "transfer person" UI. Transfer only happens as a side effect
  of an assignment.

## Background / current behavior

- **Assign flow.** The footer "Assignments to Do" modal
  (`templates/_footer.html`) and the inline-assign popovers in
  `recycling.html` / `new_dept.html` all POST to
  `POST /api/staffing/attribute` (`routes/staffing.py`), which inserts one
  `wc_time_attributions` row.
- **Table.** `wc_time_attributions(id, day, wc_name, person_name, start_utc,
  end_utc, source DEFAULT 'manual', created_at)`. `person_name` is NOT NULL.
  No schema change needed — `source` distinguishes testing rows.
- **Crediting is whole-WC-for-day.** `production_history.attribute_for_day`
  takes `wc_totals = {wc: (units, downtime)}` (full-day totals) and splits each
  WC's units equally among its operators. The attribution's `start_utc` /
  `end_utc` are stored but used **only as display labels** — they do not affect
  crediting today.
- **`attribution_for(d, client)`** is the single chokepoint: it builds
  `wc_totals` + `extra = wc_attributions.people_by_wc(d)` and calls
  `attribute_for_day`. It feeds live numbers and (via the nightly precompute
  into `production_daily`) historical numbers.
- **Transfer primitive exists.** `odoo_client.transfer(employee_id,
  new_wc_name, ts)` closes the employee's current open `hr.attendance` at `ts`
  and opens a new one at `ts`, tagged with the new WC's department (via
  `_department_id_for_wc`). `clock_in` writes the resolved department field.
- **Leaderboard samples.** `leaderboard.Result.samples` is a tuple of
  `(event_dt_utc, units)`, so units inside an arbitrary sub-window can be
  summed. `cached_leaderboard` makes repeat calls cheap.
- **Person → Odoo.** `staffing.Person.employee_id` is the `hr.employee.id`
  (None for legacy people with no Odoo link).
- **WC → department.** `staffing.Location.department` (Recycled / New /
  Supervisor / Maintenance / Transportation); `_department_id_for_wc(wc_name)`
  resolves it to an Odoo `hr.department.id`.

## Decisions (from brainstorming)

- Transfer trigger compares the WC's department to the person's **current open
  Odoo attendance** department (a real physical move), not their home dept.
- Transfer timestamp = **start of the window the person worked** (production
  start for a normal assign; testing-end for the remainder person), clamped to
  not precede the existing punch's check-in.
- Testing units are **credited to no one**.
- Odoo writes are **automatic with an Undo** toast.
- If the person has **no open attendance**, **open a fresh punch** at the WC's
  department effective the window start.

## Design

### 1. Data model

No DDL change. Conventions on `wc_time_attributions`:

- **Testing segment:** `source = 'testing'`, `person_name = 'Testing'`
  (module constant `wc_attributions.TESTING_PERSON`). Never fed into crediting
  as an operator.
- **Real attribution:** unchanged (`source = 'manual'`).

New / changed helpers in `wc_attributions.py`:

- `people_by_wc(day)` — **filter to `source <> 'testing'`** so a testing row
  never becomes a credited operator.
- `testing_windows_for_day(day) -> {wc_name: [(start_utc, end_utc), ...]}` —
  new accessor over `source = 'testing'` rows.
- `unattributed_for_day(day, client)` — a WC drops off the to-do list when it
  has **either** a real operator (`people_by_wc`) **or** a testing window
  covering it (`testing_windows_for_day`), so an all-testing WC stops nagging.

### 2. Department-transfer decision (server-side)

New module `staffing_transfer.py` with a single testable entry point used by
both endpoints:

```
decide_and_apply(person_name, wc_name, window_start_utc, client) -> dict
```

Logic:

1. Resolve `person_name` → `employee_id` via the roster. None →
   `{"transfer": "skipped_no_employee"}`, no Odoo write.
2. `current = odoo_client.get_current_attendance(employee_id)`.
3. `transfer_ts = max(window_start_utc, current.check_in)` when a punch exists,
   else `window_start_utc`. (Prevents closing a punch before it opened.)
4. Branch:
   - **Open punch, current dept == WC dept** → no-op,
     `{"transfer": "already_in_dept"}`.
   - **Open punch, dept differs** → `odoo_client.transfer(employee_id,
     wc_name, transfer_ts)` → `(closed_id, new_id)`;
     `{"transfer": "moved", "closed_id", "new_id", "from_dept", "to_dept",
     "person"}`.
   - **No open punch** → `odoo_client.clock_in(employee_id, wc_name,
     transfer_ts)` → `new_id`;
     `{"transfer": "opened", "new_id", "to_dept", "person"}`.

`get_current_attendance` must expose the attendance's department id (the kiosk
department field) so step 4 can compare. The implementation plan verifies this
and adds the field to the read if missing.

### 3. Endpoints

- **`POST /api/staffing/attribute`** (existing) — after inserting the
  attribution row, call `decide_and_apply(person, wc, start_utc, client)` and
  include its result in the JSON response under `transfer`.
- **`POST /api/staffing/attribute-with-testing`** (new). Body:
  `{day, wc_name, testing_start_utc, testing_end_utc, remainder_person?}`.
  - Insert the testing row (`source='testing'`, person `'Testing'`,
    `[testing_start, testing_end]`).
  - If `remainder_person` is present: insert a normal attribution row for it
    (window `[testing_end, sensed_end]`, display-only), then
    `decide_and_apply(remainder_person, wc, testing_end_utc, client)`.
  - Return `{ok, ids, transfer}`.
- **`POST /api/staffing/transfer/undo`** (new). Body `{closed_id?, new_id}`.
  Calls `odoo_client.undo_transfer(closed_id, new_id)`: `unlink` `new_id`; if
  `closed_id`, reopen it by writing `check_out = False`. Reverses only the
  Odoo write — the credit row is removed separately via the existing × in
  "Saved today."

Cache invalidation (`invalidate_today_cache`) on all mutating paths, as today.

### 4. Footer modal UI (`templates/_footer.html`)

- Add a **Testing** button beside each item's **Save**.
- Clicking expands an inline panel within the item:
  - Two time inputs (testing start / end) prefilled with the sensed window's
    `first_label` / `last_label`; both editable.
  - A *"Who worked after testing?"* person picker, optional.
  - A **Confirm** button → `POST /api/staffing/attribute-with-testing`.
- After any save whose `transfer` result is `"moved"` or `"opened"`, show a
  toast: *"Transferred {person} → {to_dept}"* with an **Undo** link calling
  `/api/staffing/transfer/undo`. Reuse the existing toast styling if present.
- Time inputs are entered in site-local time and converted to UTC ISO before
  POST (mirror the existing `first_iso` handling). Validate
  `testing_start < testing_end <= sensed_end`.

### 5. Crediting carve-out (`production_history.attribution_for`)

After building `wc_totals`:

- `testing = wc_attributions.testing_windows_for_day(d)`. If non-empty, fetch
  per-WC `samples` (reuse the leaderboard results already needed for
  `wc_totals`; refactor `_fetch_wc_totals` to also return samples, or add a
  sibling helper) and, for each WC with testing windows, subtract the units of
  samples whose `event_dt` falls inside any testing window from that WC's
  total (floored at 0).
- Then call `attribute_for_day` as today. The remainder person is credited
  `WC_total − testing_units`; the `'Testing'` sentinel is never an operator,
  so it appears on no leaderboard or player card.

This is the only point touching the crediting layer; it covers live numbers
immediately and historical numbers once the nightly precompute reruns.

## Edge cases

- **Legacy person (no `employee_id`).** Attribute only; `transfer:
  "skipped_no_employee"`; no toast.
- **Punch starts after window start.** `transfer_ts` clamp keeps
  check_out ≥ check_in.
- **All-testing WC (no remainder person).** Testing row alone removes the WC
  from the to-do list; no operator credited; testing units subtracted.
- **Testing window in the middle of the sensed window.** Allowed. The single
  remainder person gets all non-testing units (crediting is whole-WC minus
  testing, not per-segment).
- **Undo after the page reloaded.** Undo is driven by `closed_id`/`new_id`
  returned in the POST response and held in the toast; once dismissed/reloaded,
  reversal is via reassign or manual Odoo edit. Acceptable.

## Test plan

- **`staffing_transfer.decide_and_apply`** (Odoo client mocked): no-employee,
  already-in-dept, dept-differs→transfer, no-punch→open; `transfer_ts` clamp.
- **`wc_attributions`**: `people_by_wc` excludes `source='testing'`;
  `testing_windows_for_day` shape; `unattributed_for_day` drops a WC covered
  only by testing.
- **Crediting**: `attribution_for` subtracts testing-window units; remainder
  person credited `total − testing`; `'Testing'` sentinel absent from output.
- **`odoo_client.undo_transfer`** (mocked): unlinks new, reopens old.
- **Endpoints**: `attribute` returns `transfer`; `attribute-with-testing`
  happy path and testing-only (no remainder); `transfer/undo`.

## Affected files

- `src/zira_dashboard/wc_attributions.py` — testing helpers, `people_by_wc`
  filter, `unattributed_for_day` change.
- `src/zira_dashboard/staffing_transfer.py` — new decision module.
- `src/zira_dashboard/odoo_client.py` — `undo_transfer`; ensure
  `get_current_attendance` exposes department id.
- `src/zira_dashboard/production_history.py` — testing carve-out in
  `attribution_for` (+ samples from `_fetch_wc_totals`).
- `src/zira_dashboard/routes/staffing.py` — `attribute` transfer hook,
  `attribute-with-testing`, `transfer/undo`.
- `src/zira_dashboard/templates/_footer.html` — Testing button + panel, toast
  + Undo.
- Tests under `tests/` mirroring the plan above.
