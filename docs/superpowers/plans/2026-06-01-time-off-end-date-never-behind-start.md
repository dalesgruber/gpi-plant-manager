# Time-off End Date Never Behind Start — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the full-day time-off request form, the end date can never sit behind the start date — it snaps up to the start whenever either date would leave it behind, while preserving a deliberately-set multi-day end.

**Architecture:** A single client-side change in the request form's JS. Replace the current unconditional "start change → end = start" reset with a `clampEndToStart()` that only snaps the end up when it is behind the start, and run it on changes to **both** date inputs (today only the start re-syncs). Also point the end picker's `min` at the start as a UX hint. No server, template, or Python changes.

**Tech Stack:** Vanilla JS (no framework, no bundler), `<input type="date">` (ISO `YYYY-MM-DD` values, lexicographically comparable). Repo has no JS test runner; verification is `node --check` for syntax plus a browser check that loads the real file against a minimal fixture.

**Spec:** `docs/superpowers/specs/2026-06-01-time-off-end-date-never-behind-start-design.md`

---

### Task 1: Conditional end-date clamp on both date inputs

**Files:**
- Modify: `src/zira_dashboard/static/timeclock_time_off.js:217-241` (the listener-wiring block at the end of the IIFE)
- Temp (verification only, deleted in Step 5): `_endclamp_fixture.html` at repo root

- [ ] **Step 1: Replace the listener-wiring block**

In `src/zira_dashboard/static/timeclock_time_off.js`, replace this exact block (currently lines 217–241):

```javascript
  // Recalc the balance panel when the type, end date, or times change.
  [typeSel, dateTo, timeA, timeB].forEach(function (el) {
    if (el) {
      el.addEventListener("change", recalc);
      el.addEventListener("input", recalc);
    }
  });

  // Picking a start date defaults the end date to match it, so a single-day
  // request is one tap (the user can still push the end date out afterward
  // for a multi-day request). Only the full_day form has an end-date input;
  // the partial shapes use a server-forced single date, so dateTo is null
  // there and this is a harmless no-op. Recalc after syncing.
  function onStartDateChange() {
    if (dateTo && dateFrom.value) {
      dateTo.value = dateFrom.value;
    }
    recalc();
  }
  if (dateFrom) {
    dateFrom.addEventListener("change", onStartDateChange);
    dateFrom.addEventListener("input", onStartDateChange);
  }

  recalc();
```

with:

```javascript
  // The end date must never fall behind the start date. Whenever either
  // date changes, snap the end up to the start if it's behind — this
  // collapses an inverted range to a single day, while a multi-day end the
  // user set on purpose stays put as long as it's still on/after the start.
  // Picking a future start when the end is still at today therefore pulls
  // the end along (the one-tap single-day flow). Also point the end-date
  // picker's `min` at the start so earlier days grey out in the calendar
  // popup — a hint; the value clamp below is the real enforcement.
  //
  // Only the full_day form has a real end-date input; the partial shapes
  // use a hidden, server-forced single date (id "date-to-hidden"), so
  // dateTo is null there and this is a harmless no-op.
  function clampEndToStart() {
    if (dateTo && dateFrom && dateFrom.value) {
      dateTo.min = dateFrom.value;
      if (dateTo.value && dateTo.value < dateFrom.value) {
        dateTo.value = dateFrom.value;
      }
    }
  }

  // Keep the end date valid and the balance panel current on any change to
  // the type, either date, or the times.
  function onDateOrTimeChange() {
    clampEndToStart();
    recalc();
  }

  [typeSel, dateFrom, dateTo, timeA, timeB].forEach(function (el) {
    if (el) {
      el.addEventListener("change", onDateOrTimeChange);
      el.addEventListener("input", onDateOrTimeChange);
    }
  });

  clampEndToStart();
  recalc();
```

- [ ] **Step 2: Syntax check**

Run: `node --check src/zira_dashboard/static/timeclock_time_off.js`
Expected: no output, exit 0 (any parse error prints file:line and exits non-zero).

- [ ] **Step 3: Create a minimal browser fixture that loads the real file**

Write `_endclamp_fixture.html` at the repo root (so the relative `src` resolves to the real JS):

```html
<!doctype html>
<meta charset="utf-8">
<title>end-clamp fixture</title>
<div id="time-off-details" data-shape="full_day" data-shift-from="8" data-shift-to="16.5"></div>
<select id="holiday-status-select">
  <option value="1" data-requires-alloc="no" data-unit="day">Vacation</option>
</select>
<label>Start <input type="date" id="date-from" value="2026-06-01"></label>
<label>End <input type="date" id="date-to" value="2026-06-01"></label>
<button id="submit-btn">Submit</button>
<script src="src/zira_dashboard/static/timeclock_time_off.js"></script>
```

(The real IIFE bails unless `#time-off-details`, `#holiday-status-select`, and `#submit-btn` exist; the balance-panel elements are intentionally absent, so `recalc` skips the panel math via its `hasBalancePanel` guard.)

- [ ] **Step 4: Verify behavior in a browser**

Open `_endclamp_fixture.html` in a browser (e.g. `preview_start` on the repo root then open the file, or `file://`), and run this in the console. Every assert must stay silent (a failure logs `N fail: <value>`):

```javascript
const f = document.getElementById('date-from');
const t = document.getElementById('date-to');
const setF = v => { f.value = v; f.dispatchEvent(new Event('change')); };
const setT = v => { t.value = v; t.dispatchEvent(new Event('change')); };

// reset
f.value = '2026-06-01'; t.value = '2026-06-01';

// 1. Future start, end was behind → end pulled up (one-tap single day)
setF('2026-06-10');
console.assert(t.value === '2026-06-10', '1 fail: ' + t.value);

// 2. Push end out to a later date → kept
setT('2026-06-15');
console.assert(t.value === '2026-06-15', '2 fail: ' + t.value);

// 3. Nudge start earlier but still <= end → multi-day end preserved
setF('2026-06-12');
console.assert(t.value === '2026-06-15', '3 fail: ' + t.value);

// 4. Set end before start → snaps back up to start (now 2026-06-12)
setT('2026-06-05');
console.assert(t.value === '2026-06-12', '4 fail: ' + t.value);

// 5. min hint points at the current start
console.assert(t.min === '2026-06-12', '5 fail min: ' + t.min);

console.log('end-clamp checks done');
```

Expected console output: only `end-clamp checks done` (no `N fail` lines).

- [ ] **Step 5: Remove the fixture and commit**

```bash
rm _endclamp_fixture.html
git add src/zira_dashboard/static/timeclock_time_off.js
git commit -m "$(cat <<'EOF'
fix(timeclock): keep the time-off end date from falling behind the start

The full-day request form now snaps the end date up to the start whenever
it would sit behind it — on changes to either date, not just the start.
This replaces the prior unconditional "start change resets end" reset:
a multi-day end the user set on purpose is now preserved when they nudge
the start within range, and editing the end to before the start (which
previously slipped through and got swapped server-side into an unintended
span) now snaps back up. The end picker's min is also pointed at the start
so earlier days grey out. Partial shapes use a hidden single date, so this
stays a no-op there.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

Confirm `_endclamp_fixture.html` is not staged (it was removed) and `git status` is clean apart from the pre-existing untracked `PTO-Policy-Proposal-DRAFT.pdf`.

---

## Self-Review

**Spec coverage:**
- "snap end up to start when behind, both directions" → Step 1 `clampEndToStart` + wiring to both inputs; checks 1 & 4.
- "multi-day end preserved when correcting only on inversion" → check 3.
- "one-tap single day kept" → check 1.
- "native picker min hint" → Step 1 `dateTo.min`; check 5.
- "partial shapes = no-op" → guard `if (dateTo && …)`; covered by spec note (dateTo is the hidden `date-to-hidden`, so `getElementById("date-to")` is null).
- "no server/template/Python changes" → only the one JS file is modified.
- "verified by node --check + manual browser check" → Steps 2 & 4.

**Placeholder scan:** None — full replacement code, full fixture, full console script, exact commands and commit message are inline.

**Type/name consistency:** `clampEndToStart` and `onDateOrTimeChange` are defined in Step 1 and referenced only there. Element ids (`date-from`, `date-to`, `holiday-status-select`, `submit-btn`, `time-off-details`) match the real template and the file's existing `getElementById` calls. ISO date strings compared with `<`.
