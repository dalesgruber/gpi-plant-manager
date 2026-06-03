# staffing.py Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Extract the pure per-work-center render model and the I/O attendance/late-report helpers out of the 1284-line `routes/staffing.py` into `staffing_view.py` + `staffing_attendance.py`, behavior- and UI-identical, and lock the render model (currently untested) with characterization tests.

**Architecture:** `build_staffing_bays(...)` is pure given already-loaded `roster`/`sched`/`time_off_entries` — extract it to `staffing_view.py` (mirroring the already-extracted `wc_dashboard_data.py`) and characterize it. The six attendance helpers (`_safe_attendance` & friends) are I/O + exception-swallowing — move them to `staffing_attendance.py`, preserving lazy imports and `except` semantics verbatim. The route becomes an orchestrator that fans out I/O, calls `build_staffing_bays`, and merges the result into the template context. `_LATE_REPORT_CACHE`, `_bust_after_mutation` (imported by `routes/late_report.py`), `/api/late-report`, and all POST/JSON endpoints stay in the route.

**Tech Stack:** Python 3.11, pytest (monkeypatch, SimpleNamespace stand-ins), FastAPI/Jinja (unchanged). Render-model tests are pure (no `DATABASE_URL`).

---

## File Structure

- **Create** `src/zira_dashboard/staffing_view.py` — pure `build_staffing_bays(roster, sched, time_off_entries, publish_blocked) -> dict`. Imports only `from . import staffing, work_centers_store` (lazy, like `wc_dashboard_data.py`). No FastAPI/template/DB imports.
- **Create** `tests/test_staffing_view.py` — characterization tests for `build_staffing_bays` (pure; the render model has no direct test today).
- **Create** `src/zira_dashboard/staffing_attendance.py` — the six helpers `_live_or_fallback`, `_safe_time_off_entries`, `_attendance_with_fallback`, `_timeoff_names_with_fallback`, `_safe_attendance`, `_late_emp_ids`. Keep their lazy `from . import live_cache / scheduler_time_off / attendance / late_report` imports and every `except Exception` verbatim.
- **Modify** `src/zira_dashboard/routes/staffing.py` — import the helpers + `build_staffing_bays`; `staffing_page` calls `build_staffing_bays` and merges; delete the moved code. Keep orchestration, caching, ThreadPoolExecutor, `_LATE_REPORT_CACHE`, `_bust_*`, `_next_working_day`, `_Phase`, all endpoints.
- **Modify** `tests/test_staffing_attendance_source.py` — repoint monkeypatches from the route module to `staffing_attendance`.
- **Modify** `tests/test_staffing_options_color.py` — (optional, Task 3) rewrite to test the real `build_staffing_bays` instead of re-implementing the color contract inline.

---

## Task 1: Extract the pure render model → `staffing_view.build_staffing_bays`

**Files:**
- Create: `src/zira_dashboard/staffing_view.py`
- Create: `tests/test_staffing_view.py`
- Modify: `src/zira_dashboard/routes/staffing.py` (the pure render bands inside `staffing_page`)

- [ ] **Step 1: Read the current inline render model.** In `routes/staffing.py`, read `staffing_page` and identify the two PURE bands (no I/O): (A) the pure derivations ~lines 341-426 (`full_day_entries`/`time_off_set`, `active_people`/`all_by_name`, `all_active_people`, `partial_hours_by_name`/`partial_range_by_name`/`partial_clear_by_name`) and (B) the bay model ~lines 447-596 (the `options_for` closure + `_options_cache`, the `bays` loop, `publish_block_reasons`, `defaults_by_loc`, `unassigned`/`reserves`/`assigned_today`). Confirm there is NO I/O in those bands (only `staffing.*` / `work_centers_store.*` pure helpers + stdlib). If any I/O is present, leave that line in the route and pass its result in as a param — note it.

- [ ] **Step 2: Create `staffing_view.py`** with:

```python
"""Pure render-model builder for the staffing day view, extracted from
routes/staffing.py. Given already-loaded roster + schedule + time-off data,
computes the per-work-center bays, headcount status, reserve pools, default
assignments, publish-block reasons, and partial-time-off derivations. No DB /
Odoo / Request / template imports — the route fans out the I/O and passes the
results in. Mirrors the already-extracted wc_dashboard_data.py."""
from __future__ import annotations


def build_staffing_bays(roster, sched, time_off_entries, publish_blocked):
    """Build the template's per-WC render model. Pure; see module docstring."""
    from . import staffing, work_centers_store
    # <-- the exact bodies of bands A + B, moved VERBATIM, with options_for as a
    #     nested function over active_people + a local _options_cache dict.
    ...
    return {
        # exact keys the template consumes (confirm against the route's context):
        "bays": bays,
        "publish_block_reasons": publish_block_reasons,
        "defaults_by_loc": defaults_by_loc,
        "unassigned": unassigned,
        "reserves": reserves,
        "time_off_names": time_off_names,
        "time_off_entries": time_off_entries_sorted,
        "partial_hours_by_name": partial_hours_by_name,
        "partial_range_by_name": partial_range_by_name,
        "partial_clear_by_name": partial_clear_by_name,
        "people_meta": people_meta,
        "all_active_people": all_active_people,
    }
```

Move the band bodies VERBATIM (same comprehensions, same sort keys `(reserve, -level, name.lower())`, same headcount thresholds, same skill-color calls, same "currently-assigned safety net" re-add). Do not change logic. The exact return keys must match what the route currently puts in the `TemplateResponse` context from these bands — verify by reading the context dict.

- [ ] **Step 3: Wire the route.** In `staffing_page`, replace bands A + B with:

```python
    bay_model = staffing_view.build_staffing_bays(
        roster=roster, sched=sched, time_off_entries=time_off_entries,
        publish_blocked=publish_blocked,
    )
```

and merge `bay_model` into the `TemplateResponse` context (e.g. `**bay_model`) alongside the I/O-derived keys (`attendance_by_name`, `late_names_set`, `assignments_todo`, `assignments_done`, `attributions_by_wc`, `cleared_partials_today`, `person_certs`, `eff_*`, snapshot flags). Add `from .. import staffing_view` (two-dot sibling import). Confirm `time_off_set` is computed inside the builder (the bay loop + `present_operators` close over it) — don't leave a stale duplicate in the route.

- [ ] **Step 4: Write characterization tests** in `tests/test_staffing_view.py`. Use `SimpleNamespace` Person stand-ins + monkeypatched `work_centers_store.required_skills/min_ops/max_ops/default_people` and `staffing.LOCATIONS` (follow the pattern in `tests/test_staffing_options_color.py`). After extraction, read the real returned structure and pin these scenarios with exact asserts:
  - **Skill color:** WC `required=("Repair",)` → assigned level-0 person gets the red color; level≥1 gets `staffing.skill_color(lvl)`; **blank `required` → level 2 / neutral** for both assigned and pool.
  - **Headcount status:** count 0 → "empty"; `< min_ops` → "under"; `max_ops` set and count `> max_ops` → "over"; else "ok".
  - **Reserve split + sort:** pool sorted `(reserve, -level, name.lower())`; reserve flags fire.
  - **Currently-assigned safety net:** a historically-assigned name absent from `options_for` is re-appended to pool (`trained = level>=1, reserve=False`) — not dropped.
  - **Time-off exclusion:** names in `time_off_set` removed from pool + headcount, but retained in `assigned`/`assigned_set`; excluded from `unassigned`/`reserves`.
  - **Partials:** `partial_hours_by_name` only for `0 < hours < 8`; full-day (`hours is None`) excluded from partials, included in `time_off_names`.
  - **publish_block_reasons:** populated only when `publish_blocked` AND `hc_status=="under"` AND `min_ops>=2`.

- [ ] **Step 5: Verify.**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_staffing_view.py -v` → PASS.
Run: `./.venv/Scripts/python.exe -m pytest -q` → still green (was 545 passed, 207 skipped; now 545 + new tests).
Run: `./.venv/Scripts/ruff.exe check src/zira_dashboard/staffing_view.py src/zira_dashboard/routes/staffing.py tests/test_staffing_view.py --output-format=concise` → "All checks passed!" (no unused import left in the route from the moved bands).
Run: `./.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); import zira_dashboard.app; print('import OK')"` → import OK.

- [ ] **Step 6: Commit (local, no push).**

```bash
git add src/zira_dashboard/staffing_view.py tests/test_staffing_view.py src/zira_dashboard/routes/staffing.py
git commit -m "refactor(staffing): extract pure build_staffing_bays render model to staffing_view"
```

---

## Task 2: Extract the attendance/late-report helpers → `staffing_attendance.py`

**Files:**
- Create: `src/zira_dashboard/staffing_attendance.py`
- Modify: `src/zira_dashboard/routes/staffing.py`
- Modify: `tests/test_staffing_attendance_source.py`

- [ ] **Step 1: Move the six helpers VERBATIM** from `routes/staffing.py` into a new `staffing_attendance.py`: `_live_or_fallback`, `_safe_time_off_entries`, `_attendance_with_fallback`, `_timeoff_names_with_fallback`, `_safe_attendance`, `_late_emp_ids`. Module docstring: """Attendance + late-report data assembly for the staffing views. I/O-backed (live_cache / Odoo / scheduler), with degrade-to-empty exception handling so a backend hiccup yields an empty panel, not a 500. Extracted from routes/staffing.py.""" Their lazy `from .. import …` imports become `from . import …` (now a top-level package module). **Preserve every `except Exception` and `# noqa: BLE001` verbatim** — the degrade-to-empty semantics are load-bearing. Also move/keep `_Phase`? NO — `_Phase` and `_server_timing_header` stay in the route. Only the six helpers move.

- [ ] **Step 2: Fix the internal-call seam.** Inside `_safe_attendance`, the calls to `_timeoff_names_with_fallback(...)` and `_attendance_with_fallback(...)` must remain monkeypatchable. Since all three now live in `staffing_attendance.py`, change those two internal calls to reference the module so a test patch on the module object takes effect:

```python
from . import staffing_attendance  # at top of staffing_attendance.py? No — same module.
```

Actually they are in the SAME module, so a bare call resolves to the module global and `monkeypatch.setattr(staffing_attendance, "_attendance_with_fallback", ...)` WILL take effect (module globals are looked up at call time). Confirm by keeping the calls as bare names `_attendance_with_fallback(...)` / `_timeoff_names_with_fallback(...)` (NOT importing them into a local). This is the default and needs no change — just verify the test (Step 4) patches `staffing_attendance.*`.

- [ ] **Step 3: Repoint the route.** In `routes/staffing.py`, delete the six moved functions and add:

```python
from ..staffing_attendance import (
    _safe_attendance, _safe_time_off_entries, _late_emp_ids,
)
```

(Only import the names the route actually calls: `staffing_page` uses `_safe_time_off_entries`, `_safe_attendance`, `_late_emp_ids`; `late_report_json` uses `_safe_attendance`. `_live_or_fallback`/`_attendance_with_fallback`/`_timeoff_names_with_fallback` are internal to `staffing_attendance` — do NOT import them into the route unless something there calls them directly; grep to confirm.) Remove any now-unused imports left in the route (ruff F401 will flag them).

- [ ] **Step 4: Repoint the test.** In `tests/test_staffing_attendance_source.py`, change the monkeypatch targets and the `_safe_attendance` reference from the route module to `from zira_dashboard import staffing_attendance` and patch/call `staffing_attendance._timeoff_names_with_fallback`, `staffing_attendance._attendance_with_fallback`, `staffing_attendance._safe_attendance` (and any `attendance`/`staffing`/`shift_config` patches stay as they are, since those modules are unchanged). The test's intent is unchanged — only the home of the patched functions moved.

- [ ] **Step 5: Verify.**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_staffing_attendance_source.py -v` → PASS (with the repointed patches).
Run: `./.venv/Scripts/python.exe -m pytest -q` → still green, 0 failed.
Run: `./.venv/Scripts/ruff.exe check src/zira_dashboard/staffing_attendance.py src/zira_dashboard/routes/staffing.py tests/test_staffing_attendance_source.py --output-format=concise` → "All checks passed!"
Run: `./.venv/Scripts/python.exe -c "import sys; sys.path.insert(0,'src'); import zira_dashboard.app; print('import OK')"` → import OK. ALSO confirm `routes/late_report.py`'s `from .staffing import _bust_after_mutation` still resolves (it stays in the route).

- [ ] **Step 6: Commit (local, no push).**

```bash
git add src/zira_dashboard/staffing_attendance.py src/zira_dashboard/routes/staffing.py tests/test_staffing_attendance_source.py
git commit -m "refactor(staffing): extract attendance/late-report helpers to staffing_attendance"
```

---

## Task 3 (optional polish): point `test_staffing_options_color.py` at the real builder

**Files:** Modify `tests/test_staffing_options_color.py`

- [ ] **Step 1:** This test currently re-implements the skill-color contract inline (per its own comment) because it couldn't reach the `options_for` closure. Now that the logic lives in `staffing_view.build_staffing_bays`, rewrite the test to call the real function and assert the colors on the returned `bays` model, removing the duplicated inline contract. Keep the same scenarios. If the rewrite is awkward (the function needs more setup than the focused test wants), SKIP this task and leave the test as-is — it's polish, not required.

- [ ] **Step 2: Verify + commit.** `pytest -q` green; `git commit -m "test(staffing): point options-color test at the real build_staffing_bays"`.

---

## Notes / guardrails

- **`build_staffing_bays` is PURE** — it must not import or call `db`, `live_cache`, `attendance`, `odoo_client`, `scheduler_time_off`, FastAPI, or templates. Only `staffing` + `work_centers_store` pure helpers. If a band has I/O, leave it in the route and pass the result in.
- **Preserve exception-swallowing** in every moved attendance helper verbatim (degrade-to-empty panel, not a 500). Don't "tidy" the `# noqa: BLE001`.
- **Preserve lazy imports** in the moved attendance helpers — converting to top-level imports risks reintroducing the cycle they were dodging (`live_cache`/`scheduler_time_off`/`late_report`).
- **Do NOT move:** `_LATE_REPORT_CACHE`, `_bust_late_report_cache`, `_bust_after_mutation` (imported by `routes/late_report.py:29`), `/api/late-report`, `_next_working_day`, `_Phase`, `_server_timing_header`, or any POST/JSON endpoint.
- **`staffing_page` name must not change** — `tests/test_share_route.py` and `tests/test_page_warmer.py` reference the symbol/path.
- The DB-gated route tests skip locally; rely on the pure `build_staffing_bays` characterization tests + the repointed `test_staffing_attendance_source.py` + the verbatim-move discipline + the final review.

## CHANGELOG
After both tasks ship and are reviewed, add one `### TIME` entry under today's date summarizing the staffing.py decomposition (behavior-identical; render model now testable).
