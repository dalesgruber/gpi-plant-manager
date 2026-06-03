# Recycling Goal-Math Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock the recycling per-work-center goal math behind characterization tests (no DB needed), then extract the pure computation out of `routes/departments.py` into a new `recycling_data.py` — provably without changing any goal number.

**Architecture:** The per-WC goal denominator is `expected_by_wc(active_segments, target_per_hour, productive_minutes_fn)`. Its inputs are already pure (`resolve_segments`, `productive_minutes_in_window`); the risk is the *wiring* inside `_recycling_day_data` (departments.py:283-299), which has no test — both June goal regressions lived there. We (1) add pure characterization tests that pin that wiring and the segment resolution for the exact regression scenarios, then (2) extract `progress_color`, a `compute_per_wc_expected` wrapper, and the `_render_recycling` aggregation closures into `recycling_data.py`, keeping all I/O and the `station_target` / `productive_minutes_in_window` callables in the route so the goal number cannot drift.

**Tech Stack:** Python 3.11, pytest (monkeypatch), FastAPI/Jinja (unchanged). Tests are pure — they run without `DATABASE_URL`.

---

## File Structure

- **Create** `tests/test_recycling_goal_math.py` — characterization tests over the *existing* pure chain (`resolve_segments` + `expected_by_wc` + `productive_minutes_in_window`), pinning the June-regression scenarios. Added BEFORE any extraction; must stay green throughout.
- **Create** `src/zira_dashboard/recycling_data.py` — pure functions extracted from `departments.py`: `progress_color`, `compute_per_wc_expected`, `aggregate_buckets`, `group_goal`, `build_bars`, `sort_bars`, `build_downtime_rows`. No DB/Odoo/Request imports; depends only on `assignment_windows` + stdlib + types.
- **Create** `tests/test_recycling_data.py` — unit tests for the extracted pure functions (esp. the aggregation closures, which are currently untestable because they're nested).
- **Modify** `src/zira_dashboard/routes/departments.py` — replace inline `_progress_color`, the `expected_by_wc` wiring, and the `_render_recycling` aggregation closures with calls into `recycling_data`; dedupe the inline-assign popover + absent-names blocks into small route-local helpers. Keep all I/O, caching, threadpool, Request handling.
- **Modify** `src/zira_dashboard/assignment_windows.py` — fix the stale `expected_by_wc` docstring (says `effective_minutes_worked`; the route passes `productive_minutes_in_window` since the 6/02 fix).

Each task is independently committable and keeps the full suite green.

---

## Phase 1 — Characterization tests (no extraction; pure; protects the fire zone)

### Task 1: Pin segment resolution for the regression scenarios

**Files:**
- Test: `tests/test_recycling_goal_math.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""Characterization tests for the recycling per-WC goal math.

These compose the SAME pure pieces the /recycling route wires together
(assignment_windows.resolve_segments -> expected_by_wc with a breaks-only
productive-minutes function) and pin the exact scenarios behind the June 2026
goal regressions, so a future refactor of routes/departments.py cannot silently
change a goal number. Pure -- no DATABASE_URL needed.
"""
from datetime import datetime, timezone

from zira_dashboard import assignment_windows as aw


def _utc(h, m=0):
    # The math is tz-agnostic; a fixed UTC day keeps windows readable.
    return datetime(2026, 6, 2, h, m, tzinfo=timezone.utc)


def _minutes(_name, s, e):
    """Breaks-only productive minutes stub = full window span (no breaks)."""
    return (e - s).total_seconds() / 60.0


def test_segments_full_day_across_autolunch_split():
    # Auto-lunch closes the morning hr.attendance record and opens a fresh
    # afternoon one; BOTH come through as punch windows on the same WC.
    # (June 6/03 fix: goal must span the whole clocked-in day, not morning only.)
    segs = aw.resolve_segments(
        assignments={}, attributions=[],
        punch_windows={"Jose": [("Dismantler 1", _utc(12), _utc(16)),
                                ("Dismantler 1", _utc(17), _utc(20, 30))]},
        shift_start_utc=_utc(12), cap_utc=_utc(20, 30),
    )
    d1 = sorted([s for s in segs if s.wc_name == "Dismantler 1"], key=lambda s: s.start_utc)
    assert [(s.start_utc, s.end_utc) for s in d1] == [
        (_utc(12), _utc(16)), (_utc(17), _utc(20, 30))]
    total_min = sum((s.end_utc - s.start_utc).total_seconds() / 60.0 for s in d1)
    assert total_min == 450.0  # 240 morning + 210 afternoon (NOT 240 morning-only)


def test_segments_midday_assignment_to_unscheduled_wc_open_ended():
    # Operator assigned mid-shift to an UNSCHEDULED WC via an open-ended
    # attribution (end_utc=None) -> accrues from its own start to cap. (6/02 2:07 PM)
    segs = aw.resolve_segments(
        assignments={}, attributions=[
            {"wc_name": "Dismantler 4", "person_name": "Eulogio",
             "start_utc": _utc(15), "end_utc": None}],
        punch_windows={}, shift_start_utc=_utc(12), cap_utc=_utc(20),
    )
    d4 = [s for s in segs if s.wc_name == "Dismantler 4"]
    assert len(d4) == 1
    assert (d4[0].start_utc, d4[0].end_utc) == (_utc(15), _utc(20))  # own start -> cap


def test_segments_punch_beats_attribution_for_same_person():
    # Hybrid precedence: a person's punches win over a manual attribution.
    segs = aw.resolve_segments(
        assignments={}, attributions=[
            {"wc_name": "Repair 2", "person_name": "Ana", "start_utc": _utc(12), "end_utc": None}],
        punch_windows={"Ana": [("Repair 1", _utc(12), _utc(18))]},
        shift_start_utc=_utc(12), cap_utc=_utc(20),
    )
    wcs = {s.wc_name for s in segs if s.person_name == "Ana"}
    assert wcs == {"Repair 1"}  # punch wins; attribution ignored
```

- [ ] **Step 2: Run to verify they pass against current code**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_recycling_goal_math.py -v`
Expected: PASS (these characterize existing pure behavior). If any FAIL, the understanding is wrong — stop and reconcile before extracting.

- [ ] **Step 3: Commit**

```bash
git add tests/test_recycling_goal_math.py
git commit -m "test(recycling): characterize segment resolution for goal-regression scenarios"
```

### Task 2: Pin the goal-denominator math (`expected_by_wc`) for the regression scenarios

**Files:**
- Test: `tests/test_recycling_goal_math.py` (append)

- [ ] **Step 1: Write the failing tests**

```python
def test_expected_prorates_full_day_across_autolunch_split():
    segs = aw.resolve_segments(
        assignments={}, attributions=[],
        punch_windows={"Jose": [("Dismantler 1", _utc(12), _utc(16)),
                                ("Dismantler 1", _utc(17), _utc(20, 30))]},
        shift_start_utc=_utc(12), cap_utc=_utc(20, 30))
    exp = aw.expected_by_wc(segs, {"Dismantler 1": 6.0}, _minutes)
    assert round(exp["Dismantler 1"], 6) == round(6.0 * 450 / 60.0, 6)  # 45.0
    # Morning-only (the bug) would be 6.0 * 240/60 = 24.0 (~half) -> guarded above.


def test_expected_uses_breaks_only_not_timeoff_adjusted_minutes():
    # The pace goal must NOT shrink because an operator took partial leave.
    # expected_by_wc takes the minutes fn as a param: the route passes the
    # breaks-only productive_minutes_in_window, NOT effective_minutes_worked.
    segs = aw.resolve_segments(
        assignments={"Dismantler 1": ["Maria"]}, attributions=[], punch_windows={},
        shift_start_utc=_utc(12), cap_utc=_utc(20))  # 480-min window
    breaks_only = aw.expected_by_wc(segs, {"Dismantler 1": 6.0}, _minutes)
    timeoff_adjusted = aw.expected_by_wc(
        segs, {"Dismantler 1": 6.0}, lambda n, s, e: 240.0)  # if 4h leave were netted out
    assert breaks_only["Dismantler 1"] == 48.0          # correct pace (full window)
    assert timeoff_adjusted["Dismantler 1"] == 24.0     # the WRONG shrunk number
    assert breaks_only["Dismantler 1"] != timeoff_adjusted["Dismantler 1"]


def test_expected_skips_zero_target_and_zero_minute_segments():
    segs = aw.resolve_segments(
        assignments={"Dismantler 1": ["A"], "Trim Saw": ["B"]}, attributions=[],
        punch_windows={}, shift_start_utc=_utc(12), cap_utc=_utc(20))
    exp = aw.expected_by_wc(segs, {"Dismantler 1": 6.0, "Trim Saw": 0.0}, _minutes)
    assert "Trim Saw" not in exp           # thr <= 0 skipped
    assert exp["Dismantler 1"] == 48.0


def test_expected_testing_window_adds_nothing():
    # A testing carve-out is excluded upstream (wc_attributions.creditable_for_day
    # drops source='testing'), so it never reaches resolve_segments and adds 0.
    # (June 6/02 2:38 PM.) Model that absence here.
    segs = aw.resolve_segments(
        assignments={}, attributions=[], punch_windows={},
        shift_start_utc=_utc(12), cap_utc=_utc(20))
    exp = aw.expected_by_wc(segs, {"Dismantler 1": 6.0}, _minutes)
    assert exp == {}  # no segments -> no expected
```

- [ ] **Step 2: Run to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_recycling_goal_math.py -v`
Expected: PASS.

- [ ] **Step 3: Add a real `productive_minutes_in_window` breaks case**

```python
def test_breaks_only_minutes_subtract_breaks(monkeypatch):
    from datetime import date as _date
    from zira_dashboard import shift_config
    # A 30-min break inside the window must be subtracted from productive minutes.
    monkeypatch.setattr(shift_config, "breaks_for",
                        lambda d: [(_utc(15), _utc(15, 30))])
    mins = shift_config.productive_minutes_in_window(_date(2026, 6, 2), _utc(12), _utc(20))
    assert mins == 480 - 30  # span minus the overlapping break
```

(If `breaks_for`'s real return shape differs from `[(start, end)]`, match it exactly — read `shift_config.productive_minutes_in_window` first and mirror `tests/test_productive_minutes_window.py`.)

- [ ] **Step 4: Run + commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_recycling_goal_math.py -v` → PASS

```bash
git add tests/test_recycling_goal_math.py
git commit -m "test(recycling): pin goal denominator (expected_by_wc) for regression scenarios"
```

---

## Phase 2 — Extract pure computation into `recycling_data.py`

### Task 3: Extract `progress_color` (smallest, zero-risk move)

**Files:**
- Create: `src/zira_dashboard/recycling_data.py`
- Modify: `src/zira_dashboard/routes/departments.py` (`_progress_color` at :24-39 → import)
- Test: `tests/test_recycling_data.py` (create)

- [ ] **Step 1: Write the failing test**

```python
from zira_dashboard import recycling_data as rd


def test_progress_color_buckets():
    assert rd.progress_color(None) is None
    # Same thresholds/HSL as the current departments._progress_color — copy the
    # exact body, then assert the boundary values it currently produces.
```

- [ ] **Step 2: Create `recycling_data.py` with `progress_color`** — copy the EXACT body of `departments._progress_color` (read :24-39), rename to `progress_color` (drop the leading underscore; it's now a module public fn). Add a module docstring stating it's pure.

- [ ] **Step 3: Point the route at it** — in `departments.py`, replace the `_progress_color` def with `from .recycling_data import progress_color` (top import) and `progress_color` at call sites (the `_bars` closure uses it). Update the test's asserts to the real boundary values.

- [ ] **Step 4: Run + commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_recycling_data.py tests/test_recycling_goal_math.py -v` → PASS, then full `pytest -q` → 524 passed.

```bash
git add src/zira_dashboard/recycling_data.py src/zira_dashboard/routes/departments.py tests/test_recycling_data.py
git commit -m "refactor(recycling): extract progress_color to recycling_data"
```

### Task 4: Extract `compute_per_wc_expected` (the goal-denominator boundary)

**Files:**
- Modify: `src/zira_dashboard/recycling_data.py` (add fn)
- Modify: `src/zira_dashboard/routes/departments.py` (:286-299 → call)
- Test: `tests/test_recycling_data.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_compute_per_wc_expected_filters_active_and_defaults_zero():
    from datetime import datetime, timezone
    from zira_dashboard import assignment_windows as aw
    def u(h): return datetime(2026, 6, 2, h, tzinfo=timezone.utc)
    segs = [aw.WorkSegment("Dismantler 1", "A", u(12), u(20), "schedule"),
            aw.WorkSegment("Inactive WC", "B", u(12), u(20), "schedule")]
    out = rd.compute_per_wc_expected(
        segments=segs, active_wc_names={"Dismantler 1", "Dismantler 4"},
        target_per_hour={"Dismantler 1": 6.0, "Inactive WC": 6.0},
        productive_minutes=lambda n, s, e: (e - s).total_seconds() / 60.0)
    assert out["Dismantler 1"] == 48.0      # active + worked
    assert out["Dismantler 4"] == 0.0       # active, no segment -> defaulted to 0.0
    assert "Inactive WC" not in out          # filtered out (not in active set)
```

- [ ] **Step 2: Add `compute_per_wc_expected` to `recycling_data.py`**

```python
def compute_per_wc_expected(*, segments, active_wc_names, target_per_hour, productive_minutes):
    """Prorated expected pallets per ACTIVE work center.

    Mirrors the route wiring exactly (departments.py): filter segments to the
    active WCs, sum via assignment_windows.expected_by_wc, then default every
    active WC to 0.0 so the dashboard shows a goal even before any production.
    `productive_minutes(name, start, end)` MUST be the breaks-only
    shift_config.productive_minutes_in_window closure -- NOT effective_minutes_
    worked, which would wrongly shrink the pace goal on partial-leave days."""
    from . import assignment_windows
    active = [s for s in segments if s.wc_name in active_wc_names]
    out = assignment_windows.expected_by_wc(active, target_per_hour, productive_minutes)
    for name in active_wc_names:
        out.setdefault(name, 0.0)
    return out
```

- [ ] **Step 3: Wire the route** — replace `departments.py:286-299` (the `active_segments` filter + `expected_by_wc` call + `setdefault` loop) with:

```python
    per_wc_expected = recycling_data.compute_per_wc_expected(
        segments=segments,
        active_wc_names=active_wc_names,
        target_per_hour=target_per_hour,
        productive_minutes=lambda name, s_utc, e_utc:
            shift_config.productive_minutes_in_window(d, s_utc, e_utc),
    )
```

Keep the explanatory comment block (:287-292) — it documents why the breaks-only fn is used. Add `from . import recycling_data` (or `import recycling_data` per the route's import style).

- [ ] **Step 4: Run + commit**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_recycling_data.py tests/test_recycling_goal_math.py -v` → PASS; full `pytest -q` → 524 passed.

```bash
git add src/zira_dashboard/recycling_data.py src/zira_dashboard/routes/departments.py tests/test_recycling_data.py
git commit -m "refactor(recycling): extract compute_per_wc_expected (goal denominator) to recycling_data"
```

### Task 5: Extract the `_render_recycling` aggregation closures

**Files:**
- Modify: `src/zira_dashboard/recycling_data.py`
- Modify: `src/zira_dashboard/routes/departments.py` (:455-541 closures → calls)
- Test: `tests/test_recycling_data.py` (append)

- [ ] **Step 1: Read the closures** `_aggregate_buckets` (:455-469), `_group_goal` (:476-484), `_bars` (:490-514), `_sorted_bars` (:516-521), `_downtime_rows` (:523-541). For each, list the loop-local aggregates it closes over (`agg_expected`, `agg_category`, `agg_units`, `agg_downtime`, `agg_active_names`, `agg_who_today`, `elapsed_hours_total`, `total_elapsed`, `is_range`, `customs_all`).

- [ ] **Step 2: Write failing tests** for each as a pure function taking those aggregates as explicit params. Example for `group_goal`:

```python
def test_group_goal_sums_category_expected_over_hours():
    out = rd.group_goal(category="Dismantlers",
                        agg_expected={"Dismantler 1": 48.0, "Dismantler 4": 12.0},
                        agg_category={"Dismantler 1": "Dismantlers", "Dismantler 4": "Dismantlers"},
                        elapsed_hours_total=8.0)
    assert out == (48.0 + 12.0) / 8.0
```

(Write equivalent tests for `aggregate_buckets`, `build_bars` incl. `pct_of_target` + `progress_color`, `sort_bars`, `build_downtime_rows`, mirroring each closure's current output exactly.)

- [ ] **Step 3: Move each closure to `recycling_data.py`** as a module function with the closed-over state promoted to explicit keyword params (signatures per the §6 map in the spec). Names: `aggregate_buckets`, `group_goal`, `build_bars`, `sort_bars`, `build_downtime_rows`.

- [ ] **Step 4: Wire the route** — in `_render_recycling`, replace each closure with a call to the `recycling_data` function, passing the aggregates explicitly. Do the same in `_render_new_dept` where it uses the equivalents.

- [ ] **Step 5: Run + commit**

Run: full `pytest -q` → 524 passed (+ the new pure tests). If `DATABASE_URL` is available, also run the DB-gated dashboard tests.

```bash
git add src/zira_dashboard/recycling_data.py src/zira_dashboard/routes/departments.py tests/test_recycling_data.py
git commit -m "refactor(recycling): extract aggregation helpers to recycling_data"
```

---

## Phase 3 — Dedupe + stale-docstring fix

### Task 6: Dedupe the inline-assign popover + absent-names blocks

**Files:**
- Modify: `src/zira_dashboard/routes/departments.py`

- [ ] **Step 1** Extract `_assign_popover_context(today, client) -> tuple[dict, list[str]]` (route-local; I/O) from the near-identical blocks at :574-596 and :859-881; have both `_render_recycling` and `_render_new_dept` call it. Behavior identical — same `unattributed_for_day` call + dict construction + roster sort.

- [ ] **Step 2** Extract `_absent_names(d) -> set[str]` from the identical try/except `attendance.full_day_absent_names(d)` blocks (:138-142, :772-776).

- [ ] **Step 3: Run + commit**

Run: full `pytest -q` → 524 passed.

```bash
git add src/zira_dashboard/routes/departments.py
git commit -m "refactor(recycling): dedupe inline-assign popover + absent-names helpers"
```

### Task 7: Fix the stale `expected_by_wc` docstring

**Files:**
- Modify: `src/zira_dashboard/assignment_windows.py:101-103`

- [ ] **Step 1** Replace the docstring line that says the route passes a closure over `staffing.effective_minutes_worked` with the truth: it passes the breaks-only `shift_config.productive_minutes_in_window` closure (changed in the 6/02 pace-goal fix); `effective_minutes_worked` is deliberately NOT used because it nets out partial time-off and would shrink the pace goal.

- [ ] **Step 2: Run + commit**

Run: full `pytest -q` → 524 passed.

```bash
git add src/zira_dashboard/assignment_windows.py
git commit -m "docs(assignment_windows): correct stale expected_by_wc docstring (breaks-only proration)"
```

---

## CHANGELOG

After each shipped commit that changes behavior surface, add a `### TIME` entry under today's date in `CHANGELOG.md` (per project convention). The Phase 1 test-only and Phase 3 docstring commits can share one entry noting the goal-math characterization net + decomposition.

## Notes / guardrails

- **Never inline `station_target` or `productive_minutes_in_window` into `recycling_data.py`** — they stay as injected callables in the route. This is the whole point: the goal number's provenance must not change.
- **Import cycles:** `recycling_data.py` imports only `assignment_windows` (lazy, inside `compute_per_wc_expected`) + stdlib. It must NOT import `staffing`/`settings_store`/`shift_config`/`routes` at module top. The route keeps its lazy imports for I/O modules.
- **`align_to_standard`** threading into `progress_buckets` is unchanged (that math stays in the route / `progress.py`).
- Keep `_recycling_day_data`'s I/O body and the `_render_recycling` orchestration (cache, threadpool, TemplateResponse) in the route.
