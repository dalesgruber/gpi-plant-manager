# Recycling Leaderboard TV Normalized Averages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Recycling Leaderboard TV and make normalized full-day production averages the shared personal average metric for leaderboards and employee cards.

**Architecture:** Add one pure `production_metrics.py` module for the 4-hour cutoff and full-day normalization, then route all personal production-average surfaces through it. Build the TV dashboard as a dedicated FastAPI route/template/CSS pair, and wire it into the existing TV display registry.

**Tech Stack:** FastAPI routes, Jinja templates, plain CSS, pytest, existing `production_daily`/`production_history.daily_records()` data source.

## Global Constraints

- Normalized average formula: `normalized_units = units / credited_hours * standard_full_day_hours`.
- Under 4.0 credited hours in a metric scope on a date is ignored.
- Exactly 4.0 credited hours qualifies.
- `standard_full_day_hours` comes from `shift_config.productive_minutes_per_day() / 60`.
- Visible count label is `days`, not `qualified days`, `q-days`, or `actual times`.
- YTD and L30 qualify independently at `ceil(leader_qualified_days * 0.10)`.
- Rows on the TV include the union of people who qualify in either YTD or L30.
- A non-qualifying TV cell shows `not enough days`.
- Do not change live throughput KPIs such as `/recycling` `pallets/hr/person`.
- Do not change Trophy Case awards in this pass.
- Do not change raw totals such as total units, total downtime, days absent, or days late.
- The new TV route is TV-only in v1.

---

## File Structure

- Create `src/zira_dashboard/production_metrics.py`
  - Owns normalized daily sample construction, per-person normalized averages, and Recycling Leaderboard TV data assembly.
  - Pure functions only: no DB, no request, no template imports.
- Create `tests/test_production_metrics.py`
  - Unit tests for cutoff, normalization, same-day multi-WC grouping, YTD/L30 eligibility, sorting, and ribbons.
- Modify `src/zira_dashboard/routes/leaderboards.py`
  - Replace `Avg/day` math in `averages_for_wc()` and `averages_for_group()` with `production_metrics.normalized_average_by_person()`.
  - Keep existing `Avg %` calculation path intact, but exclude people with no normalized days from Best Averages output.
- Modify `tests/test_leaderboards_avg.py`
  - Update old units/day expectations to normalized full-day expectations.
  - Add under-4-hour exclusion tests for WC and group averages.
- Modify `src/zira_dashboard/routes/people.py`
  - Load `production_history.daily_records(start_d, end_d)` once for normalized player-card averages.
  - Use shared helper for per-WC `Full-day avg` and group stat bubbles.
- Modify `src/zira_dashboard/templates/player_card.html`
  - Rename pph labels to `Full-day avg`.
  - Display `pallets/day` or `per day`, not `pph`, for production averages.
- Modify `tests/test_player_card_stats.py`
  - Patch `production_history.daily_records`.
  - Verify normalized full-day averages and qualified day counts.
- Create `src/zira_dashboard/routes/recycling_leaderboard.py`
  - Renders `/tv/recycling-leaderboard`, resolves theme, fetches yearly records once, and passes `production_metrics.build_recycling_leaderboard()` output to the template.
- Create `src/zira_dashboard/templates/recycling_leaderboard_tv.html`
  - Dedicated TV-only Jinja template using `_tv_header.html`, `/static/tv-mode.css`, and `/static/tv-refresh.js`.
- Create `src/zira_dashboard/static/recycling_leaderboard.css`
  - TV layout and typography for the approved three-column design.
- Modify `src/zira_dashboard/app.py`
  - Include the new route module.
- Modify `src/zira_dashboard/routes/tv_displays.py`
  - Dispatch `vs_recycling_leaderboard` displays to the new render helper.
- Modify `src/zira_dashboard/tv_displays_store.py`
  - Add the new kind to validation and seed list.
- Modify `src/zira_dashboard/routes/settings.py`
  - Add `Recycling Leaderboard` to the Settings -> TVs dashboard picker.
- Modify `src/zira_dashboard/templates/_settings_tvs.html`
  - Include `vs_recycling_leaderboard` in the selected-option logic.
- Modify `src/zira_dashboard/_schema.py`
  - Update the `tv_displays.kind` CHECK constraint to include `vs_recycling_leaderboard`.
- Modify TV display route/store/schema tests.

---

### Task 1: Shared Normalized Production Metrics Helper

**Files:**
- Create: `src/zira_dashboard/production_metrics.py`
- Create: `tests/test_production_metrics.py`

**Interfaces:**
- Produces:
  - `normalized_daily_scores(records, *, wc_names, standard_full_day_hours, min_hours=4.0) -> list[dict]`
  - `normalized_average_by_person(records, *, wc_names, standard_full_day_hours, min_hours=4.0) -> list[dict]`
- Consumes:
  - Records shaped like `production_history.daily_records()`: `{day, person, wc, units, hours, downtime}`.

- [ ] **Step 1: Write failing tests for daily score cutoff and normalization**

Create `tests/test_production_metrics.py` with these initial tests:

```python
from datetime import date

from zira_dashboard import production_metrics as pm


STD_HOURS = 7.0


def rec(day, person, wc, units, hours):
    return {
        "day": day,
        "person": person,
        "wc": wc,
        "units": float(units),
        "hours": float(hours),
        "downtime": 0.0,
    }


def test_normalized_daily_scores_ignores_under_4_hours():
    rows = pm.normalized_daily_scores(
        [rec(date(2026, 7, 1), "Alice", "Repair 1", 60, 3.99)],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert rows == []


def test_normalized_daily_scores_exactly_4_hours_qualifies():
    rows = pm.normalized_daily_scores(
        [rec(date(2026, 7, 1), "Alice", "Repair 1", 80, 4.0)],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    assert rows[0]["day"] == date(2026, 7, 1)
    assert rows[0]["units"] == 80.0
    assert rows[0]["hours"] == 4.0
    assert rows[0]["normalized_units"] == 140.0


def test_normalized_daily_scores_sums_same_day_scope_before_cutoff():
    rows = pm.normalized_daily_scores(
        [
            rec(date(2026, 7, 1), "Alice", "Repair 1", 40, 2.0),
            rec(date(2026, 7, 1), "Alice", "Repair 2", 50, 3.0),
        ],
        wc_names={"Repair 1", "Repair 2"},
        standard_full_day_hours=STD_HOURS,
    )
    assert len(rows) == 1
    assert rows[0]["units"] == 90.0
    assert rows[0]["hours"] == 5.0
    assert rows[0]["normalized_units"] == 126.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_production_metrics.py -v`

Expected: FAIL with `ImportError` or `AttributeError` because `production_metrics` does not exist yet.

- [ ] **Step 3: Implement daily score and average helpers**

Create `src/zira_dashboard/production_metrics.py`:

```python
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import ceil
from calendar import month_abbr, monthrange


def normalized_daily_scores(
    records: list[dict],
    *,
    wc_names: set[str],
    standard_full_day_hours: float,
    min_hours: float = 4.0,
) -> list[dict]:
    """One normalized score per (person, day) inside a WC scope.

    Records are summed by person/day before the cutoff so split time across
    multiple WCs in the same scope counts fairly.
    """
    if standard_full_day_hours <= 0:
        return []
    scoped = [r for r in records if r.get("wc") in wc_names]
    by_person_day: dict[tuple[str, date], dict] = defaultdict(
        lambda: {"units": 0.0, "hours": 0.0}
    )
    for r in scoped:
        person = str(r["person"])
        day = r["day"]
        bucket = by_person_day[(person, day)]
        bucket["units"] += float(r.get("units") or 0.0)
        bucket["hours"] += float(r.get("hours") or 0.0)

    out: list[dict] = []
    for (person, day), totals in by_person_day.items():
        hours = totals["hours"]
        if hours < min_hours or hours <= 0:
            continue
        normalized = totals["units"] / hours * standard_full_day_hours
        out.append({
            "name": person,
            "day": day,
            "units": totals["units"],
            "hours": hours,
            "normalized_units": normalized,
        })
    out.sort(key=lambda r: (r["day"], r["name"].lower()))
    return out


def normalized_average_by_person(
    records: list[dict],
    *,
    wc_names: set[str],
    standard_full_day_hours: float,
    min_hours: float = 4.0,
) -> list[dict]:
    """Average normalized pallets/day by person for one WC/group/role scope."""
    scores = normalized_daily_scores(
        records,
        wc_names=wc_names,
        standard_full_day_hours=standard_full_day_hours,
        min_hours=min_hours,
    )
    by_person: dict[str, dict] = defaultdict(
        lambda: {"total_normalized_units": 0.0, "total_units": 0.0, "total_hours": 0.0, "days": 0}
    )
    for s in scores:
        bucket = by_person[s["name"]]
        bucket["total_normalized_units"] += s["normalized_units"]
        bucket["total_units"] += s["units"]
        bucket["total_hours"] += s["hours"]
        bucket["days"] += 1

    out: list[dict] = []
    for name, totals in by_person.items():
        days = totals["days"]
        if days <= 0:
            continue
        out.append({
            "name": name,
            "avg_units": totals["total_normalized_units"] / days,
            "days": days,
            "total_units": totals["total_units"],
            "total_hours": totals["total_hours"],
        })
    out.sort(key=lambda r: (-r["avg_units"], -r["days"], r["name"].lower()))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_production_metrics.py -v`

Expected: PASS for the three tests.

- [ ] **Step 5: Add tests for averages**

Append to `tests/test_production_metrics.py`:

```python
def test_normalized_average_by_person_averages_qualified_days():
    rows = pm.normalized_average_by_person(
        [
            rec(date(2026, 7, 1), "Alice", "Repair 1", 80, 4.0),   # 140
            rec(date(2026, 7, 2), "Alice", "Repair 1", 70, 7.0),   # 70
            rec(date(2026, 7, 3), "Alice", "Repair 1", 999, 3.0),  # ignored
        ],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert rows[0]["name"] == "Alice"
    assert rows[0]["days"] == 2
    assert rows[0]["avg_units"] == 105.0
    assert rows[0]["total_units"] == 150.0
    assert rows[0]["total_hours"] == 11.0


def test_normalized_average_by_person_sorts_by_avg_then_days_then_name():
    rows = pm.normalized_average_by_person(
        [
            rec(date(2026, 7, 1), "Bob", "Repair 1", 70, 7.0),
            rec(date(2026, 7, 1), "Anne", "Repair 1", 70, 7.0),
            rec(date(2026, 7, 2), "Anne", "Repair 1", 70, 7.0),
            rec(date(2026, 7, 1), "Cara", "Repair 1", 100, 7.0),
        ],
        wc_names={"Repair 1"},
        standard_full_day_hours=STD_HOURS,
    )
    assert [r["name"] for r in rows] == ["Cara", "Anne", "Bob"]
```

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_production_metrics.py -v`

Expected: PASS for all tests.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/production_metrics.py tests/test_production_metrics.py
git commit -m "feat: add normalized production metrics"
```

---

### Task 2: Recycling Leaderboard Data Builder

**Files:**
- Modify: `src/zira_dashboard/production_metrics.py`
- Modify: `tests/test_production_metrics.py`

**Interfaces:**
- Consumes: `normalized_daily_scores()` and `normalized_average_by_person()` from Task 1.
- Produces: `build_recycling_leaderboard(records, *, today, standard_full_day_hours, wc_role_by_name) -> dict`.

- [ ] **Step 1: Add failing tests for YTD/L30 independent qualification**

Append to `tests/test_production_metrics.py`:

```python
def test_build_recycling_leaderboard_l30_only_person_gets_ytd_not_enough_days():
    records = []
    # YTD leader has 20 repair days, so YTD threshold is 2.
    for i in range(20):
        records.append(rec(date(2026, 1, 1 + i), "YTD Leader", "Repair 1", 70, 7.0))
    # Recent person has only one YTD day, but it is inside L30. L30 leader has
    # one day too, so L30 threshold is 1 and the L30 cell qualifies.
    records.append(rec(date(2026, 7, 5), "Recent Star", "Repair 1", 140, 7.0))

    data = pm.build_recycling_leaderboard(
        records,
        today=date(2026, 7, 9),
        standard_full_day_hours=STD_HOURS,
        wc_role_by_name={"Repair 1": "Repair"},
    )

    repairs = data["roles"]["Repair"]["rows"]
    recent = next(r for r in repairs if r["name"] == "Recent Star")
    assert recent["ytd"]["eligible"] is False
    assert recent["ytd"]["label"] == "not enough days"
    assert recent["l30"]["eligible"] is True
    assert recent["l30"]["avg_units"] == 140.0
    assert recent["l30"]["days"] == 1


def test_build_recycling_leaderboard_thresholds_are_ceil_10_percent():
    records = [
        rec(date(2026, 1, day), "Leader", "Dismantler 1", 70, 7.0)
        for day in range(1, 13)
    ]
    data = pm.build_recycling_leaderboard(
        records,
        today=date(2026, 7, 9),
        standard_full_day_hours=STD_HOURS,
        wc_role_by_name={"Dismantler 1": "Dismantler"},
    )
    assert data["roles"]["Dismantler"]["thresholds"]["ytd"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_production_metrics.py::test_build_recycling_leaderboard_l30_only_person_gets_ytd_not_enough_days tests/test_production_metrics.py::test_build_recycling_leaderboard_thresholds_are_ceil_10_percent -v`

Expected: FAIL with `AttributeError: module 'zira_dashboard.production_metrics' has no attribute 'build_recycling_leaderboard'`.

- [ ] **Step 3: Implement leaderboard builder**

Append these helpers to `src/zira_dashboard/production_metrics.py`:

```python
def _threshold(rows: list[dict]) -> int:
    leader_days = max((r["days"] for r in rows), default=0)
    return ceil(leader_days * 0.10) if leader_days > 0 else 0


def _by_name(rows: list[dict]) -> dict[str, dict]:
    return {r["name"]: r for r in rows}


def _span_cell(row: dict | None, threshold: int) -> dict:
    if row is None:
        return {"eligible": False, "label": "not enough days", "avg_units": None, "days": 0}
    eligible = threshold > 0 and row["days"] >= threshold
    return {
        "eligible": eligible,
        "label": None if eligible else "not enough days",
        "avg_units": row["avg_units"] if eligible else None,
        "days": row["days"],
    }


def _role_rows(
    *,
    ytd_rows: list[dict],
    l30_rows: list[dict],
    ytd_threshold: int,
    l30_threshold: int,
) -> list[dict]:
    ytd = _by_name(ytd_rows)
    l30 = _by_name(l30_rows)
    names = {
        r["name"] for r in ytd_rows if ytd_threshold > 0 and r["days"] >= ytd_threshold
    } | {
        r["name"] for r in l30_rows if l30_threshold > 0 and r["days"] >= l30_threshold
    }
    rows: list[dict] = []
    for name in names:
        ytd_cell = _span_cell(ytd.get(name), ytd_threshold)
        l30_cell = _span_cell(l30.get(name), l30_threshold)
        rows.append({"name": name, "ytd": ytd_cell, "l30": l30_cell})

    def sort_key(row):
        if row["ytd"]["eligible"]:
            return (0, -row["ytd"]["avg_units"], -row["ytd"]["days"], row["name"].lower())
        return (1, -(row["l30"]["avg_units"] or 0.0), -row["l30"]["days"], row["name"].lower())

    rows.sort(key=sort_key)
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def _month_bounds(year: int, month: int, today: date) -> tuple[date, date]:
    end_day = monthrange(year, month)[1]
    start = date(year, month, 1)
    end = date(year, month, end_day)
    if start <= today <= end:
        end = today
    return start, end


def _add_months(d: date, months: int) -> date:
    month_index = d.year * 12 + (d.month - 1) + months
    year = month_index // 12
    month = month_index % 12 + 1
    return date(year, month, 1)


def _best_ribbon(records: list[dict], *, wc_names: set[str], standard_full_day_hours: float) -> dict | None:
    scores = normalized_daily_scores(
        records,
        wc_names=wc_names,
        standard_full_day_hours=standard_full_day_hours,
    )
    if not scores:
        return None
    scores.sort(key=lambda r: (-r["normalized_units"], -r["units"], r["name"].lower(), r["day"]))
    best = scores[0]
    return {
        "name": best["name"],
        "day": best["day"],
        "amount": best["normalized_units"],
        "days": 1,
    }


def build_recycling_leaderboard(
    records: list[dict],
    *,
    today: date,
    standard_full_day_hours: float,
    wc_role_by_name: dict[str, str],
) -> dict:
    ytd_start = date(today.year, 1, 1)
    ytd_end = today
    l30_start = today - timedelta(days=29)
    l30_end = today
    roles = {
        "Repair": {wc for wc, role in wc_role_by_name.items() if role == "Repair"},
        "Dismantler": {wc for wc, role in wc_role_by_name.items() if role == "Dismantler"},
    }

    out_roles = {}
    for role, wc_names in roles.items():
        ytd_records = [r for r in records if ytd_start <= r["day"] <= ytd_end]
        l30_records = [r for r in records if l30_start <= r["day"] <= l30_end]
        ytd_rows = normalized_average_by_person(
            ytd_records,
            wc_names=wc_names,
            standard_full_day_hours=standard_full_day_hours,
        )
        l30_rows = normalized_average_by_person(
            l30_records,
            wc_names=wc_names,
            standard_full_day_hours=standard_full_day_hours,
        )
        ytd_threshold = _threshold(ytd_rows)
        l30_threshold = _threshold(l30_rows)
        out_roles[role] = {
            "rows": _role_rows(
                ytd_rows=ytd_rows,
                l30_rows=l30_rows,
                ytd_threshold=ytd_threshold,
                l30_threshold=l30_threshold,
            ),
            "thresholds": {"ytd": ytd_threshold, "l30": l30_threshold},
        }

    ribbons = []
    current_month = date(today.year, today.month, 1)
    for offset in range(12):
        month_start = _add_months(current_month, -offset)
        start, end = _month_bounds(month_start.year, month_start.month, today)
        month_records = [r for r in records if start <= r["day"] <= end]
        ribbons.append({
            "year": month_start.year,
            "month": month_start.month,
            "month_label": month_abbr[month_start.month],
            "repair": _best_ribbon(month_records, wc_names=roles["Repair"], standard_full_day_hours=standard_full_day_hours),
            "dismantler": _best_ribbon(month_records, wc_names=roles["Dismantler"], standard_full_day_hours=standard_full_day_hours),
        })

    return {
        "ytd_start": ytd_start,
        "ytd_end": ytd_end,
        "l30_start": l30_start,
        "l30_end": l30_end,
        "roles": out_roles,
        "ribbons": ribbons,
    }
```

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_production_metrics.py -v`

Expected: PASS.

- [ ] **Step 5: Add ribbon test**

Append:

```python
def test_build_recycling_leaderboard_ribbons_use_normalized_amount():
    records = [
        rec(date(2026, 7, 2), "Short Day", "Repair 1", 80, 4.0),  # normalized 140
        rec(date(2026, 7, 3), "Full Day", "Repair 1", 100, 7.0),  # normalized 100
        rec(date(2026, 7, 4), "Tiny", "Repair 1", 200, 3.0),      # ignored
    ]
    data = pm.build_recycling_leaderboard(
        records,
        today=date(2026, 7, 9),
        standard_full_day_hours=STD_HOURS,
        wc_role_by_name={"Repair 1": "Repair"},
    )
    july = data["ribbons"][0]
    assert july["month"] == 7
    assert july["repair"]["name"] == "Short Day"
    assert july["repair"]["day"] == date(2026, 7, 2)
    assert july["repair"]["amount"] == 140.0
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_production_metrics.py -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/production_metrics.py tests/test_production_metrics.py
git commit -m "feat: build recycling leaderboard metrics"
```

---

### Task 3: Existing Best Averages Use Normalized Full-Day Avg

**Files:**
- Modify: `src/zira_dashboard/routes/leaderboards.py`
- Modify: `tests/test_leaderboards_avg.py`

**Interfaces:**
- Consumes: `production_metrics.normalized_average_by_person()`.
- Produces: existing `averages_for_wc()` and `averages_for_group()` rows with `avg_units` normalized to full-day pallets/day and `name_count` equal to qualified days.

- [ ] **Step 1: Add failing tests for normalized Avg/day**

Append to `tests/test_leaderboards_avg.py`:

```python
def test_averages_for_wc_units_normalizes_4_hour_day():
    records = [
        {"day": date(2026, 7, 1), "person": "Alice", "wc": "WC1", "units": 80, "downtime": 0.0, "hours": 4.0},
    ]
    rows = averages_for_wc(
        records,
        30.0,
        _const_productive,
        "units",
        standard_full_day_hours=7.0,
    )
    assert rows[0]["avg_units"] == 140.0
    assert rows[0]["name_count"] == 1


def test_averages_for_wc_units_excludes_under_4_hour_day():
    records = [
        {"day": date(2026, 7, 1), "person": "Alice", "wc": "WC1", "units": 200, "downtime": 0.0, "hours": 3.99},
    ]
    assert averages_for_wc(
        records,
        30.0,
        _const_productive,
        "units",
        standard_full_day_hours=7.0,
    ) == []


def test_averages_for_group_units_sums_same_day_wcs_before_cutoff():
    records = [
        {"day": date(2026, 7, 1), "person": "Alice", "wc": "Repair-1", "units": 40, "downtime": 0.0, "hours": 2.0},
        {"day": date(2026, 7, 1), "person": "Alice", "wc": "Repair-2", "units": 50, "downtime": 0.0, "hours": 3.0},
    ]
    rows = averages_for_group(
        records,
        {"Repair-1": 30.0, "Repair-2": 30.0},
        _const_productive,
        "units",
        standard_full_day_hours=7.0,
    )
    assert rows[0]["avg_units"] == 126.0
    assert rows[0]["name_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_leaderboards_avg.py::test_averages_for_wc_units_normalizes_4_hour_day tests/test_leaderboards_avg.py::test_averages_for_wc_units_excludes_under_4_hour_day tests/test_leaderboards_avg.py::test_averages_for_group_units_sums_same_day_wcs_before_cutoff -v`

Expected: FAIL because current `avg_units` uses raw `total_units / record_count`.

- [ ] **Step 3: Modify `averages_for_wc()`**

In `src/zira_dashboard/routes/leaderboards.py`, import the helper:

```python
from .. import production_metrics
```

Change the signature so the route can pass the true standard full-day hours:

```python
def averages_for_wc(
    records: list[dict],
    target_per_hour: float,
    productive_minutes_for,
    mode: str,
    *,
    standard_full_day_hours: float | None = None,
) -> list[dict]:
```

Inside `averages_for_wc()`, replace the `by_person`/`avg_units` row assembly with this structure while keeping the existing `avg_pct` loop:

```python
    rows = [r for r in records if r["units"] > 0]
    if standard_full_day_hours is None:
        standard_full_day_hours = max(
            (productive_minutes_for(r["day"]) for r in rows),
            default=0.0,
        ) / 60.0
    normalized_rows = production_metrics.normalized_average_by_person(
        rows,
        wc_names={r["wc"] for r in rows},
        standard_full_day_hours=standard_full_day_hours,
    )
    normalized_by_name = {r["name"]: r for r in normalized_rows}
    by_person: dict[str, list[dict]] = {}
    for r in rows:
        if r["person"] in normalized_by_name:
            by_person.setdefault(r["person"], []).append(r)

    out: list[dict] = []
    for person, recs in by_person.items():
        norm = normalized_by_name[person]
        pct_per_day: list[float] = []
        for r in recs:
            prod_min = productive_minutes_for(r["day"]) - r.get("excluded_minutes", 0.0)
            expected = target_per_hour * max(0.0, prod_min) / 60.0
            if expected > 0:
                pct_per_day.append(r["units"] / expected)
        avg_pct = sum(pct_per_day) / len(pct_per_day) if pct_per_day else None
        out.append({
            "name": person,
            "name_count": norm["days"],
            "avg_units": norm["avg_units"],
            "avg_pct": avg_pct,
        })
```

- [ ] **Step 4: Modify `averages_for_group()`**

Change the signature the same way:

```python
def averages_for_group(
    records: list[dict],
    target_per_hour_by_wc: dict[str, float],
    productive_minutes_for,
    mode: str,
    *,
    standard_full_day_hours: float | None = None,
) -> list[dict]:
```

Set the same fallback at the top of the function after `rows` is built:

```python
    if standard_full_day_hours is None:
        standard_full_day_hours = max(
            (productive_minutes_for(r["day"]) for r in rows),
            default=0.0,
        ) / 60.0
```

Use all `target_per_hour_by_wc.keys()` as the helper scope:

```python
    normalized_rows = production_metrics.normalized_average_by_person(
        rows,
        wc_names=set(target_per_hour_by_wc.keys()),
        standard_full_day_hours=standard_full_day_hours,
    )
```

Keep `top_wc` based on raw `recs` as today. Set:

```python
        "name_count": norm["days"],
        "avg_units": norm["avg_units"],
```

- [ ] **Step 5: Pass the standard day from the route**

In `staffing_leaderboards()`, after `_productive_minutes_cached()` is defined, add:

```python
    standard_full_day_hours = shift_config.productive_minutes_per_day() / 60.0
```

Pass it to both helper call sites:

```python
        avg_rows = averages_for_wc(
            wc_records,
            target_per_hour,
            _productive_minutes_cached,
            metric,
            standard_full_day_hours=standard_full_day_hours,
        )
```

```python
        avg_rows = averages_for_group(
            g_records,
            target_per_hour_by_wc,
            _productive_minutes_cached,
            metric,
            standard_full_day_hours=standard_full_day_hours,
        )
```

- [ ] **Step 6: Run leaderboard average tests**

Run: `pytest tests/test_leaderboards_avg.py -v`

Expected: PASS after updating older assertions where raw units/day expected values changed only when test records use non-standard hours.

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/routes/leaderboards.py tests/test_leaderboards_avg.py
git commit -m "feat: normalize leaderboard best averages"
```

---

### Task 4: Player Cards Use Full-Day Avg Instead Of PPH

**Files:**
- Modify: `src/zira_dashboard/routes/people.py`
- Modify: `src/zira_dashboard/templates/player_card.html`
- Modify: `tests/test_player_card_stats.py`

**Interfaces:**
- Consumes: `production_metrics.normalized_average_by_person()`.
- Produces:
  - Context rows with `full_day_avg` and qualified `days_worked`.
  - `group_avgs` entries with `avg_units` and `days`, not `pph`.

- [ ] **Step 1: Add failing player-card tests**

Update `tests/test_player_card_stats.py` helper `_stub_route_dependencies()` to patch daily records:

```python
    daily_records = []
    for wc_name, totals in person_data.items():
        daily_records.append({
            "day": __import__("datetime").date(2026, 7, 1),
            "person": "Test Person",
            "wc": wc_name,
            "units": totals["units"],
            "downtime": totals["downtime"],
            "hours": totals["hours"],
        })
    monkeypatch.setattr(
        production_history,
        "daily_records",
        lambda s, e: list(daily_records),
    )
```

Replace `test_avg_pph_per_wc_added_to_rows` with:

```python
def test_full_day_avg_per_wc_added_to_rows(monkeypatch):
    _stub_route_dependencies(
        monkeypatch,
        person_data={
            "Repair 1": {"units": 80.0, "downtime": 5.0, "hours": 4.0, "days_worked": 1},
        },
        registered=[],
        members_map={},
    )
    captured = {}

    def _capture(request, template, ctx):
        captured["ctx"] = ctx
        from fastapi.responses import HTMLResponse
        return HTMLResponse("ok")

    from zira_dashboard.deps import templates
    monkeypatch.setattr(templates, "TemplateResponse", _capture)

    r = _make_client().get("/staffing/people/Test Person?start=2026-07-01&end=2026-07-01")
    assert r.status_code == 200
    rows = captured["ctx"]["rows"]
    assert rows[0]["wc"] == "Repair 1"
    assert rows[0]["full_day_avg"] == 140.0
    assert rows[0]["days_worked"] == 1
```

Replace group average assertion tests so they check `avg_units` and `days`:

```python
assert group_avgs[0]["avg_units"] == 70.0
assert group_avgs[0]["days"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_player_card_stats.py -v`

Expected: FAIL because context still contains `avg_pph` and `group_avgs[].pph`.

- [ ] **Step 3: Modify `routes/people.py`**

Import:

```python
from .. import production_metrics, shift_config
```

After `person = range_out.get(name, {})`, load daily records:

```python
    metric_records_all = production_history.daily_records(start_d, end_d)
    metric_records = [r for r in metric_records_all if r["person"] == name]
    standard_full_day_hours = shift_config.productive_minutes_per_day() / 60.0
```

When building per-WC rows, replace `avg_pph` assignment:

```python
    for r in rows:
        metric = production_metrics.normalized_average_by_person(
            metric_records,
            wc_names={r["wc"]},
            standard_full_day_hours=standard_full_day_hours,
        )
        if metric:
            r["full_day_avg"] = round(metric[0]["avg_units"], 1)
            r["days_worked"] = metric[0]["days"]
        else:
            r["full_day_avg"] = None
            r["days_worked"] = 0
```

For group stat bubbles, replace pph calculation:

```python
        metric = production_metrics.normalized_average_by_person(
            metric_records,
            wc_names=wc_names,
            standard_full_day_hours=standard_full_day_hours,
        )
        mine = next((m for m in metric if m["name"] == name), None)
        if mine:
            group_avgs.append({
                "name": group_name,
                "avg_units": round(mine["avg_units"], 1),
                "days": mine["days"],
            })
```

- [ ] **Step 4: Modify `player_card.html`**

Replace group stat value:

```jinja2
<div class="lab">{{ g.name }} · Full-day avg</div>
<div class="v">{{ '{:,.1f}'.format(g.avg_units) }} <span style="font-size:0.85rem;color:var(--muted);font-weight:500">per day</span></div>
<div style="font-size:0.72rem;color:var(--muted);margin-top:0.2rem">{{ g.days }} days</div>
```

Replace table header:

```jinja2
<tr><th>Work Center</th><th class="num">Days</th><th class="num">Units</th><th class="num">Full-day avg</th><th class="num">Downtime (min)</th></tr>
```

Replace average cell:

```jinja2
<td class="num">{% if r.full_day_avg is not none %}{{ '{:,.1f}'.format(r.full_day_avg) }}{% else %}&mdash;{% endif %}</td>
```

- [ ] **Step 5: Run focused player-card tests**

Run: `pytest tests/test_player_card_stats.py tests/test_player_card.py -v`

Expected: PASS after updating old `avg_pph` assertions to `full_day_avg`.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/people.py src/zira_dashboard/templates/player_card.html tests/test_player_card_stats.py tests/test_player_card.py
git commit -m "feat: normalize player card averages"
```

---

### Task 5: Recycling Leaderboard TV Route, Template, And CSS

**Files:**
- Create: `src/zira_dashboard/routes/recycling_leaderboard.py`
- Create: `src/zira_dashboard/templates/recycling_leaderboard_tv.html`
- Create: `src/zira_dashboard/static/recycling_leaderboard.css`
- Modify: `src/zira_dashboard/app.py`
- Create: `tests/test_recycling_leaderboard_tv.py`

**Interfaces:**
- Consumes: `production_metrics.build_recycling_leaderboard()`.
- Produces:
  - `render_recycling_leaderboard_tv(request, tv_theme="dark")`.
  - `GET /tv/recycling-leaderboard`.

- [ ] **Step 1: Write failing route render test**

Create `tests/test_recycling_leaderboard_tv.py`:

```python
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from zira_dashboard.app import app


def test_tv_recycling_leaderboard_renders(monkeypatch):
    fake_data = {
        "ytd_start": date(2026, 1, 1),
        "ytd_end": date(2026, 7, 9),
        "l30_start": date(2026, 6, 10),
        "l30_end": date(2026, 7, 9),
        "roles": {
            "Repair": {
                "thresholds": {"ytd": 13, "l30": 2},
                "rows": [
                    {
                        "rank": 1,
                        "name": "Maria S.",
                        "ytd": {"eligible": True, "avg_units": 98.4, "days": 128, "label": None},
                        "l30": {"eligible": True, "avg_units": 102.2, "days": 16, "label": None},
                    },
                    {
                        "rank": 2,
                        "name": "Luis A.",
                        "ytd": {"eligible": False, "avg_units": None, "days": 8, "label": "not enough days"},
                        "l30": {"eligible": True, "avg_units": 88.2, "days": 3, "label": None},
                    },
                ],
            },
            "Dismantler": {"thresholds": {"ytd": 12, "l30": 2}, "rows": []},
        },
        "ribbons": [
            {
                "year": 2026,
                "month": 7,
                "month_label": "Jul",
                "repair": {"name": "Maria S.", "day": date(2026, 7, 2), "amount": 118.0},
                "dismantler": {"name": "Daniel M.", "day": date(2026, 7, 7), "amount": 168.0},
            }
        ],
    }
    monkeypatch.setattr(
        "zira_dashboard.routes.recycling_leaderboard._leaderboard_payload",
        lambda today: fake_data,
    )
    r = TestClient(app).get("/tv/recycling-leaderboard")
    assert r.status_code == 200
    assert 'data-tv-theme="dark"' in r.text
    assert "Leaderboard" in r.text
    assert "Maria S." in r.text
    assert "not enough days" in r.text
    assert "q-days" not in r.text
    assert "actual times" not in r.text
    assert "tv-refresh.js" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_recycling_leaderboard_tv.py -v`

Expected: FAIL because route module and app router do not exist.

- [ ] **Step 3: Create route module**

Create `src/zira_dashboard/routes/recycling_leaderboard.py`:

```python
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from .. import production_history, production_metrics, shift_config, staffing
from ..deps import templates
from ..plant_day import today as plant_today

router = APIRouter()


def _wc_role_by_name() -> dict[str, str]:
    return {
        loc.name: loc.skill
        for loc in staffing.LOCATIONS
        if loc.skill in ("Repair", "Dismantler")
    }


def _leaderboard_payload(today: date) -> dict:
    records = production_history.daily_records(date(today.year - 1, 1, 1), today)
    return production_metrics.build_recycling_leaderboard(
        records,
        today=today,
        standard_full_day_hours=shift_config.productive_minutes_per_day() / 60.0,
        wc_role_by_name=_wc_role_by_name(),
    )


def render_recycling_leaderboard_tv(
    request: Request,
    *,
    tv_theme: str = "dark",
) -> HTMLResponse:
    today = plant_today()
    data = _leaderboard_payload(today)
    return templates.TemplateResponse(
        request,
        "recycling_leaderboard_tv.html",
        {
            "tv_theme": tv_theme if tv_theme in ("light", "dark") else "dark",
            "data": data,
        },
    )


@router.get("/tv/recycling-leaderboard", response_class=HTMLResponse)
def tv_recycling_leaderboard(request: Request, theme: str | None = Query(default=None)):
    tv_theme = "light" if theme == "light" else "dark"
    return render_recycling_leaderboard_tv(request, tv_theme=tv_theme)
```

- [ ] **Step 4: Include router in `app.py`**

In the route import block, add `recycling_leaderboard`. In the router includes, add:

```python
app.include_router(recycling_leaderboard.router)
```

Place it near `departments` / `wc_dashboard`.

- [ ] **Step 5: Create template**

Create `src/zira_dashboard/templates/recycling_leaderboard_tv.html`:

```html
{% from "_tv_header.html" import tv_header %}
<!doctype html>
<html lang="en" data-tv-theme="{{ tv_theme or 'dark' }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/static/gpi-logo.png">
<title>TV · Recycling Leaderboard — GPI Plant Manager</title>
<link rel="stylesheet" href="/static/tv-mode.css?v={{ static_v('tv-mode.css') }}">
<link rel="stylesheet" href="/static/recycling_leaderboard.css?v={{ static_v('recycling_leaderboard.css') }}">
<script defer src="/static/tv-refresh.js?v={{ static_v('tv-refresh.js') }}"></script>
</head>
<body class="recycling-leaderboard-tv">
  {{ tv_header("Leaderboard", crumb="RECYCLING") }}
  <main class="rlb-main">
    <div class="rlb-range">
      <span>YTD: {{ data.ytd_start.strftime('%b %-d') }}-{{ data.ytd_end.strftime('%b %-d') }}</span>
      <span>L30: {{ data.l30_start.strftime('%b %-d') }}-{{ data.l30_end.strftime('%b %-d') }}</span>
    </div>
    <section class="rlb-grid">
      {% for role in ["Repair", "Dismantler"] %}
      {% set block = data.roles[role] %}
      <section class="rlb-panel">
        <header class="rlb-panel-head">
          <div>
            <h2>{{ "Repairs" if role == "Repair" else "Dismantlers" }}</h2>
            <p>Sorted by YTD full-day avg</p>
          </div>
          <div class="rlb-thresholds">
            <div>YTD min {{ block.thresholds.ytd }} days</div>
            <div>L30 min {{ block.thresholds.l30 }} days</div>
          </div>
        </header>
        {% if block.rows %}
        <table class="rlb-table">
          <thead><tr><th>#</th><th>Name</th><th class="num">YTD Avg</th><th class="num">L30 Avg</th></tr></thead>
          <tbody>
          {% for r in block.rows %}
            <tr>
              <td class="rank">{{ r.rank }}</td>
              <td class="name">{{ r.name }}</td>
              <td class="num">
                {% if r.ytd.eligible %}
                  <span class="score">{{ "%.1f"|format(r.ytd.avg_units) }}</span>
                  <span class="days">{{ r.ytd.days }} days</span>
                {% else %}
                  <span class="not-enough">not enough days</span>
                {% endif %}
              </td>
              <td class="num">
                {% if r.l30.eligible %}
                  <span class="score l30">{{ "%.1f"|format(r.l30.avg_units) }}</span>
                  <span class="days">{{ r.l30.days }} days</span>
                {% else %}
                  <span class="not-enough">not enough days</span>
                {% endif %}
              </td>
            </tr>
          {% endfor %}
          </tbody>
        </table>
        {% else %}
        <div class="rlb-empty">No qualified days yet.</div>
        {% endif %}
      </section>
      {% endfor %}
      <section class="rlb-panel rlb-ribbons">
        <header class="rlb-panel-head"><div><h2>Gold Ribbons</h2><p>Best single qualifying day</p></div></header>
        <div class="rlb-months">
        {% for m in data.ribbons %}
          <div class="rlb-month-row">
            <div class="month">{{ m.month_label }}</div>
            <div class="ribbon"><b>Repair</b>{% if m.repair %}<span>{{ m.repair.name }}</span><em>{{ m.repair.day.strftime('%b %-d') }} - {{ "%.0f"|format(m.repair.amount) }}</em>{% else %}<span>-</span>{% endif %}</div>
            <div class="ribbon"><b>Dism</b>{% if m.dismantler %}<span>{{ m.dismantler.name }}</span><em>{{ m.dismantler.day.strftime('%b %-d') }} - {{ "%.0f"|format(m.dismantler.amount) }}</em>{% else %}<span>-</span>{% endif %}</div>
          </div>
        {% endfor %}
        </div>
      </section>
    </section>
  </main>
</body>
</html>
```

- [ ] **Step 6: Create CSS**

Create `src/zira_dashboard/static/recycling_leaderboard.css` with these classes:

```css
.recycling-leaderboard-tv {
  background: var(--bg);
  color: var(--fg);
}
.rlb-main {
  padding: 0 clamp(16px, 2vw, 40px) clamp(14px, 1.6vh, 30px);
}
.rlb-range {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  color: var(--muted);
  font-size: clamp(0.75rem, 1vw, 1.1rem);
  margin-bottom: 0.6rem;
}
.rlb-grid {
  display: grid;
  grid-template-columns: 1.15fr 1.15fr 0.9fr;
  gap: clamp(10px, 1.2vw, 24px);
  height: calc(100vh - 7.2rem);
  min-height: 0;
}
.rlb-panel {
  min-width: 0;
  min-height: 0;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: clamp(10px, 1.2vw, 22px);
  overflow: hidden;
}
.rlb-panel-head {
  display: flex;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.8rem;
}
.rlb-panel h2 {
  margin: 0;
  color: var(--fg);
  font-size: clamp(1.4rem, 2.4vw, 3rem);
  line-height: 1;
}
.rlb-panel p {
  margin: 0.35rem 0 0;
  color: var(--muted);
  font-weight: 700;
}
.rlb-thresholds {
  color: #86efac;
  text-align: right;
  font-weight: 800;
  font-size: clamp(0.72rem, 1vw, 1.2rem);
  white-space: nowrap;
}
.rlb-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}
.rlb-table th {
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: clamp(0.65rem, 0.85vw, 1rem);
  padding: 0.35rem 0.4rem;
  border-bottom: 1px solid var(--border);
}
.rlb-table td {
  padding: clamp(0.35rem, 1vh, 0.85rem) 0.4rem;
  border-bottom: 1px solid rgba(148, 163, 184, 0.18);
}
.rlb-table .rank {
  width: 2.5rem;
  color: var(--muted);
  font-size: clamp(1.4rem, 2vw, 2.4rem);
  font-weight: 900;
}
.rlb-table .name {
  color: var(--fg);
  font-size: clamp(1rem, 1.6vw, 2rem);
  font-weight: 900;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rlb-table .num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.rlb-table .score {
  display: block;
  color: var(--fg);
  font-size: clamp(1.25rem, 2vw, 2.6rem);
  font-weight: 950;
  line-height: 1;
}
.rlb-table .score.l30 {
  color: #86efac;
}
.rlb-table .days,
.rlb-table .not-enough {
  display: block;
  margin-top: 0.25rem;
  color: var(--muted);
  font-size: clamp(0.65rem, 0.9vw, 1.05rem);
  font-weight: 800;
}
.rlb-months {
  display: grid;
  grid-template-rows: repeat(12, minmax(0, 1fr));
  gap: 0.35rem;
  height: calc(100% - 3rem);
}
.rlb-month-row {
  display: grid;
  grid-template-columns: 2.4rem 1fr 1fr;
  gap: 0.35rem;
  min-height: 0;
}
.rlb-month-row .month {
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fbbf24;
  font-weight: 950;
}
.rlb-month-row .ribbon {
  min-width: 0;
  border: 1px solid rgba(251, 191, 36, 0.28);
  background: rgba(251, 191, 36, 0.08);
  border-radius: 6px;
  padding: 0.3rem 0.4rem;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.rlb-month-row .ribbon b {
  color: #fbbf24;
  font-size: clamp(0.55rem, 0.7vw, 0.85rem);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.rlb-month-row .ribbon span {
  color: var(--fg);
  font-weight: 900;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rlb-month-row .ribbon em {
  color: var(--muted);
  font-style: normal;
  font-size: clamp(0.6rem, 0.75vw, 0.9rem);
}
.rlb-empty {
  color: var(--muted);
  font-style: italic;
}
```

- [ ] **Step 7: Run route test**

Run: `pytest tests/test_recycling_leaderboard_tv.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/routes/recycling_leaderboard.py src/zira_dashboard/templates/recycling_leaderboard_tv.html src/zira_dashboard/static/recycling_leaderboard.css src/zira_dashboard/app.py tests/test_recycling_leaderboard_tv.py
git commit -m "feat: add recycling leaderboard tv"
```

---

### Task 6: TV Display Registry And Settings Picker

**Files:**
- Modify: `src/zira_dashboard/tv_displays_store.py`
- Modify: `src/zira_dashboard/routes/tv_displays.py`
- Modify: `src/zira_dashboard/routes/settings.py`
- Modify: `src/zira_dashboard/templates/_settings_tvs.html`
- Modify: `src/zira_dashboard/_schema.py`
- Modify: `tests/test_tv_displays_store.py`
- Modify: `tests/test_tv_displays_routes.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: `render_recycling_leaderboard_tv()` from Task 5.
- Produces: TV display kind `vs_recycling_leaderboard`.

- [ ] **Step 1: Add failing registry tests**

In `tests/test_tv_displays_store.py`, add:

```python
def test_save_recycling_leaderboard_kind():
    from zira_dashboard import tv_displays_store
    row = tv_displays_store.save(
        name="st-recycling-leaderboard",
        kind="vs_recycling_leaderboard",
        wc_name=None,
        theme="dark",
    )
    assert row["kind"] == "vs_recycling_leaderboard"
    assert row["wc_name"] is None
```

Update `test_seed_defaults_if_empty_seeds_when_empty`:

```python
    assert "Recycling Leaderboard" in names
    assert len(rows) == 11
```

In `tests/test_tv_displays_routes.py`, add:

```python
def test_get_tv_recycling_leaderboard_dispatches(monkeypatch):
    from zira_dashboard.routes import recycling_leaderboard

    def _fake_render(request, *, tv_theme="dark"):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(f'<html data-tv-theme="{tv_theme}">Leaderboard</html>')

    monkeypatch.setattr(recycling_leaderboard, "render_recycling_leaderboard_tv", _fake_render)
    c = TestClient(app)
    c.post("/api/tv-displays", json={
        "name": "rt-recycling-leaderboard",
        "kind": "vs_recycling_leaderboard",
        "theme": "light",
    })
    r = c.get("/tv/rt-recycling-leaderboard")
    assert r.status_code == 200
    assert 'data-tv-theme="light"' in r.text
    assert "Leaderboard" in r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tv_displays_store.py::test_save_recycling_leaderboard_kind tests/test_tv_displays_routes.py::test_get_tv_recycling_leaderboard_dispatches -v`

Expected: FAIL because the kind is rejected.

- [ ] **Step 3: Update `tv_displays_store.py`**

At module level:

```python
_VALID_KINDS = ("vs_recycling", "vs_new", "vs_recycling_leaderboard", "wc")
```

Update `_SEED_LIST` to include:

```python
("Recycling Leaderboard", "vs_recycling_leaderboard", None),
```

Update validation:

```python
if kind not in _VALID_KINDS:
    raise ValueError(f"invalid kind: {kind}")
```

- [ ] **Step 4: Update `routes/tv_displays.py`**

Dispatch:

```python
    if kind == "vs_recycling_leaderboard":
        from . import recycling_leaderboard
        return recycling_leaderboard.render_recycling_leaderboard_tv(
            request,
            tv_theme=tv_theme,
        )
```

Update API validation:

```python
if kind not in ("vs_recycling", "vs_new", "vs_recycling_leaderboard", "wc"):
    return JSONResponse({"ok": False, "error": "kind invalid"}, status_code=400)
```

- [ ] **Step 5: Update Settings picker**

In `routes/settings.py`, add to `all_dashboards_for_picker`:

```python
{"kind": "vs_recycling_leaderboard", "ref": "", "name": "Recycling Leaderboard"},
```

In `_settings_tvs.html`, update selected logic:

```jinja2
(d.kind in ('vs_recycling','vs_new','vs_work_centers','vs_recycling_leaderboard')) or
```

- [ ] **Step 6: Update schema constraint**

In `_schema.py`, change both CHECK occurrences to:

```sql
CHECK (kind IN ('vs_recycling', 'vs_new', 'vs_recycling_leaderboard', 'wc'))
```

If the guarded `DO $$` block only adds the constraint when missing, add a compatibility step before it:

```sql
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'tv_displays_kind_check'
      AND conrelid = 'tv_displays'::regclass
  ) THEN
    ALTER TABLE tv_displays DROP CONSTRAINT tv_displays_kind_check;
  END IF;
END $$;
```

Then the existing add-when-missing block recreates the widened constraint.

- [ ] **Step 7: Run TV registry tests**

Run: `pytest tests/test_tv_displays_store.py tests/test_tv_displays_routes.py -v`

Expected: PASS when `DATABASE_URL` is set; otherwise SKIPPED for Postgres-gated tests.

- [ ] **Step 8: Run schema tests**

Run: `pytest tests/test_db.py -v`

Expected: PASS when `DATABASE_URL` is set; otherwise existing project behavior applies.

- [ ] **Step 9: Commit**

```bash
git add src/zira_dashboard/tv_displays_store.py src/zira_dashboard/routes/tv_displays.py src/zira_dashboard/routes/settings.py src/zira_dashboard/templates/_settings_tvs.html src/zira_dashboard/_schema.py tests/test_tv_displays_store.py tests/test_tv_displays_routes.py tests/test_db.py
git commit -m "feat: register recycling leaderboard tv display"
```

---

### Task 7: Final Verification And Static Guards

**Files:**
- Create: `tests/test_recycling_leaderboard_static.py`
- Modify only if failures reveal real issues: files touched in Tasks 1-6.

**Interfaces:**
- Consumes all previous tasks.
- Produces final verified feature.

- [ ] **Step 1: Add static guards**

Create `tests/test_recycling_leaderboard_static.py`:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "src/zira_dashboard/templates/recycling_leaderboard_tv.html").read_text()
CSS = (ROOT / "src/zira_dashboard/static/recycling_leaderboard.css").read_text()
PLAYER_CARD = (ROOT / "src/zira_dashboard/templates/player_card.html").read_text()


def test_tv_leaderboard_copy_uses_days_not_q_days_or_actual_times():
    assert "q-days" not in TEMPLATE
    assert "actual times" not in TEMPLATE
    assert "not enough days" in TEMPLATE


def test_tv_leaderboard_names_have_dark_mode_foreground_color():
    assert ".rlb-table .name" in CSS
    name_block = CSS[CSS.index(".rlb-table .name"):CSS.index(".rlb-table .num")]
    assert "color: var(--fg)" in name_block


def test_player_card_no_longer_labels_production_average_as_pph():
    assert "Avg (pph)" not in PLAYER_CARD
    assert ">pph<" not in PLAYER_CARD
    assert "Full-day avg" in PLAYER_CARD
```

- [ ] **Step 2: Run static guards**

Run: `pytest tests/test_recycling_leaderboard_static.py -v`

Expected: PASS.

- [ ] **Step 3: Run focused non-DB test suite**

Run:

```bash
pytest \
  tests/test_production_metrics.py \
  tests/test_leaderboards_avg.py \
  tests/test_player_card.py \
  tests/test_player_card_stats.py \
  tests/test_recycling_leaderboard_tv.py \
  tests/test_recycling_leaderboard_static.py \
  -v
```

Expected: PASS.

- [ ] **Step 4: Run lint check**

Run: `ruff check src/zira_dashboard/production_metrics.py src/zira_dashboard/routes/recycling_leaderboard.py src/zira_dashboard/routes/leaderboards.py src/zira_dashboard/routes/people.py tests/test_production_metrics.py tests/test_recycling_leaderboard_tv.py tests/test_recycling_leaderboard_static.py`

Expected: PASS.

- [ ] **Step 5: Commit static guards or verification fixes**

If Step 1 added a new file and no fixes were needed:

```bash
git add tests/test_recycling_leaderboard_static.py
git commit -m "test: guard recycling leaderboard copy"
```

If verification required code fixes, stage only the fixed files and use:

```bash
git add <fixed-files>
git commit -m "fix: stabilize recycling leaderboard verification"
```
