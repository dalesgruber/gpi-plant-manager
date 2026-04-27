# Production History, Player Cards & Leaderboards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a per-person production attribution layer (split-credit when shared), then layer three UI features on top: VS-dashboard widgets that show operator names, per-person Player Cards with date/WC filters, and Historical Leaderboards with weekly/monthly/quarterly/yearly windows.

**Architecture:** A pure-function attribution core in a new `production_history` module — takes posted-schedule assignments + per-WC Zira totals and returns `{person → {wc → {units, downtime, hours, days_worked}}}` with units/downtime split equally across operators. Three new Flask/FastAPI routes (VS dashboard widgets, Player Card pages, Leaderboards page) call into the wrapper that does Zira I/O. Tests focus on the pure core.

**Tech Stack:** Python 3.11+ • FastAPI • Jinja2 • pytest • existing `staffing.load_schedule()` and `leaderboard.leaderboard()` infrastructure.

---

## File structure

**New files:**
- `src/zira_dashboard/production_history.py` — pure attribution math + I/O wrapper around `leaderboard()`.
- `src/zira_dashboard/templates/people_index.html` — list of active operators (links to player cards).
- `src/zira_dashboard/templates/player_card.html` — single operator's stats with date-range + per-WC breakdown.
- `src/zira_dashboard/templates/leaderboards.html` — one section per WC category, window selector + metric toggle.
- `tests/test_production_history.py` — unit tests for the pure attribution layer.

**Modified files:**
- `src/zira_dashboard/app.py` — enrich `/recycling` and `/new-vs` bars with operator names; add three new routes (`/staffing/people`, `/staffing/people/<name>`, `/staffing/leaderboards`).
- `src/zira_dashboard/templates/recycling.html` — render `b.who` instead of `b.name` in widget bars.
- `src/zira_dashboard/templates/new_vs.html` — same.
- `src/zira_dashboard/templates/_staffing_subnav.html` — add People + Leaderboards links.

---

## Phase 1 — production_history.py (pure attribution core)

The whole feature pivots on this module being right. TDD it hard.

### Task 1: Skeleton module + first test

**Files:**
- Create: `src/zira_dashboard/production_history.py`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_production_history.py
from datetime import date
from zira_dashboard.production_history import attribute_for_day


def test_attribute_for_day_empty_schedule_returns_empty():
    out = attribute_for_day(
        assignments={},
        wc_totals={},
        elapsed_minutes=480,
    )
    assert out == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_attribute_for_day_empty_schedule_returns_empty -v`
Expected: ImportError ("No module named ..." or "cannot import name attribute_for_day").

- [ ] **Step 3: Write minimal implementation**

```python
# src/zira_dashboard/production_history.py
"""Per-day, per-person production attribution.

Joins published schedules (who worked where) with Zira leaderboard output
(what each WC produced) into a {person → {wc → totals}} structure used by
the VS dashboard, Player Cards, and Leaderboards features. Units and
downtime at multi-person WCs are split equally across all assigned
operators.

The pure core (`attribute_for_day`, `attribute_for_range`) takes pre-fetched
data and is fully testable. The wrappers (`attribution_for`,
`attribution_range`) call Zira and load schedules.
"""

from __future__ import annotations

from datetime import date


def attribute_for_day(
    assignments: dict[str, list[str]],
    wc_totals: dict[str, tuple[int, int]],
    elapsed_minutes: int,
) -> dict[str, dict[str, dict[str, float]]]:
    """Attribute one day's WC output to the operators on each WC.

    Args:
        assignments: {wc_name: [person_name, ...]} — from the schedule's
            assignments dict, with the time-off pseudo-key already stripped.
        wc_totals: {wc_name: (units, downtime_minutes)} — from a Zira
            leaderboard call. Missing entries (WC with no meter) are
            treated as zero output.
        elapsed_minutes: shift minutes available that day; same for everyone.

    Returns:
        {person: {wc_name: {"units": float, "downtime": float, "hours": float,
                            "days_worked": int}}}
    """
    return {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_production_history.py::test_attribute_for_day_empty_schedule_returns_empty -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "feat: add production_history module skeleton with empty-input test"
```

### Task 2: Solo operator gets full credit

**Files:**
- Modify: `src/zira_dashboard/production_history.py`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test**

```python
def test_solo_operator_gets_full_credit():
    out = attribute_for_day(
        assignments={"Repair 1": ["Christian"]},
        wc_totals={"Repair 1": (80, 12)},
        elapsed_minutes=480,
    )
    assert out == {
        "Christian": {
            "Repair 1": {
                "units": 80.0,
                "downtime": 12.0,
                "hours": 8.0,
                "days_worked": 1,
            }
        }
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_solo_operator_gets_full_credit -v`
Expected: FAIL — actual is `{}`.

- [ ] **Step 3: Implement the loop**

Replace the body of `attribute_for_day` with:

```python
    out: dict[str, dict[str, dict[str, float]]] = {}
    hours = elapsed_minutes / 60.0
    for wc_name, operators in assignments.items():
        if not operators:
            continue
        units, downtime = wc_totals.get(wc_name, (0, 0))
        n = len(operators)
        per_units = units / n
        per_downtime = downtime / n
        for person in operators:
            wc_map = out.setdefault(person, {})
            wc_map[wc_name] = {
                "units": per_units,
                "downtime": per_downtime,
                "hours": hours,
                "days_worked": 1,
            }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_production_history.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "feat: solo-operator attribution gets full credit"
```

### Task 3: Multi-operator WC splits units and downtime equally

**Files:**
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test**

```python
def test_two_operators_split_evenly():
    out = attribute_for_day(
        assignments={"Trim Saw 1": ["Iban", "Porfirio"]},
        wc_totals={"Trim Saw 1": (200, 6)},
        elapsed_minutes=480,
    )
    assert out["Iban"]["Trim Saw 1"]["units"] == 100.0
    assert out["Iban"]["Trim Saw 1"]["downtime"] == 3.0
    assert out["Porfirio"]["Trim Saw 1"]["units"] == 100.0
    assert out["Porfirio"]["Trim Saw 1"]["downtime"] == 3.0
    assert out["Iban"]["Trim Saw 1"]["days_worked"] == 1
    assert out["Porfirio"]["Trim Saw 1"]["days_worked"] == 1


def test_three_operators_split_evenly():
    out = attribute_for_day(
        assignments={"Hand Build #1": ["A", "B", "C"]},
        wc_totals={"Hand Build #1": (90, 9)},
        elapsed_minutes=480,
    )
    for n in ("A", "B", "C"):
        assert out[n]["Hand Build #1"]["units"] == 30.0
        assert out[n]["Hand Build #1"]["downtime"] == 3.0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_production_history.py -v`
Expected: PASS — the loop already divides by `n`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_production_history.py
git commit -m "test: cover multi-operator equal-split attribution"
```

### Task 4: Time-off pseudo-key is excluded from attribution

**Files:**
- Modify: `src/zira_dashboard/production_history.py`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test**

```python
from zira_dashboard.staffing import TIME_OFF_KEY


def test_time_off_excluded():
    out = attribute_for_day(
        assignments={
            "Repair 1": ["Christian"],
            TIME_OFF_KEY: ["Iban", "Lupe"],
        },
        wc_totals={"Repair 1": (80, 12)},
        elapsed_minutes=480,
    )
    assert "Christian" in out
    assert "Iban" not in out
    assert "Lupe" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_time_off_excluded -v`
Expected: FAIL — Iban + Lupe end up under `TIME_OFF_KEY` in the output.

- [ ] **Step 3: Skip the time-off entry in the loop**

In `attribute_for_day`, replace the `for wc_name, operators in assignments.items():` line with code that skips the time-off key:

```python
    from .staffing import TIME_OFF_KEY  # local import avoids circular at module load

    out: dict[str, dict[str, dict[str, float]]] = {}
    hours = elapsed_minutes / 60.0
    for wc_name, operators in assignments.items():
        if wc_name == TIME_OFF_KEY:
            continue
        if not operators:
            continue
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_production_history.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "feat: exclude time-off pseudo-key from attribution"
```

### Task 5: WC with no Zira data still credits days_worked

**Files:**
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test**

```python
def test_unmetered_wc_credits_day_but_zero_units():
    # Hand Build has no meter_id, so no entry in wc_totals.
    out = attribute_for_day(
        assignments={"Hand Build #1": ["Lupe", "Carlos"]},
        wc_totals={},  # empty — no Zira data for this WC
        elapsed_minutes=480,
    )
    assert out["Lupe"]["Hand Build #1"]["units"] == 0.0
    assert out["Lupe"]["Hand Build #1"]["downtime"] == 0.0
    assert out["Lupe"]["Hand Build #1"]["days_worked"] == 1
    assert out["Carlos"]["Hand Build #1"]["days_worked"] == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_production_history.py::test_unmetered_wc_credits_day_but_zero_units -v`
Expected: PASS — `wc_totals.get(wc_name, (0, 0))` already handles this.

- [ ] **Step 3: Commit**

```bash
git add tests/test_production_history.py
git commit -m "test: unmetered WCs still credit day_worked"
```

### Task 6: attribute_for_range accumulates totals + days

**Files:**
- Modify: `src/zira_dashboard/production_history.py`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test**

```python
from zira_dashboard.production_history import attribute_for_range


def test_range_sums_units_and_days():
    day1 = {
        "Christian": {"Repair 1": {"units": 80.0, "downtime": 12.0, "hours": 8.0, "days_worked": 1}},
    }
    day2 = {
        "Christian": {"Repair 1": {"units": 95.0, "downtime": 5.0,  "hours": 8.0, "days_worked": 1}},
    }
    day3 = {
        "Christian": {"Repair 4": {"units": 70.0, "downtime": 0.0, "hours": 8.0, "days_worked": 1}},
        "Adrian":    {"Repair 1": {"units": 75.0, "downtime": 8.0, "hours": 8.0, "days_worked": 1}},
    }
    out = attribute_for_range([day1, day2, day3])
    assert out["Christian"]["Repair 1"]["units"] == 175.0
    assert out["Christian"]["Repair 1"]["days_worked"] == 2
    assert out["Christian"]["Repair 4"]["days_worked"] == 1
    assert out["Adrian"]["Repair 1"]["days_worked"] == 1
    assert out["Adrian"]["Repair 1"]["units"] == 75.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_range_sums_units_and_days -v`
Expected: ImportError.

- [ ] **Step 3: Implement attribute_for_range**

Append to `production_history.py`:

```python
def attribute_for_range(
    daily_attributions: list[dict[str, dict[str, dict[str, float]]]],
) -> dict[str, dict[str, dict[str, float]]]:
    """Sum a list of per-day attribution dicts (output of attribute_for_day).

    Adds the four numeric fields per (person, wc); days_worked counts the
    number of input days that contained that (person, wc) pair.
    """
    out: dict[str, dict[str, dict[str, float]]] = {}
    for daily in daily_attributions:
        for person, wc_map in daily.items():
            person_out = out.setdefault(person, {})
            for wc_name, totals in wc_map.items():
                acc = person_out.setdefault(
                    wc_name,
                    {"units": 0.0, "downtime": 0.0, "hours": 0.0, "days_worked": 0},
                )
                acc["units"] += totals["units"]
                acc["downtime"] += totals["downtime"]
                acc["hours"] += totals["hours"]
                acc["days_worked"] += totals["days_worked"]
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_production_history.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "feat: attribute_for_range accumulates totals across days"
```

### Task 7: I/O wrappers around schedule + Zira

**Files:**
- Modify: `src/zira_dashboard/production_history.py`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test (with a fake leaderboard fn)**

```python
from datetime import date
from zira_dashboard.production_history import attribution_for


def test_attribution_for_returns_empty_for_unpublished_day(monkeypatch):
    """Drafts don't count for attribution history."""
    from zira_dashboard import staffing

    fake_sched = staffing.Schedule(
        day=date(2026, 4, 27),
        published=False,
        assignments={"Repair 1": ["Christian"]},
    )
    monkeypatch.setattr(staffing, "load_schedule", lambda d: fake_sched)
    out = attribution_for(date(2026, 4, 27), client=object())
    assert out == {}


def test_attribution_for_uses_published_assignments(monkeypatch):
    from zira_dashboard import staffing, production_history

    fake_sched = staffing.Schedule(
        day=date(2026, 4, 27),
        published=True,
        assignments={"Trim Saw 1": ["Iban", "Porfirio"]},
    )
    monkeypatch.setattr(staffing, "load_schedule", lambda d: fake_sched)

    # Stub the per-day Zira lookup so we don't hit the real API.
    def fake_wc_totals(client, day):
        return {"Trim Saw 1": (200, 6)}
    monkeypatch.setattr(production_history, "_fetch_wc_totals", fake_wc_totals)
    monkeypatch.setattr(production_history, "_elapsed_minutes_for", lambda d: 480)

    out = attribution_for(date(2026, 4, 27), client=object())
    assert out["Iban"]["Trim Saw 1"]["units"] == 100.0
    assert out["Porfirio"]["Trim Saw 1"]["units"] == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py -v`
Expected: ImportError on `attribution_for`.

- [ ] **Step 3: Implement the I/O wrappers**

Append to `production_history.py`:

```python
from datetime import datetime, timezone

from .leaderboard import leaderboard
from .shift_config import shift_elapsed_minutes
from .stations import Station


def _fetch_wc_totals(client, day: date) -> dict[str, tuple[int, int]]:
    """Returns {wc_name: (units, downtime_minutes)} for every metered WC.

    Only consults staffing.LOCATIONS and pulls the WCs that have a meter_id.
    Unmetered WCs return no entry; callers should treat missing entries as
    zero output (which is what attribute_for_day does).
    """
    from . import staffing  # local import — staffing imports leaderboard.Station

    metered = [loc for loc in staffing.LOCATIONS if loc.meter_id]
    if not metered:
        return {}
    stations = [
        Station(meter_id=loc.meter_id, name=loc.name, category=loc.skill, cell=loc.bay)
        for loc in metered
    ]
    results = leaderboard(client, stations, day)
    return {r.station.name: (r.units, r.downtime_minutes) for r in results}


def _elapsed_minutes_for(d: date) -> int:
    """Productive minutes available on day d, evaluated as of right now."""
    return shift_elapsed_minutes(d, datetime.now(timezone.utc))


def attribution_for(d: date, client) -> dict[str, dict[str, dict[str, float]]]:
    """Attribute production on a single published day. Returns {} for drafts."""
    from . import staffing
    sched = staffing.load_schedule(d)
    if not sched.published:
        return {}
    wc_totals = _fetch_wc_totals(client, d)
    elapsed = _elapsed_minutes_for(d)
    return attribute_for_day(sched.assignments, wc_totals, elapsed)


def attribution_range(
    start: date,
    end: date,
    client,
) -> dict[str, dict[str, dict[str, float]]]:
    """Sum attribution_for() across [start, end] inclusive."""
    from datetime import timedelta
    daily: list[dict] = []
    cursor = start
    while cursor <= end:
        daily.append(attribution_for(cursor, client))
        cursor += timedelta(days=1)
    return attribute_for_range(daily)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_production_history.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "feat: I/O wrappers attribution_for + attribution_range"
```

### Task 8: rank_by_category helper for leaderboards

**Files:**
- Modify: `src/zira_dashboard/production_history.py`
- Test: `tests/test_production_history.py`

- [ ] **Step 1: Write the failing test**

```python
from zira_dashboard.production_history import rank_by_category


def test_rank_by_category_filters_to_category_wcs_and_threshold():
    # range output for one window
    range_out = {
        "Christian": {"Repair 1": {"units": 480.0, "downtime": 30.0, "hours": 40.0, "days_worked": 5}},
        "Adrian":    {"Repair 1": {"units": 250.0, "downtime": 10.0, "hours": 16.0, "days_worked": 2}},  # below threshold
        "Eulogio":   {"Repair 4": {"units": 385.0, "downtime": 18.0, "hours": 40.0, "days_worked": 5}},
        "Iban":      {"Trim Saw 1": {"units": 600.0, "downtime": 12.0, "hours": 40.0, "days_worked": 5}},  # different category
    }
    expected_per_wc = {"Repair 1": 100, "Repair 4": 100}  # expected daily target

    rows = rank_by_category(
        range_out,
        category_wcs=["Repair 1", "Repair 2", "Repair 3", "Repair 4", "Repair 5"],
        expected_units_per_day_by_wc=expected_per_wc,
        min_days=3,
    )
    names = [r["name"] for r in rows]
    assert names == ["Christian", "Eulogio"]   # sorted by % of target desc
    assert "Adrian" not in names               # below 3-day threshold
    assert "Iban" not in names                 # wrong category
    # % of target: Christian = 480 / (5 days * 100) = 96%
    assert rows[0]["pct_of_target"] == 96.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_production_history.py::test_rank_by_category_filters_to_category_wcs_and_threshold -v`
Expected: ImportError.

- [ ] **Step 3: Implement rank_by_category**

Append to `production_history.py`:

```python
def rank_by_category(
    range_attribution: dict[str, dict[str, dict[str, float]]],
    category_wcs: list[str],
    expected_units_per_day_by_wc: dict[str, int],
    min_days: int = 3,
) -> list[dict]:
    """Build a leaderboard for one WC category.

    Each row has: name, units (sum within the category), downtime,
    days_worked (unique days the person worked any WC in the category),
    pct_of_target (sum_units / sum_expected * 100, or None if expected is 0).
    Rows are sorted by pct_of_target desc, ties broken by units desc.
    Rows below min_days are filtered out before ranking.
    """
    cat_set = set(category_wcs)
    rows: list[dict] = []
    for person, wc_map in range_attribution.items():
        units = 0.0
        downtime = 0.0
        days = 0
        expected = 0.0
        for wc_name, totals in wc_map.items():
            if wc_name not in cat_set:
                continue
            units += totals["units"]
            downtime += totals["downtime"]
            days += totals["days_worked"]
            per_day = expected_units_per_day_by_wc.get(wc_name, 0)
            expected += per_day * totals["days_worked"]
        if days < min_days:
            continue
        pct = (units / expected * 100.0) if expected > 0 else None
        rows.append({
            "name": person,
            "units": round(units, 1),
            "downtime": round(downtime, 1),
            "days_worked": days,
            "pct_of_target": round(pct, 1) if pct is not None else None,
            "expected": round(expected, 1),
        })
    rows.sort(key=lambda r: (-(r["pct_of_target"] or -1), -r["units"]))
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_production_history.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/production_history.py tests/test_production_history.py
git commit -m "feat: rank_by_category leaderboard helper"
```

---

## Phase 2 — VS dashboard widget labels

### Task 9: Add operator names to /recycling bars

**Files:**
- Modify: `src/zira_dashboard/app.py`

- [ ] **Step 1: Locate the `recycling()` route's bar building**

Open `src/zira_dashboard/app.py`. Find the `_bars` inner function inside `def recycling(...)` — it's the one that builds dicts with `name`, `units`, `expected`, etc.

- [ ] **Step 2: Add an operator-name lookup before the bar building**

Just above `def _bars(...)`, add:

```python
    # Look up who's working each WC today; falls back to draft if unpublished.
    sched_for_labels = staffing.load_schedule(d)
    who_by_wc: dict[str, str] = {}
    for wc_name, ops in sched_for_labels.assignments.items():
        if wc_name == staffing.TIME_OFF_KEY or not ops:
            continue
        who_by_wc[wc_name] = " + ".join(ops)
```

- [ ] **Step 3: Add a `who` field to each bar dict**

Inside `_bars`, in the dict literal that builds each entry, add:

```python
                    "who": who_by_wc.get(r.station.name, r.station.name),
```

- [ ] **Step 4: Verify**

Run: `pytest tests/ -v`
Expected: all existing tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(recycling): expose 'who' label for each WC bar"
```

### Task 10: Render operator names in recycling.html

**Files:**
- Modify: `src/zira_dashboard/templates/recycling.html`

- [ ] **Step 1: Update bar templates to show `b.who`**

In `recycling.html`, find the two places `b.name` is rendered inside `bar_chart` macros (vertical bars block and horizontal bars block). Replace `{{ b.name }}` with `{{ b.who if b.who else b.name }}` in BOTH the vbar-name div and the bar-row name div, and in the title attribute.

Example, the horizontal bar row title:
```html
<div class="bar-row numpos-{{ numpos }}" title="{{ b.who if b.who else b.name }} — {{ b.units }} / {{ b.expected }} expected">
```

And the name cell:
```html
<div class="name">{{ b.who if b.who else b.name }}</div>
```

Same in the vertical-bars branch (`vbar-col` title and `vbar-name` div).

- [ ] **Step 2: Manually open `/recycling` in a browser**

Refresh. With a published schedule, bars should now show "Iban + Porfirio" instead of "Trim Saw 1". With no schedule (or unpublished), they fall back to the WC name.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/recycling.html
git commit -m "feat(recycling): show operator names on widget bars"
```

### Task 11: Same for /new-vs

**Files:**
- Modify: `src/zira_dashboard/app.py`
- Modify: `src/zira_dashboard/templates/new_vs.html`

- [ ] **Step 1: Mirror the `who_by_wc` lookup in `new_vs()`**

In `app.py`'s `new_vs()` route, add the same block right above where the `bars` list is built:

```python
    sched_for_labels = staffing.load_schedule(d)
    who_by_wc: dict[str, str] = {}
    for wc_name, ops in sched_for_labels.assignments.items():
        if wc_name == staffing.TIME_OFF_KEY or not ops:
            continue
        who_by_wc[wc_name] = " + ".join(ops)
```

- [ ] **Step 2: Add `who` to each bar dict**

In the bar-building loop, add `"who": who_by_wc.get(r.station.name, r.station.name),` to each appended dict.

- [ ] **Step 3: Update `new_vs.html` to render `b.who`**

In the bar-row markup (find the `<div class="bar-row" ...>` block), change the `title` and the name `<div>` to use `{{ b.who if b.who else b.name }}`.

- [ ] **Step 4: Manually open `/new-vs`**

Refresh. WCs with operators show their names; otherwise WC name.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/app.py src/zira_dashboard/templates/new_vs.html
git commit -m "feat(new-vs): show operator names on widget bars"
```

---

## Phase 3 — Player Cards

### Task 12: People index route + template

**Files:**
- Modify: `src/zira_dashboard/app.py`
- Create: `src/zira_dashboard/templates/people_index.html`

- [ ] **Step 1: Add the route**

In `app.py`, add this route near the other `/staffing/...` GET handlers:

```python
@app.get("/staffing/people", response_class=HTMLResponse)
def staffing_people(request: Request):
    roster = staffing.load_roster()
    active = sorted([p for p in roster if p.active], key=lambda p: p.name.lower())
    return templates.TemplateResponse(
        request,
        "people_index.html",
        {"active": "people", "people": active},
    )
```

- [ ] **Step 2: Create the template**

```html
{% extends "_staffing_base.html" %}
{% block title %}People{% endblock %}

{% block styles %}
  .people-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 0.7rem; padding: 0.5rem 0; }
  .people-grid a {
    display: block; padding: 0.7rem 0.85rem;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    color: var(--fg); text-decoration: none; font-weight: 500;
  }
  .people-grid a:hover { border-color: var(--accent); }
  .people-grid .skills { color: var(--muted); font-size: 0.78rem; margin-top: 0.25rem; }
{% endblock %}

{% block content %}
<h2 style="margin:0 0 0.6rem">People · {{ people|length }} active</h2>
<div class="people-grid">
  {% for p in people %}
    {% set top = (p.skills.items() | selectattr('1', 'gt', 1) | list) | sort(attribute='1', reverse=True) %}
    <a href="/staffing/people/{{ p.name | urlencode }}">
      {{ p.name }}
      <div class="skills">
        {% if top %}{% for s, lvl in top[:3] %}{{ s }}{% if not loop.last %} · {% endif %}{% endfor %}{% else %}—{% endif %}
      </div>
    </a>
  {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 3: Manually open `/staffing/people`**

Refresh. Should show a grid of active operators with their top 3 trained skills.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py src/zira_dashboard/templates/people_index.html
git commit -m "feat(staffing): people index page"
```

### Task 13: Player card route + template

**Files:**
- Modify: `src/zira_dashboard/app.py`
- Create: `src/zira_dashboard/templates/player_card.html`

- [ ] **Step 1: Add the route**

```python
@app.get("/staffing/people/{name}", response_class=HTMLResponse)
def staffing_player_card(
    request: Request,
    name: str,
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
):
    from . import production_history
    today = datetime.now(timezone.utc).date()
    end_d = date.fromisoformat(end) if end else today
    start_d = date.fromisoformat(start) if start else (end_d - timedelta(days=29))
    range_out = production_history.attribution_range(start_d, end_d, client)
    person = range_out.get(name, {})
    rows = sorted(
        ({"wc": wc, **t} for wc, t in person.items()),
        key=lambda r: -r["units"],
    )
    total_units    = sum(r["units"] for r in rows)
    total_downtime = sum(r["downtime"] for r in rows)
    total_days     = sum(r["days_worked"] for r in rows)
    roster = {p.name: p for p in staffing.load_roster()}
    p = roster.get(name)
    skills = []
    if p:
        skills = sorted(
            ((s, lvl) for s, lvl in p.skills.items() if lvl >= 1),
            key=lambda kv: -kv[1],
        )
    return templates.TemplateResponse(
        request,
        "player_card.html",
        {
            "active": "people",
            "name": name,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
            "today": today.isoformat(),
            "rows": rows,
            "total_units": round(total_units, 1),
            "total_downtime": round(total_downtime, 1),
            "total_days": total_days,
            "skills": skills,
        },
    )
```

- [ ] **Step 2: Create the template**

```html
{% extends "_staffing_base.html" %}
{% block title %}{{ name }}{% endblock %}

{% block styles %}
  .pc-header { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: center; margin-bottom: 0.5rem; }
  .pc-header h2 { margin: 0; }
  .pc-header form { display: inline-flex; gap: 0.4rem; align-items: center; }
  .pc-header input[type=date] {
    background: var(--panel-2); color: var(--fg);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 0.3rem 0.5rem; font: inherit; font-size: 0.85rem; color-scheme: dark;
  }
  .pc-header .preset {
    color: var(--muted); text-decoration: none; font-size: 0.82rem;
    border: 1px solid var(--border); border-radius: 6px; padding: 0.25rem 0.55rem;
  }
  .pc-header .preset:hover { color: var(--fg); border-color: var(--muted); }
  .pc-skills { color: var(--muted); font-size: 0.85rem; margin-bottom: 0.75rem; }
  .pc-skills .lvl-3 { color: var(--accent); font-weight: 600; }
  .pc-skills .lvl-2 { color: var(--fg); }
  .pc-skills .lvl-1 { color: var(--warn); }
  .pc-totals { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 0.6rem; margin-bottom: 1rem; }
  .pc-totals .stat { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 0.7rem 0.9rem; }
  .pc-totals .stat .lab { color: var(--muted); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.5px; }
  .pc-totals .stat .v { font-size: 1.4rem; font-weight: 700; font-variant-numeric: tabular-nums; margin-top: 0.2rem; }
  table.pc { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  table.pc th, table.pc td { padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border); text-align: left; }
  table.pc th { color: var(--muted); font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  table.pc td.num { text-align: right; font-variant-numeric: tabular-nums; }
{% endblock %}

{% block content %}
<div class="pc-header">
  <h2>{{ name }}</h2>
  <span class="pc-skills">
    {% for s, lvl in skills %}<span class="lvl-{{ lvl }}">{{ s }}</span>{% if not loop.last %} · {% endif %}{% endfor %}
    {% if not skills %}<span style="color:var(--muted)">— no trained skills</span>{% endif %}
  </span>
  <form method="get" style="margin-left:auto">
    <label style="color:var(--muted);font-size:0.82rem">From</label>
    <input type="date" name="start" value="{{ start }}" max="{{ today }}">
    <label style="color:var(--muted);font-size:0.82rem">to</label>
    <input type="date" name="end" value="{{ end }}" max="{{ today }}">
    <button type="submit" style="background:var(--accent-dim);color:var(--accent);border:1px solid var(--accent-dim);border-radius:6px;padding:0.3rem 0.7rem;font:inherit;font-weight:600;cursor:pointer">Apply</button>
    {% set t_iso = today %}
    <a class="preset" href="?start={{ (today | string)[:7] }}-01&end={{ today }}">This month</a>
    <a class="preset" href="?start={{ today }}&end={{ today }}">Today</a>
  </form>
</div>

<div class="pc-totals">
  <div class="stat"><div class="lab">Days worked</div><div class="v">{{ total_days }}</div></div>
  <div class="stat"><div class="lab">Total units (split)</div><div class="v">{{ '{:,.0f}'.format(total_units) }}</div></div>
  <div class="stat"><div class="lab">Total downtime (min)</div><div class="v">{{ '{:,.0f}'.format(total_downtime) }}</div></div>
</div>

{% if rows %}
<table class="pc">
  <thead>
    <tr><th>Work Center</th><th class="num">Days</th><th class="num">Units</th><th class="num">Downtime (min)</th></tr>
  </thead>
  <tbody>
    {% for r in rows %}
    <tr>
      <td>{{ r.wc }}</td>
      <td class="num">{{ r.days_worked }}</td>
      <td class="num">{{ '{:,.0f}'.format(r.units) }}</td>
      <td class="num">{{ '{:,.0f}'.format(r.downtime) }}</td>
    </tr>
    {% endfor %}
  </tbody>
</table>
{% else %}
<p style="color:var(--muted);font-style:italic">No published-day production for {{ name }} in this range.</p>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Open `/staffing/people/Christian` (or any active name)**

Refresh. Should show the card with totals and per-WC breakdown.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py src/zira_dashboard/templates/player_card.html
git commit -m "feat(staffing): player card with date-range filter"
```

### Task 14: Add "People" link to the staffing subnav

**Files:**
- Modify: `src/zira_dashboard/templates/_staffing_subnav.html`

- [ ] **Step 1: Read the current subnav**

```bash
cat src/zira_dashboard/templates/_staffing_subnav.html
```

Note the existing pattern (each link has `class="{% if active == '<key>' %}active{% endif %}"`).

- [ ] **Step 2: Add the People link**

Insert a new link in the `<div class="sub-nav">`, between the existing "People Matrix" and "Past Schedules" entries:

```html
<a href="/staffing/people" class="{% if active == 'people' %}active{% endif %}">People</a>
```

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/_staffing_subnav.html
git commit -m "feat(staffing): add People link to subnav"
```

---

## Phase 4 — Leaderboards

### Task 15: Leaderboards route + window helpers

**Files:**
- Modify: `src/zira_dashboard/app.py`

- [ ] **Step 1: Add window helpers near the top of the route handlers**

In `app.py`, near the other small helpers (close to `_parse_day`), add:

```python
def _window_dates(window: str, today_d: date) -> tuple[date, date]:
    """Return (start, end) inclusive for one of: week|month|quarter|year."""
    if window == "month":
        return today_d.replace(day=1), today_d
    if window == "quarter":
        q_start_month = ((today_d.month - 1) // 3) * 3 + 1
        return today_d.replace(month=q_start_month, day=1), today_d
    if window == "year":
        return today_d.replace(month=1, day=1), today_d
    # default: week (Monday → today)
    monday = today_d - timedelta(days=today_d.weekday())
    return monday, today_d
```

- [ ] **Step 2: Add the leaderboards route**

```python
@app.get("/staffing/leaderboards", response_class=HTMLResponse)
def staffing_leaderboards(
    request: Request,
    window: str = Query(default="week"),
    metric: str = Query(default="pct"),
):
    from . import production_history
    today_d = datetime.now(timezone.utc).date()
    start_d, end_d = _window_dates(window, today_d)
    range_out = production_history.attribution_range(start_d, end_d, client)

    # Group WCs by their `skill` category and compute per-WC daily expected units.
    cats: dict[str, list[staffing.Location]] = {}
    for loc in staffing.LOCATIONS:
        cats.setdefault(loc.skill, []).append(loc)
    expected_per_day_by_wc: dict[str, int] = {}
    for loc in staffing.LOCATIONS:
        target_per_hr = settings_store.station_target(
            stations.Station(meter_id=loc.meter_id, name=loc.name, category=loc.skill, cell=loc.bay)
        )
        expected_per_day_by_wc[loc.name] = int(round(target_per_hr * 8))  # 8 productive hrs

    sections = []
    for skill_name, locs in cats.items():
        wc_names = [loc.name for loc in locs]
        rows = production_history.rank_by_category(
            range_out,
            category_wcs=wc_names,
            expected_units_per_day_by_wc=expected_per_day_by_wc,
            min_days=3,
        )
        if metric == "units":
            rows = sorted(rows, key=lambda r: -r["units"])
        sections.append({"category": skill_name, "rows": rows})
    sections.sort(key=lambda s: s["category"].lower())

    return templates.TemplateResponse(
        request,
        "leaderboards.html",
        {
            "active": "leaderboards",
            "sections": sections,
            "window": window,
            "metric": metric,
            "start": start_d.isoformat(),
            "end": end_d.isoformat(),
        },
    )
```

Above this route, near the top imports, ensure `from . import stations` is available; `stations` module is already imported as `STATIONS, Station, recycling_stations` etc. — use `stations.Station(...)` instead.

- [ ] **Step 3: Verify the route loads without 500**

Manually open `/staffing/leaderboards`. Expected: empty sections initially (no published days yet) — page should render the section headers with the empty-state message.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(staffing): leaderboards route with window helpers"
```

### Task 16: Leaderboards template

**Files:**
- Create: `src/zira_dashboard/templates/leaderboards.html`

- [ ] **Step 1: Write the template**

```html
{% extends "_staffing_base.html" %}
{% block title %}Leaderboards{% endblock %}

{% block styles %}
  .lb-toolbar { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 0.75rem; }
  .lb-toolbar .group {
    display: inline-flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
  }
  .lb-toolbar .group a {
    padding: 0.3rem 0.7rem; color: var(--muted); text-decoration: none;
    font-size: 0.82rem; font-weight: 600; border-right: 1px solid var(--border);
  }
  .lb-toolbar .group a:last-child { border-right: none; }
  .lb-toolbar .group a.active { background: var(--accent-dim); color: var(--accent); }
  .lb-toolbar .group a:hover { color: var(--fg); }
  .lb-toolbar .range { color: var(--muted); font-size: 0.82rem; margin-left: auto; }

  .lb-section { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 0.85rem 1rem; margin-bottom: 0.75rem; }
  .lb-section h3 { margin: 0 0 0.5rem; font-size: 0.95rem; font-weight: 600; }
  .lb-section h3 .sub { color: var(--muted); font-size: 0.75rem; font-weight: 400; margin-left: 0.4rem; }
  .lb-section .empty { color: var(--muted); font-style: italic; font-size: 0.85rem; }
  table.lb { width: 100%; border-collapse: collapse; }
  table.lb th, table.lb td { padding: 0.35rem 0.55rem; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.88rem; }
  table.lb th { color: var(--muted); font-size: 0.68rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  table.lb td.num { text-align: right; font-variant-numeric: tabular-nums; }
  table.lb td.rank { color: var(--muted); width: 2.5rem; font-variant-numeric: tabular-nums; }
  table.lb td.name a { color: var(--fg); text-decoration: none; }
  table.lb td.name a:hover { color: var(--accent); }
{% endblock %}

{% block content %}
<div class="lb-toolbar">
  <div class="group">
    <a href="?window=week&metric={{ metric }}"    class="{% if window == 'week'    %}active{% endif %}">Week</a>
    <a href="?window=month&metric={{ metric }}"   class="{% if window == 'month'   %}active{% endif %}">Month</a>
    <a href="?window=quarter&metric={{ metric }}" class="{% if window == 'quarter' %}active{% endif %}">Quarter</a>
    <a href="?window=year&metric={{ metric }}"    class="{% if window == 'year'    %}active{% endif %}">Year</a>
  </div>
  <div class="group">
    <a href="?window={{ window }}&metric=pct"   class="{% if metric == 'pct'   %}active{% endif %}">% of target</a>
    <a href="?window={{ window }}&metric=units" class="{% if metric == 'units' %}active{% endif %}">Raw units</a>
  </div>
  <div class="range">{{ start }} → {{ end }}</div>
</div>

{% for sec in sections %}
<div class="lb-section">
  <h3>Best {{ sec.category }}<span class="sub">— min 3 days</span></h3>
  {% if sec.rows %}
  <table class="lb">
    <thead>
      <tr>
        <th></th>
        <th>Name</th>
        <th class="num">% target</th>
        <th class="num">Units</th>
        <th class="num">Days</th>
      </tr>
    </thead>
    <tbody>
      {% for r in sec.rows %}
      <tr>
        <td class="rank">#{{ loop.index }}</td>
        <td class="name"><a href="/staffing/people/{{ r.name | urlencode }}">{{ r.name }}</a></td>
        <td class="num">{% if r.pct_of_target is not none %}{{ r.pct_of_target }}%{% else %}—{% endif %}</td>
        <td class="num">{{ '{:,.0f}'.format(r.units) }}</td>
        <td class="num">{{ r.days_worked }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% else %}
  <div class="empty">No qualifying operators yet — needs 3+ days at a {{ sec.category }} WC this {{ window }}.</div>
  {% endif %}
</div>
{% endfor %}
{% endblock %}
```

- [ ] **Step 2: Open `/staffing/leaderboards` and switch tabs**

Click Week / Month / Quarter / Year — URL updates, sections re-render. Click % of target / Raw units — sort changes. Names link to player cards.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/leaderboards.html
git commit -m "feat(staffing): leaderboards page template"
```

### Task 17: Add "Leaderboards" link to staffing subnav

**Files:**
- Modify: `src/zira_dashboard/templates/_staffing_subnav.html`

- [ ] **Step 1: Add the link**

After the "People" link added in Task 14, insert:

```html
<a href="/staffing/leaderboards" class="{% if active == 'leaderboards' %}active{% endif %}">Leaderboards</a>
```

- [ ] **Step 2: Commit**

```bash
git add src/zira_dashboard/templates/_staffing_subnav.html
git commit -m "feat(staffing): add Leaderboards link to subnav"
```

### Task 18: Verify the entire feature end-to-end

- [ ] **Step 1: Restart uvicorn so app.py changes load**

In the uvicorn terminal: Ctrl+C, then double-click `run_dashboard.bat`.

- [ ] **Step 2: Run all tests**

Run: `pytest tests/ -v`
Expected: all PASS, including the new `test_production_history.py` cases.

- [ ] **Step 3: Smoke-test the URLs**

In a browser, visit each:
- `/recycling` and `/new-vs` — bars should label operators by name on days that have a published schedule.
- `/staffing/people` — grid of operators.
- `/staffing/people/<any name>` — card with totals + per-WC table.
- `/staffing/leaderboards` — sections per category with the four window tabs and metric toggle.

- [ ] **Step 4: Commit any final tweaks**

```bash
git status
# If anything changed, commit it.
git add <files>
git commit -m "chore: polish from end-to-end smoke test"
```

---

## Self-review notes

- **Spec coverage:** all four locked decisions (equal split, pct-default + raw toggle, 3-day threshold, WC-category buckets) implemented in Tasks 2-3, 8, 8, 15-16.
- **Module boundaries:** pure attribution math (`attribute_for_day`, `attribute_for_range`, `rank_by_category`) is fully unit-tested; Zira/schedule I/O lives only in `attribution_for` / `attribution_range` and is exercised via monkeypatching.
- **No placeholder steps:** every step has either an exact command or a complete code block. Window-date math is fully spelled out, not hand-waved.
- **Type consistency:** the dict shape `{"units","downtime","hours","days_worked"}` is used identically by `attribute_for_day`, `attribute_for_range`, and `rank_by_category`. The route handlers consume `wc_map.items()` and reference these same keys.
- **Out-of-scope features called out:** daily trend chart on player card, per-WC click-to-filter on player card, file-system attribution cache. These stay deferred until v1 ships and we see real usage.
