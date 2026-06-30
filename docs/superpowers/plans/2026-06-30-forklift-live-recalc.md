# Forklift Live Recalculation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recalculate the Forklift bay predicted time-to-claim immediately when scheduled forklift driver counts change.

**Architecture:** Add server-rendered live model fields to the existing forklift advisor output, expose them as `window.FORKLIFT_LIVE_MODEL`, and update `staffing.js` to recompute the scheduled-driver prediction locally on every schedule picker mutation.

**Tech Stack:** Python, Jinja2, vanilla JavaScript, pytest static/template tests.

## Global Constraints

- Live recalculation must not wait for autosave.
- Use the same Erlang-C formula and thresholds as the server advisor.
- Count whichever work centers the server says count as forklift driver slots.
- Preserve autosave behavior and existing server-side fallback rendering.

---

### Task 1: Server Live Model

**Files:**
- Modify: `src/zira_dashboard/forklift_advisor.py`
- Modify: `src/zira_dashboard/routes/staffing.py`
- Modify: `src/zira_dashboard/templates/staffing.html`
- Test: `tests/test_forklift_advisor.py`
- Test: `tests/test_staffing_forklift_card.py`

- [x] Add advisor live model fields containing recommendation, target, lambda, mean handle, calibration, status, and overload state.
- [x] Pass configured forklift driver work center names to the template.
- [x] Render `window.FORKLIFT_LIVE_MODEL`.

### Task 2: Browser Recalculation

**Files:**
- Modify: `src/zira_dashboard/static/staffing.js`
- Test: `tests/test_staffing_static.py`

- [x] Add JS Erlang-C helper and status classification.
- [x] Count checked people in configured forklift driver work centers.
- [x] Update the existing `.forklift-bay-summary` immediately after scheduled picker changes, clears, resets, undo, and redo.

### Task 3: Verification

- [x] Run `PYTHONPATH=src .venv/bin/pytest tests/test_forklift_advisor.py tests/test_staffing_forklift_card.py tests/test_staffing_static.py -q`.
- [x] Run `ZIRA_API_KEY=test PYTHONPATH=src .venv/bin/python -c "from zira_dashboard.deps import templates; templates.get_template('staffing.html'); print('ok')"`.
