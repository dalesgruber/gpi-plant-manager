# Dashboard Cumulative Buckets, Range Goal Lines, In-Bar Labels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three small dashboard fixes — (1) align 15-min progress buckets to the global shift grid in multi-day ranges so custom-hours days don't double up at adjacent times, (2) show per-WC vertical goal lines on Pallets-by-Work-Center bars in range mode, (3) render the actual unit count inside each bar on both progress charts.

**Architecture:** Surgical extensions of existing chart infrastructure. One new kwarg on `progress_buckets()`, one boolean flip in `_bars()`, template label changes in two duplicated macros, matching CSS in two duplicated stylesheets. No new components.

**Tech Stack:** Python 3.12 / FastAPI / Jinja2 templates / vanilla CSS. Tests use pytest + FastAPI TestClient + monkeypatch + unittest.mock.patch. Test runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-05-05-dashboard-cumulative-buckets-design.md`

---

## File map

| File | Change |
|---|---|
| `src/zira_dashboard/progress.py` | Refactor `_in_any_break` signature; add `align_to_standard` kwarg to `progress_buckets()` |
| `src/zira_dashboard/routes/value_streams.py` | Pass `align_to_standard=is_range` in recycling range path; drop `not is_range` from `has_target_line` |
| `src/zira_dashboard/templates/recycling.html` | Add in-bar `<span>` to `progress_chart` macro; move existing label inside `.bar` in `cumulative_progress_chart` macro |
| `src/zira_dashboard/templates/new_vs.html` | Same `cumulative_progress_chart` change as above (macro is duplicated) |
| `src/zira_dashboard/static/recycling.css` | Add `position: relative` to `.cum-progress .bar` and `.progress .col .bar`; rewrite `.cum-progress .bar-label`; new `.progress .col .bar-label` rule |
| `src/zira_dashboard/static/new_vs.css` | Same `.cum-progress` changes as above |
| `tests/test_progress.py` (new) | Unit tests for `progress_buckets` — current behavior + `align_to_standard=True` |
| `tests/test_dashboards_polish.py` | Add range-mode tests for `target_pct` on bars and standard-bucket labels |
| `CHANGELOG.md` | Add entry for the deploy |

---

### Task 1: Create `tests/test_progress.py` with regression coverage for current behavior

This file doesn't exist yet. Add a baseline test that locks in current single-day behavior so the refactor in Task 2 and the new flag in Task 3 don't regress it.

**Files:**
- Create: `tests/test_progress.py`

- [ ] **Step 1: Write the regression test**

```python
# tests/test_progress.py
from datetime import date, datetime, time, timezone
from unittest.mock import patch

from zira_dashboard import shift_config, staffing
from zira_dashboard.leaderboard import StationTotal
from zira_dashboard.progress import progress_buckets
from zira_dashboard.stations import Station


def _station(name="Repair-1", category="Repair"):
    return Station(meter_id="m1", name=name, category=category, cell="Recycling")


def _utc(d: date, h: int, m: int) -> datetime:
    """site-local h:m -> UTC datetime (matches StationTotal.samples format)."""
    return datetime.combine(d, time(h, m), tzinfo=shift_config.SITE_TZ).astimezone(timezone.utc)


def _stationtotal(station, samples=(), active_intervals=()):
    return StationTotal(
        station=station,
        units=sum(u for _, u in samples),
        reading_count=len(samples),
        truncated=False,
        downtime_minutes=0,
        active_minutes=0,
        last_reading_at=None,
        last_status=None,
        samples=tuple(samples),
        active_intervals=tuple(active_intervals),
    )


def test_progress_buckets_default_uses_per_day_shift_start(monkeypatch):
    """Regression: default behavior anchors buckets to shift_start_for(day).
    A custom-hours day starting at 07:18 produces a first bucket labeled '07:18'.
    """
    d = date(2026, 4, 30)  # Thursday
    monkeypatch.setattr(staffing, "load_schedule", lambda day: staffing.Schedule(
        day=day, published=False,
        custom_hours={"start": "07:18", "end": "15:30", "breaks": []},
    ))
    st = _station()
    samples = [(_utc(d, 7, 25), 5)]
    active = [(_utc(d, 7, 18), _utc(d, 8, 0))]
    now = _utc(d, 8, 0)
    buckets = progress_buckets([_stationtotal(st, samples, active)], d, now)
    assert buckets, "expected at least one bucket"
    assert buckets[0]["label"] == "07:18"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_progress.py -v`
Expected: PASS — this captures current behavior.

- [ ] **Step 3: Commit**

```bash
git add tests/test_progress.py
git commit -m "test(progress): regression test for default per-day shift anchor"
```

---

### Task 2: Refactor `_in_any_break` to accept breaks iterable

The helper currently calls `breaks_for(day)` internally. Decouple it so the caller controls which breaks list to consult. This is a pure refactor — no behavior change — and it sets up Task 3.

**Files:**
- Modify: `src/zira_dashboard/progress.py`

- [ ] **Step 1: Update the helper signature and call sites**

In `src/zira_dashboard/progress.py`, replace:

```python
def _in_any_break(day: date, t: time) -> bool:
    return any(b.start <= t < b.end for b in breaks_for(day))
```

with:

```python
def _in_any_break(breaks_iter, t: time) -> bool:
    return any(b.start <= t < b.end for b in breaks_iter)
```

And inside `progress_buckets()`, before the bucket loop, capture the per-day breaks once:

```python
day_breaks = breaks_for(day)
```

Then change the line `if _in_any_break(day, b_start.time()):` to:

```python
if _in_any_break(day_breaks, b_start.time()):
```

- [ ] **Step 2: Run all progress + dashboard tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_progress.py tests/test_dashboards_polish.py -v`
Expected: PASS — refactor preserves behavior.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/progress.py
git commit -m "refactor(progress): _in_any_break takes breaks iterable explicitly"
```

---

### Task 3: Add `align_to_standard` kwarg to `progress_buckets()`

Test-first. Add a failing test for the new behavior, then implement the kwarg.

**Files:**
- Modify: `src/zira_dashboard/progress.py`
- Modify: `tests/test_progress.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_progress.py`:

```python
def test_progress_buckets_align_to_standard_uses_global_shift_start(monkeypatch):
    """With align_to_standard=True, a custom-hours day starting at 07:18
    still produces a first bucket labeled with the global shift start
    (e.g. '07:00' if that's what shift_start() returns).
    """
    d = date(2026, 4, 30)
    monkeypatch.setattr(staffing, "load_schedule", lambda day: staffing.Schedule(
        day=day, published=False,
        custom_hours={"start": "07:18", "end": "15:30", "breaks": []},
    ))
    st = _station()
    samples = [(_utc(d, 7, 25), 5)]
    active = [(_utc(d, 7, 18), _utc(d, 8, 0))]
    now = _utc(d, 8, 0)
    buckets = progress_buckets([_stationtotal(st, samples, active)], d, now, align_to_standard=True)
    assert buckets, "expected at least one bucket"
    expected_first_label = shift_config.shift_start().strftime("%H:%M")
    assert buckets[0]["label"] == expected_first_label


def test_progress_buckets_align_to_standard_sample_at_0720_lands_in_0715_bucket(monkeypatch):
    """A sample at 07:20 site-local with align_to_standard=True belongs to
    the standard '07:15' bucket [07:15, 07:30).
    """
    d = date(2026, 4, 30)
    monkeypatch.setattr(staffing, "load_schedule", lambda day: staffing.Schedule(
        day=day, published=False,
        custom_hours={"start": "07:18", "end": "15:30", "breaks": []},
    ))
    # Force global shift_start to 07:00 for predictable labels.
    monkeypatch.setattr(shift_config, "shift_start", lambda: time(7, 0))
    monkeypatch.setattr(shift_config, "shift_end", lambda: time(15, 30))
    monkeypatch.setattr(shift_config, "breaks", lambda: ())
    st = _station()
    samples = [(_utc(d, 7, 20), 5)]
    active = [(_utc(d, 7, 18), _utc(d, 8, 0))]
    now = _utc(d, 8, 0)
    buckets = progress_buckets([_stationtotal(st, samples, active)], d, now, align_to_standard=True)
    by_label = {b["label"]: b for b in buckets}
    assert "07:15" in by_label
    assert by_label["07:15"]["actual"] == 5
    # 07:00 bucket exists but has no sample — sample at 07:20 didn't land there.
    assert by_label.get("07:00", {"actual": 0})["actual"] == 0
```

- [ ] **Step 2: Run tests — verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_progress.py -v -k align_to_standard`
Expected: FAIL — `progress_buckets()` doesn't accept the `align_to_standard` kwarg yet.

- [ ] **Step 3: Implement the kwarg**

In `src/zira_dashboard/progress.py`:

Update the imports — keep the existing `_for(day)` direct imports for the default path, but add a module-level import of `shift_config` so monkeypatch tests can override the global helpers:

```python
from . import shift_config
from .shift_config import (
    SITE_TZ, breaks_for, shift_end_for, shift_start_for, work_weekdays,
)
```

(The module-level `shift_config.shift_start()` calls go through the `shift_config` module's binding, so a test that does `monkeypatch.setattr(shift_config, "shift_start", ...)` actually affects `progress.py`. Direct-imported `breaks_for` etc. keep their existing behavior — no test monkeypatches those today.)

Update the function signature and body:

```python
def progress_buckets(
    group: Iterable[StationTotal],
    day: date,
    now_utc: datetime,
    bucket_minutes: int = 15,
    target_fn: TargetFn | None = None,
    align_to_standard: bool = False,
) -> list[dict]:
    """Return one dict per 15-min bucket from shift start to min(now, shift end).

    Breaks are skipped. If ``target_fn`` is provided, the caller computes each
    bucket's target — useful when the route knows about staffing and wants to
    apply rules (first-60-min staffing-based, transfer-rule afterwards) that
    this module on its own can't know about. Otherwise, falls back to the
    default per-station active-interval calculation.

    When ``align_to_standard`` is True, anchor bucket boundaries to the
    GLOBAL shift hours (shift_start/shift_end/breaks) rather than the
    per-day custom-hours-aware variants. Used by the recycling route in
    multi-day range mode so all days share a common 15-min grid.
    """
    group = list(group)
    if not group or day.weekday() not in work_weekdays():
        return []

    # All samples, converted to site-local time.
    samples: list[tuple[datetime, int]] = []
    for st in group:
        for ts_utc, units in st.samples:
            samples.append((ts_utc.astimezone(SITE_TZ), units))

    if align_to_standard:
        s_start = shift_config.shift_start()
        s_end = shift_config.shift_end()
        day_breaks = shift_config.breaks()
    else:
        s_start = shift_start_for(day)
        s_end = shift_end_for(day)
        day_breaks = breaks_for(day)

    start = datetime.combine(day, s_start, tzinfo=SITE_TZ)
    end = datetime.combine(day, s_end, tzinfo=SITE_TZ)
    edge = min(now_utc.astimezone(SITE_TZ), end)
    if edge <= start:
        return []

    buckets: list[dict] = []
    cursor = start
    delta = timedelta(minutes=bucket_minutes)
    while cursor < edge:
        b_start = cursor
        b_end = cursor + delta
        cursor = b_end
        if _in_any_break(day_breaks, b_start.time()):
            continue
        actual = sum(u for ts, u in samples if b_start <= ts < b_end)
        in_progress = b_end > edge
        if in_progress:
            tgt = actual
        elif target_fn is not None:
            tgt = target_fn(b_start, b_end)
        else:
            tgt = _default_target(group, b_start, b_end)
        buckets.append(
            {
                "label": b_start.strftime("%H:%M"),
                "actual": actual,
                "target": int(round(tgt)),
                "in_progress": in_progress,
            }
        )
    return buckets
```

- [ ] **Step 4: Run tests — verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_progress.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/progress.py tests/test_progress.py
git commit -m "feat(progress): align_to_standard kwarg for shared 15-min grid in ranges"
```

---

### Task 4: Wire recycling route to pass `align_to_standard=is_range`

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py` — `_recycling_day_data()`

`_recycling_day_data` is currently called from the recycling route in a loop over days. It doesn't know whether the caller is in range mode. Two options: thread an `align_to_standard` arg through, or pass it in the kwargs. Use the explicit-arg approach.

- [ ] **Step 1: Add `align_to_standard` parameter to `_recycling_day_data`**

In `src/zira_dashboard/routes/value_streams.py`, change the function signature:

```python
def _recycling_day_data(d, now, is_today_d, align_to_standard=False):
```

And pass it through both `progress_buckets` calls (look for `dism_buckets = progress_buckets(...)` and `repair_buckets = progress_buckets(...)`):

```python
dism_buckets = progress_buckets(
    dismantlers, d, now,
    target_fn=_make_target_fn(dismantlers),
    align_to_standard=align_to_standard,
)
repair_buckets = progress_buckets(
    repairs, d, now,
    target_fn=_make_target_fn(repairs),
    align_to_standard=align_to_standard,
)
```

- [ ] **Step 2: Pass `align_to_standard=is_range` from the route handler**

In the same file, in the `recycling()` route, find the line:

```python
per_day = [_recycling_day_data(d, now, d == today) for d in days]
```

Change it to:

```python
per_day = [_recycling_day_data(d, now, d == today, align_to_standard=is_range) for d in days]
```

(`is_range` is already defined a few lines above as `is_range = (start_d != end_d)`.)

- [ ] **Step 3: Add a route-level test**

Append to `tests/test_dashboards_polish.py`:

```python
def test_recycling_range_progress_uses_standard_buckets(monkeypatch):
    """Multi-day range with one custom-hours day produces standard 15-min
    bucket labels (e.g. '07:15') — not the custom shift-start time.
    """
    from datetime import date as _date
    from zira_dashboard import shift_config

    def _sched(day):
        if day == _date(2026, 4, 30):
            return staffing.Schedule(
                day=day, published=True,
                assignments={"Repair-1": ["Alice"]},
                custom_hours={"start": "07:18", "end": "15:30", "breaks": []},
            )
        return staffing.Schedule(
            day=day, published=True,
            assignments={"Repair-1": ["Alice"]},
        )

    monkeypatch.setattr(staffing, "load_schedule", _sched)
    monkeypatch.setattr(shift_config, "shift_start", lambda: time(7, 0))
    monkeypatch.setattr(shift_config, "shift_end", lambda: time(15, 30))
    monkeypatch.setattr(shift_config, "breaks", lambda: ())

    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb:
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=10, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        html = client.get("/recycling?start=2026-04-29&end=2026-04-30").text

    # When range mode anchors to global 07:00, the chart includes that label
    # (the very first bucket) — and crucially does NOT include '07:18'.
    assert ">07:00<" in html or "07:00" in html
    assert "07:18" not in html
```

Add `from datetime import time` to the test file's imports if it's not already there.

- [ ] **Step 4: Run the new test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py::test_recycling_range_progress_uses_standard_buckets -v`
Expected: PASS.

- [ ] **Step 5: Run the full dashboard test suite to ensure no regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py tests/test_progress.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py tests/test_dashboards_polish.py
git commit -m "feat(recycling): standard-aligned 15-min buckets in multi-day ranges"
```

---

### Task 5: Show goal lines on Pallets-by-WC bars in range mode

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py` — inside `_bars()` helper of `recycling()` route

- [ ] **Step 1: Add a failing route test**

Append to `tests/test_dashboards_polish.py`:

```python
def test_recycling_range_shows_target_line_on_bars(monkeypatch):
    """In multi-day range mode, the per-WC bar's target tick line is shown
    (target_pct is non-null in the rendered HTML's bar-target-line div).
    """
    from datetime import date as _date

    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"]},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb, \
         patch("zira_dashboard.routes.value_streams.shift_elapsed_minutes", return_value=480):
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=100, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=480, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=(
                             (datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc),
                              datetime(2026, 4, 30, 20, 0, tzinfo=timezone.utc)),
                         )),
        ]
        client = TestClient(app)
        html = client.get("/recycling?start=2026-04-29&end=2026-04-30").text

    assert "bar-target-line" in html, "expected bar-target-line on at least one bar in range mode"
```

- [ ] **Step 2: Run test — verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py::test_recycling_range_shows_target_line_on_bars -v`
Expected: FAIL — current code suppresses target lines in range mode.

- [ ] **Step 3: Apply the one-line fix**

In `src/zira_dashboard/routes/value_streams.py`, find inside `_bars()`:

```python
        # Hide target tick line for multi-day ranges: it represents "where you should be by now"
        # which only makes sense for an in-progress single day.
        has_target_line = (max_e > 0) and not is_range
```

Replace with:

```python
        has_target_line = (max_e > 0)
```

- [ ] **Step 4: Run test — verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py::test_recycling_range_shows_target_line_on_bars -v`
Expected: PASS.

- [ ] **Step 5: Run the full dashboard test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py tests/test_dashboards_polish.py
git commit -m "feat(recycling): show per-WC goal line on bars in range mode"
```

---

### Task 6: Add in-bar unit label to the 15-min progress chart

**Files:**
- Modify: `src/zira_dashboard/templates/recycling.html` — inside `progress_chart` macro
- Modify: `src/zira_dashboard/static/recycling.css` — add `.progress .col .bar` positioning + label rule

- [ ] **Step 1: Update the `progress_chart` macro**

In `src/zira_dashboard/templates/recycling.html`, find the macro body (look for `{% macro progress_chart`). Change the per-bucket column block from:

```jinja
            <div class="col {% if hit %}hit{% else %}{% if b.in_progress %}hit{% else %}miss{% endif %}{% endif %} {% if b.in_progress %}in-progress{% endif %}"
                 title="{{ b.label }} · {{ b.actual }} pallets (goal {{ b.target }})">
              <div class="bar" style="height: {{ h }}%"></div>
              {% if not b.in_progress and b.target %}<div class="target-tick" style="bottom: {{ t_h }}%"></div>{% endif %}
            </div>
```

to:

```jinja
            <div class="col {% if hit %}hit{% else %}{% if b.in_progress %}hit{% else %}miss{% endif %}{% endif %} {% if b.in_progress %}in-progress{% endif %}"
                 title="{{ b.label }} · {{ b.actual }} pallets (goal {{ b.target }})">
              <div class="bar" style="height: {{ h }}%">
                {% if b.actual > 0 %}<span class="bar-label">{{ b.actual }}</span>{% endif %}
              </div>
              {% if not b.in_progress and b.target %}<div class="target-tick" style="bottom: {{ t_h }}%"></div>{% endif %}
            </div>
```

- [ ] **Step 2: Add the matching CSS**

In `src/zira_dashboard/static/recycling.css`, find the existing `.progress .col .bar` rule (around `width: 100%; border-radius: 2px 2px 0 0;`). Update it to include `position: relative`:

```css
  .progress .col .bar {
    width: 100%;
    border-radius: 2px 2px 0 0;
    position: relative;
  }
```

Then immediately after the existing `.progress .col.in-progress .bar` rule, add a new rule for the in-bar label:

```css
  .progress .col .bar-label {
    position: absolute;
    top: 2px;
    left: 0;
    right: 0;
    text-align: center;
    font-size: 0.7rem;
    color: #fff;
    pointer-events: none;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: clip;
    line-height: 1;
  }
```

- [ ] **Step 3: Run dashboard tests to confirm no template-render regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py -v`
Expected: all PASS (tests don't assert anything about the new label, but they exercise the template render path).

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/recycling.html src/zira_dashboard/static/recycling.css
git commit -m "feat(recycling): in-bar unit label on 15-min progress chart"
```

---

### Task 7: Move cumulative chart label inside the bar (both templates)

The `cumulative_progress_chart` macro is duplicated in both `recycling.html` and `new_vs.html`. Apply the same change to both files.

**Files:**
- Modify: `src/zira_dashboard/templates/recycling.html` — inside `cumulative_progress_chart` macro
- Modify: `src/zira_dashboard/templates/new_vs.html` — same macro

- [ ] **Step 1: Update both macros**

In **both** `src/zira_dashboard/templates/recycling.html` and `src/zira_dashboard/templates/new_vs.html`, find the macro body (look for `{% macro cumulative_progress_chart`). Change the per-bucket column block from:

```jinja
            <div class="col {% if hit %}hit{% else %}miss{% endif %} {% if b.in_progress %}in-progress{% endif %}"
                 title="{{ b.label }} · {{ '{:,}'.format(cum_a|int) }} cumulative (target {{ '{:,}'.format(cum_t|int) }})">
              <span class="bar-label">{{ '{:,}'.format(cum_a|int) }}</span>
              <div class="bar" style="height: {{ h }}%"></div>
            </div>
```

to:

```jinja
            <div class="col {% if hit %}hit{% else %}miss{% endif %} {% if b.in_progress %}in-progress{% endif %}"
                 title="{{ b.label }} · {{ '{:,}'.format(cum_a|int) }} cumulative (target {{ '{:,}'.format(cum_t|int) }})">
              <div class="bar" style="height: {{ h }}%">
                {% if cum_a > 0 %}<span class="bar-label">{{ '{:,}'.format(cum_a|int) }}</span>{% endif %}
              </div>
            </div>
```

(The `<span class="bar-label">` moved inside the `.bar` div, and is suppressed when the cumulative count is zero.)

- [ ] **Step 2: Smoke test the template render**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py -v`
Expected: PASS — templates still render.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/new_vs.html
git commit -m "feat(progress): cumulative chart label moves inside the bar"
```

---

### Task 8: Update `.cum-progress .bar-label` CSS in both stylesheets

The label is now a child of `.bar` rather than a sibling. Reposition it to anchor at the inner top edge of the bar fill, and make `.cum-progress .bar` a positioning ancestor.

**Files:**
- Modify: `src/zira_dashboard/static/recycling.css`
- Modify: `src/zira_dashboard/static/new_vs.css`

- [ ] **Step 1: Update `.cum-progress .bar` and `.cum-progress .bar-label` in both files**

In **both** `src/zira_dashboard/static/recycling.css` and `src/zira_dashboard/static/new_vs.css`, find the `.cum-progress .bar` rule:

```css
  .cum-progress .bar {
    width: 100%;
    background: var(--good);
    border-radius: 2px 2px 0 0;
    min-height: 1px;
  }
```

Add `position: relative` to it:

```css
  .cum-progress .bar {
    width: 100%;
    background: var(--good);
    border-radius: 2px 2px 0 0;
    min-height: 1px;
    position: relative;
  }
```

Then find the existing `.cum-progress .bar-label` rule (which currently positions the label above the bar with `bottom: calc(100% + 1px)` etc.) and replace its body with the in-bar positioning:

```css
  .cum-progress .bar-label {
    position: absolute;
    top: 2px;
    left: 0;
    right: 0;
    text-align: center;
    font-size: 0.72rem;
    color: #fff;
    pointer-events: none;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: clip;
    line-height: 1;
  }
```

- [ ] **Step 2: Smoke-test the template renders**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dashboards_polish.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/static/recycling.css src/zira_dashboard/static/new_vs.css
git commit -m "style(progress): in-bar positioning for cumulative chart label"
```

---

### Task 9: CHANGELOG entry, final test pass, push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all PASS.

- [ ] **Step 2: Add a CHANGELOG entry**

Get the current local time:

```bash
date "+%I:%M %p"
```

Edit `CHANGELOG.md`, adding a new `### TIME` entry under today's `## YYYY-MM-DD` heading (creating that heading if it doesn't exist yet). Use this format:

```markdown
### 8:30 AM

- **Three dashboard chart fixes** —
  (1) Multi-day range progress charts now anchor 15-min buckets to the global standard shift hours, so a custom-hours day starting at 7:18 no longer creates a duplicate 7:18 bar adjacent to the standard 7:15 bar — its production lands in whichever standard bucket each sample falls into.
  (2) Pallets-by-Work-Center bar charts now show the per-WC vertical goal line in multi-day range mode, using the per-WC expected production summed across the range (prorated by each day's productive intervals).
  (3) Both the 15-minute progress chart and the daily cumulative progress chart now render the actual unit count inside each bar (top-anchored), instead of the cumulative chart's previous label-above-the-bar style. Empty buckets render no label.
```

- [ ] **Step 3: Commit and push**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat(dashboards): standard buckets in ranges, range goal lines, in-bar labels

Three small fixes in the Recycling and New VS dashboards:

1. progress_buckets() gains an align_to_standard kwarg. The recycling
   route passes True in multi-day range mode so all days share a common
   15-min grid anchored to the global shift_start/end/breaks. Custom-
   hours days no longer produce duplicate near-adjacent buckets.

2. _bars() in the recycling route no longer suppresses the per-WC
   target-line tick in range mode. agg_expected[name] already prorates
   per-WC expected production by each day's productive intervals.

3. Both progress charts render the actual unit count inside each bar.
   The cumulative chart's existing label moved from above-the-bar to
   in-bar; the 15-min chart got a new in-bar label. Empty buckets
   suppress the label.

Spec: docs/superpowers/specs/2026-05-05-dashboard-cumulative-buckets-design.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

---

## Self-review checklist

- [ ] Spec section 1 (bucketing) — covered by Tasks 2, 3, 4 ✓
- [ ] Spec section 2 (goal lines) — covered by Task 5 ✓
- [ ] Spec section 3a (15-min in-bar label) — covered by Task 6 ✓
- [ ] Spec section 3b (cumulative in-bar label) — covered by Task 7 ✓
- [ ] Spec section 3c (CSS) — covered by Tasks 6 + 8 ✓
- [ ] Test plan items 1–4 — covered by Tasks 1, 3 ✓
- [ ] Test plan items 5–6 — covered by Tasks 4, 5 ✓
- [ ] Visual / manual verification — covered in Task 9 by Dale eyeballing on Railway ✓
- [ ] No placeholders / TODOs ✓
- [ ] All file paths exact, all code blocks complete ✓
