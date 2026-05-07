# Trophy System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add monthly badges, annual trophies, and all-time GOAT awards (computed live from `daily_records` with a small `award_overrides` table for manual reassignment), surfaced on a new `/trophies` page and as a section on the player card.

**Architecture:** Single new table (`award_overrides`). Single new module (`awards.py`) exposing pure functions that query the existing `production_history.daily_records()` and apply override rows last. Two UI surfaces: `/trophies` page and a new section on `/staffing/people/{name}`. Top-nav entry duplicated across the same six templates that already hold the existing nav.

**Tech Stack:** Python 3.12 / FastAPI / Jinja2 / Postgres (psycopg2). Test runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-05-07-trophy-system-design.md`

---

## File map

| File | Change |
|---|---|
| `src/zira_dashboard/db.py` | DDL for `award_overrides`. |
| `src/zira_dashboard/awards.py` (new) | Computation engine + override layer. |
| `src/zira_dashboard/routes/trophies.py` (new) | `/trophies` page + `POST /api/awards/override`. |
| `src/zira_dashboard/templates/trophy_case.html` (new) | Trophy Case page layout. |
| `src/zira_dashboard/templates/player_card.html` | New "Trophy case" section between group avgs and per-WC table. |
| `src/zira_dashboard/routes/people.py` | Pass `awards_earned` to player card template. |
| `src/zira_dashboard/app.py` | Register the new router. |
| Top-nav templates (6 files) | Add **Trophy Case** entry. |
| `tests/test_awards.py` (new), `tests/test_trophies_route.py` (new) | Tests. |
| `CHANGELOG.md` | Entry. |

---

### Task 1: Schema — `award_overrides` table

**Files:**
- Modify: `src/zira_dashboard/db.py`

- [ ] **Step 1: Add the DDL**

In `src/zira_dashboard/db.py`, find the `_SCHEMA_DDL` string (starts around line 136). At the end of the schema string (before the closing `"""`), add:

```sql

-- Award overrides ------------------------------------------------------
-- Trophy/badge/award winners are computed live from daily_records.
-- This table stores manual reassignments + deletions; the unique
-- index ensures one override per slot.

CREATE TABLE IF NOT EXISTS award_overrides (
  id            SERIAL PRIMARY KEY,
  scope         TEXT NOT NULL,
  group_name    TEXT,
  wc_name       TEXT,
  year          INT,
  month         INT,
  position      INT NOT NULL,
  action        TEXT NOT NULL,
  name          TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  note          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS award_overrides_slot ON award_overrides
  (scope, COALESCE(group_name,''), COALESCE(wc_name,''),
   COALESCE(year,0), COALESCE(month,0), position);
```

- [ ] **Step 2: Verify the DDL parses**

```
.venv/Scripts/python.exe -c "
import zira_dashboard.db as db
assert 'CREATE TABLE IF NOT EXISTS award_overrides' in db._SCHEMA_DDL
assert 'award_overrides_slot' in db._SCHEMA_DDL
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/db.py
git commit -m "$(cat <<'EOF'
schema: add award_overrides table for trophy system

Single-table design — winners are computed live from daily_records,
overrides are the only persistent state. Unique index on the slot
key prevents duplicate overrides for the same award position.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `awards.py` engine — helpers + single-day-units family

**Files:**
- Create: `src/zira_dashboard/awards.py`
- Create: `tests/test_awards.py`

- [ ] **Step 1: Write the failing tests for the helpers + monthly_badges**

Create `tests/test_awards.py`:

```python
"""Unit tests for awards.py — computation engine.

These tests stub production_history.daily_records and
work_centers_store.members so they don't need DATABASE_URL or a
running Zira/cached_leaderboard.
"""
from __future__ import annotations

from datetime import date


def _stub_data(monkeypatch, *, records, members_map):
    """records: list of dicts (day, person, wc, units, hours, downtime)
    members_map: {group_name: [wc_name, ...]}"""
    from zira_dashboard import production_history, work_centers_store

    class _FakeLoc:
        def __init__(self, name): self.name = name

    monkeypatch.setattr(
        production_history,
        "daily_records",
        lambda s, e, c=None: [r for r in records if s <= r["day"] <= e],
    )
    monkeypatch.setattr(
        work_centers_store,
        "members",
        lambda kind, name: [_FakeLoc(n) for n in members_map.get(name, [])],
    )


def test_person_days_in_group_sums_units_and_hours_per_day(monkeypatch):
    """Per-(person, day), units and hours sum across the group's WCs."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Alice", "wc": "Repair 1",
             "units": 60.0, "hours": 4.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Alice", "wc": "Repair 2",
             "units": 40.0, "hours": 4.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Bob", "wc": "Repair 1",
             "units": 50.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1", "Repair 2"]},
    )
    from zira_dashboard import awards
    rows = awards.person_days_in_group("Repairs", date(2026, 4, 1), date(2026, 4, 1))
    by_person = {r["name"]: r for r in rows}
    assert by_person["Alice"]["units"] == 100.0
    assert by_person["Alice"]["hours"] == 8.0
    assert by_person["Bob"]["units"] == 50.0


def test_person_days_in_group_excludes_zero_unit_days(monkeypatch):
    """Days where total units == 0 are dropped (defensive — daily_records
    already filters per-WC, but a person with all zeros after summing
    shouldn't appear)."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Alice", "wc": "Repair 1",
             "units": 0.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    rows = awards.person_days_in_group("Repairs", date(2026, 4, 1), date(2026, 4, 1))
    assert rows == []


def test_monthly_badges_top_3_by_units(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "A", "wc": "Repair 1",
             "units": 100.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 5), "person": "B", "wc": "Repair 1",
             "units": 90.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 7), "person": "C", "wc": "Repair 1",
             "units": 80.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 12), "person": "D", "wc": "Repair 1",
             "units": 70.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 15), "person": "E", "wc": "Repair 1",
             "units": 60.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert [b["position"] for b in badges] == [1, 2, 3]
    assert [b["name"] for b in badges] == ["A", "B", "C"]
    assert badges[0]["units"] == 100.0


def test_monthly_badges_tiebreak_by_pph(monkeypatch):
    """Equal units — fewer hours (higher pph) ranks ahead."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Slow", "wc": "Repair 1",
             "units": 100.0, "hours": 10.0, "downtime": 0.0},
            {"day": date(2026, 4, 2), "person": "Fast", "wc": "Repair 1",
             "units": 100.0, "hours": 5.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert badges[0]["name"] == "Fast"
    assert badges[1]["name"] == "Slow"


def test_monthly_badges_tiebreak_by_name_when_pph_equal(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Bob", "wc": "Repair 1",
             "units": 100.0, "hours": 5.0, "downtime": 0.0},
            {"day": date(2026, 4, 2), "person": "Anne", "wc": "Repair 1",
             "units": 100.0, "hours": 5.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert badges[0]["name"] == "Anne"


def test_monthly_badges_only_within_month(monkeypatch):
    """Days in adjacent months are not counted."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 3, 31), "person": "X", "wc": "Repair 1",
             "units": 999.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Y", "wc": "Repair 1",
             "units": 50.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 5, 1), "person": "Z", "wc": "Repair 1",
             "units": 999.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    badges = awards.monthly_badges("Repairs", 2026, 4)
    assert [b["name"] for b in badges] == ["Y"]


def test_annual_top_days_top_3_by_units(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 1, 5), "person": "A", "wc": "Repair 1",
             "units": 100.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 6, 1), "person": "B", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 12, 31), "person": "C", "wc": "Repair 1",
             "units": 150.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 7, 7), "person": "D", "wc": "Repair 1",
             "units": 50.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    top = awards.annual_top_days("Repairs", 2026)
    assert [t["name"] for t in top] == ["B", "C", "A"]


def test_goat_returns_max_single_day(monkeypatch):
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2025, 1, 1), "person": "Old", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "New", "wc": "Repair 1",
             "units": 250.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    monkeypatch.setattr(awards, "_all_time_range", lambda: (date(2025, 1, 1), date(2026, 4, 1)))
    g = awards.goat("Repairs")
    assert g["name"] == "New"
    assert g["units"] == 250.0


def test_goat_first_to_set_on_tie(monkeypatch):
    """Equal records — earlier date holds."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2025, 6, 1), "person": "First", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 1), "person": "Tied", "wc": "Repair 1",
             "units": 200.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    monkeypatch.setattr(awards, "_all_time_range", lambda: (date(2025, 1, 1), date(2026, 12, 31)))
    g = awards.goat("Repairs")
    assert g["name"] == "First"


def test_goat_returns_none_when_no_data(monkeypatch):
    _stub_data(monkeypatch, records=[], members_map={"Repairs": ["Repair 1"]})
    from zira_dashboard import awards
    monkeypatch.setattr(awards, "_all_time_range", lambda: (date(2026, 1, 1), date(2026, 1, 1)))
    assert awards.goat("Repairs") is None
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_awards.py -v
```

Expected: 10 FAIL — `awards` module doesn't exist yet.

- [ ] **Step 3: Create `src/zira_dashboard/awards.py`**

```python
"""Trophy system computation engine.

Pure functions over `production_history.daily_records` plus a
`work_centers_store` lookup. No caching beyond what daily_records
already does (postgres-backed). Override layer is in the same module
(see Task 5).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from calendar import monthrange


def _all_time_range() -> tuple[date, date]:
    """Earliest day in zira_daily_cache (or today if empty) → today."""
    from datetime import datetime, timezone
    from . import db
    today = datetime.now(timezone.utc).date()
    rows = db.query("SELECT MIN(day) AS d FROM zira_daily_cache")
    earliest = rows[0]["d"] if rows and rows[0].get("d") else today
    return (earliest, today)


def _wc_names_for_group(group_name: str) -> set[str]:
    from . import work_centers_store
    return {loc.name for loc in work_centers_store.members("group", group_name)}


def person_days_in_group(group_name: str, start: date, end: date) -> list[dict]:
    """Returns one row per (person, day) summing units/hours across the
    group's WCs. Filters days where total units == 0.

    Each row: {"name": str, "day": date, "units": float, "hours": float}.
    """
    from . import production_history
    wc_names = _wc_names_for_group(group_name)
    if not wc_names:
        return []
    raw = production_history.daily_records(start, end, None)
    agg: dict[tuple[str, date], dict] = defaultdict(lambda: {"units": 0.0, "hours": 0.0})
    for r in raw:
        if r["wc"] not in wc_names:
            continue
        key = (r["person"], r["day"])
        agg[key]["units"] += r["units"]
        agg[key]["hours"] += r["hours"]
    return [
        {"name": person, "day": day, "units": v["units"], "hours": v["hours"]}
        for (person, day), v in agg.items()
        if v["units"] > 0
    ]


def person_days_in_wc(wc_name: str, start: date, end: date) -> list[dict]:
    """Same shape as person_days_in_group but for a single WC."""
    from . import production_history
    raw = production_history.daily_records(start, end, None)
    return [
        {"name": r["person"], "day": r["day"], "units": r["units"], "hours": r["hours"]}
        for r in raw
        if r["wc"] == wc_name and r["units"] > 0
    ]


def _rank_single_day(rows: list[dict], top_n: int) -> list[dict]:
    """Order rows by units desc, then pph desc, then name asc.
    Return top N with positions 1..N attached."""
    def _key(r):
        pph = (r["units"] / r["hours"]) if r["hours"] > 0 else 0.0
        return (-r["units"], -pph, r["name"])
    ranked = sorted(rows, key=_key)[:top_n]
    out = []
    for i, r in enumerate(ranked, start=1):
        pph = round(r["units"] / r["hours"], 1) if r["hours"] > 0 else 0.0
        out.append({
            "position": i,
            "name": r["name"],
            "day": r["day"],
            "units": r["units"],
            "pph": pph,
        })
    return out


def _month_range(year: int, month: int) -> tuple[date, date]:
    last_day = monthrange(year, month)[1]
    return (date(year, month, 1), date(year, month, last_day))


def _year_range(year: int) -> tuple[date, date]:
    return (date(year, 1, 1), date(year, 12, 31))


def monthly_badges(group_name: str, year: int, month: int) -> list[dict]:
    """Top-3 person-days in the group during [year, month]."""
    start, end = _month_range(year, month)
    rows = person_days_in_group(group_name, start, end)
    return _rank_single_day(rows, top_n=3)


def annual_top_days(group_name: str, year: int) -> list[dict]:
    """Top-3 person-days in the group during [year]."""
    start, end = _year_range(year)
    rows = person_days_in_group(group_name, start, end)
    return _rank_single_day(rows, top_n=3)


def goat(group_name: str) -> dict | None:
    """All-time best person-day in the group. Earliest day wins on tie.
    Returns {name, day, units, pph} or None when no data.
    """
    start, end = _all_time_range()
    rows = person_days_in_group(group_name, start, end)
    if not rows:
        return None
    # Sort by units desc, then day asc (earliest wins on tie), then name asc.
    rows_sorted = sorted(rows, key=lambda r: (-r["units"], r["day"], r["name"]))
    top = rows_sorted[0]
    pph = round(top["units"] / top["hours"], 1) if top["hours"] > 0 else 0.0
    return {"name": top["name"], "day": top["day"], "units": top["units"], "pph": pph}
```

- [ ] **Step 4: Run the tests**

```
.venv/Scripts/python.exe -m pytest tests/test_awards.py -v
```

Expected: 10 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/awards.py tests/test_awards.py
git commit -m "$(cat <<'EOF'
feat(awards): single-day-units engine (badges, top days, GOAT)

Three public functions over a shared person_days_in_group helper:
monthly_badges (top 3 in [year, month]), annual_top_days (top 3
in [year]), and goat (all-time best, earliest-on-tie). Pure
functions over production_history.daily_records — no caching.
Best-avg trophies and override layer come in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `awards.py` — best-avg trophies (group + WC)

**Files:**
- Modify: `src/zira_dashboard/awards.py`
- Modify: `tests/test_awards.py`

- [ ] **Step 1: Append failing tests**

At the bottom of `tests/test_awards.py`, append:

```python
def test_annual_best_avg_group_requires_30_days(monkeypatch):
    """Person with 29 days at 20pph is excluded; person with 30 days
    at 15pph wins."""
    high_pph_29 = [
        {"day": date(2026, 1, d), "person": "Sprinter", "wc": "Repair 1",
         "units": 200.0, "hours": 10.0, "downtime": 0.0}
        for d in range(1, 30)  # 29 days
    ]
    consistent_30 = [
        {"day": date(2026, 2, d), "person": "Plodder", "wc": "Repair 1",
         "units": 150.0, "hours": 10.0, "downtime": 0.0}
        for d in range(1, 31)  # 30 days
    ]
    _stub_data(
        monkeypatch,
        records=high_pph_29 + consistent_30,
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    winner = awards.annual_best_avg_group("Repairs", 2026)
    assert winner["name"] == "Plodder"
    assert winner["days"] == 30
    assert winner["pph"] == 15.0


def test_annual_best_avg_group_returns_none_when_no_qualifier(monkeypatch):
    """Nobody hits 30 days → None."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 1, d), "person": "P", "wc": "Repair 1",
             "units": 200.0, "hours": 10.0, "downtime": 0.0}
            for d in range(1, 20)
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards
    assert awards.annual_best_avg_group("Repairs", 2026) is None


def test_annual_best_avg_wc_filters_to_single_wc(monkeypatch):
    """Days in other WCs don't count toward the 30-day floor or pph."""
    repair1_30 = [
        {"day": date(2026, 1, d), "person": "P", "wc": "Repair 1",
         "units": 100.0, "hours": 10.0, "downtime": 0.0}
        for d in range(1, 31)
    ]
    repair2_30 = [
        {"day": date(2026, 1, d), "person": "P", "wc": "Repair 2",
         "units": 50.0, "hours": 10.0, "downtime": 0.0}
        for d in range(1, 31)
    ]
    _stub_data(
        monkeypatch,
        records=repair1_30 + repair2_30,
        members_map={"Repairs": ["Repair 1", "Repair 2"]},
    )
    from zira_dashboard import awards
    w = awards.annual_best_avg_wc("Repair 1", 2026)
    assert w["pph"] == 10.0  # 100u / 10h, only Repair 1 counts
    assert w["days"] == 30
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_awards.py -v -k "best_avg"
```

Expected: 3 FAIL — functions don't exist yet.

- [ ] **Step 3: Implement the best-avg functions**

In `src/zira_dashboard/awards.py`, after the `goat()` function, append:

```python
def _rank_avg(rows: list[dict], min_days: int) -> dict | None:
    """Group rows by name, sum units/hours, count days. Filter days >= min_days.
    Highest avg pph wins. Tie-break: more days → more units → name asc.
    Returns the top {name, pph, days, units, hours} or None."""
    by_person: dict[str, dict] = defaultdict(lambda: {"units": 0.0, "hours": 0.0, "days": 0})
    for r in rows:
        if r["hours"] <= 0:
            continue  # defensive — see spec edge case 4
        d = by_person[r["name"]]
        d["units"] += r["units"]
        d["hours"] += r["hours"]
        d["days"] += 1
    qualifiers = []
    for name, v in by_person.items():
        if v["days"] < min_days or v["hours"] <= 0:
            continue
        qualifiers.append({
            "name": name,
            "pph": round(v["units"] / v["hours"], 1),
            "days": v["days"],
            "units": v["units"],
            "hours": v["hours"],
        })
    if not qualifiers:
        return None
    qualifiers.sort(key=lambda q: (-q["pph"], -q["days"], -q["units"], q["name"]))
    return qualifiers[0]


def annual_best_avg_group(group_name: str, year: int) -> dict | None:
    """Highest avg pph across the group's WCs in [year], gated days >= 30."""
    start, end = _year_range(year)
    rows = person_days_in_group(group_name, start, end)
    return _rank_avg(rows, min_days=30)


def annual_best_avg_wc(wc_name: str, year: int) -> dict | None:
    """Highest avg pph in this WC alone in [year], gated days >= 30."""
    start, end = _year_range(year)
    rows = person_days_in_wc(wc_name, start, end)
    return _rank_avg(rows, min_days=30)
```

- [ ] **Step 4: Run the tests**

```
.venv/Scripts/python.exe -m pytest tests/test_awards.py -v
```

Expected: 13 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/awards.py tests/test_awards.py
git commit -m "$(cat <<'EOF'
feat(awards): best-avg trophies (group + WC)

annual_best_avg_group and annual_best_avg_wc gate on >= 30 days.
Tie-break: more days, more units, then name. Returns None when
no qualifier.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `awards.py` — override layer + `awards_earned_by`

**Files:**
- Modify: `src/zira_dashboard/awards.py`
- Modify: `tests/test_awards.py`

- [ ] **Step 1: Append failing tests**

```python
def test_apply_overrides_replace(monkeypatch):
    """A 'replace' override swaps the name in the matching slot."""
    from zira_dashboard import awards
    slots = [
        {"position": 1, "name": "A", "day": date(2026, 4, 1), "units": 100.0, "pph": 12.5},
        {"position": 2, "name": "B", "day": date(2026, 4, 5), "units": 90.0, "pph": 11.2},
        {"position": 3, "name": "C", "day": date(2026, 4, 7), "units": 80.0, "pph": 10.0},
    ]
    overrides = [
        {"scope": "badge", "group_name": "Repairs", "wc_name": None,
         "year": 2026, "month": 4, "position": 2,
         "action": "replace", "name": "Replacement"},
    ]
    out = awards.apply_overrides(
        slots, scope="badge", group_name="Repairs", year=2026, month=4,
        overrides=overrides,
    )
    assert [s["name"] for s in out] == ["A", "Replacement", "C"]


def test_apply_overrides_delete(monkeypatch):
    from zira_dashboard import awards
    slots = [
        {"position": 1, "name": "A", "day": date(2026, 4, 1), "units": 100.0, "pph": 12.5},
        {"position": 2, "name": "B", "day": date(2026, 4, 5), "units": 90.0, "pph": 11.2},
        {"position": 3, "name": "C", "day": date(2026, 4, 7), "units": 80.0, "pph": 10.0},
    ]
    overrides = [
        {"scope": "badge", "group_name": "Repairs", "wc_name": None,
         "year": 2026, "month": 4, "position": 3,
         "action": "delete", "name": None},
    ]
    out = awards.apply_overrides(
        slots, scope="badge", group_name="Repairs", year=2026, month=4,
        overrides=overrides,
    )
    assert [s["position"] for s in out] == [1, 2]


def test_apply_overrides_passthrough_when_no_match(monkeypatch):
    from zira_dashboard import awards
    slots = [{"position": 1, "name": "A", "day": date(2026, 4, 1), "units": 100.0, "pph": 12.5}]
    overrides = [
        {"scope": "badge", "group_name": "Repairs", "wc_name": None,
         "year": 2026, "month": 5, "position": 1,
         "action": "replace", "name": "Other"},
    ]
    out = awards.apply_overrides(
        slots, scope="badge", group_name="Repairs", year=2026, month=4,
        overrides=overrides,
    )
    assert out == slots


def test_apply_overrides_handles_single_winner_scope(monkeypatch):
    """Single-value (not list) override application — for goat, best-avg trophies."""
    from zira_dashboard import awards
    slot = {"name": "A", "day": date(2026, 4, 1), "units": 200.0, "pph": 25.0}
    overrides = [
        {"scope": "award_goat", "group_name": "Repairs", "wc_name": None,
         "year": None, "month": None, "position": 1,
         "action": "replace", "name": "True GOAT"},
    ]
    out = awards.apply_overrides_single(
        slot, scope="award_goat", group_name="Repairs",
        overrides=overrides,
    )
    assert out["name"] == "True GOAT"


def test_apply_overrides_single_delete_returns_none(monkeypatch):
    from zira_dashboard import awards
    slot = {"name": "A", "day": date(2026, 4, 1), "units": 200.0, "pph": 25.0}
    overrides = [
        {"scope": "award_goat", "group_name": "Repairs", "wc_name": None,
         "year": None, "month": None, "position": 1,
         "action": "delete", "name": None},
    ]
    out = awards.apply_overrides_single(
        slot, scope="award_goat", group_name="Repairs",
        overrides=overrides,
    )
    assert out is None


def test_awards_earned_by_aggregates_across_types(monkeypatch):
    """Given fixtures producing GOAT + annual top-3 + monthly badge for
    one person, earned_by returns all entries with type/period info."""
    _stub_data(
        monkeypatch,
        records=[
            {"day": date(2026, 4, 1), "person": "Hero", "wc": "Repair 1",
             "units": 250.0, "hours": 8.0, "downtime": 0.0},
            {"day": date(2026, 4, 5), "person": "Other", "wc": "Repair 1",
             "units": 100.0, "hours": 8.0, "downtime": 0.0},
        ],
        members_map={"Repairs": ["Repair 1"]},
    )
    from zira_dashboard import awards, work_centers_store
    monkeypatch.setattr(awards, "_all_time_range", lambda: (date(2026, 1, 1), date(2026, 4, 30)))
    monkeypatch.setattr(work_centers_store, "registered_groups", lambda: ["Repairs"])
    monkeypatch.setattr(awards, "_load_overrides", lambda: [])

    earned = awards.awards_earned_by("Hero", today=date(2026, 4, 30))
    types = {e["type"] for e in earned}
    # GOAT + monthly badge for April + (no annual best-avg, < 30 days)
    assert "goat" in types
    assert any(e["type"] == "badge" and e["position"] == 1 and e["group"] == "Repairs"
               for e in earned)
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_awards.py -v -k "override or earned_by"
```

Expected: 6 FAIL.

- [ ] **Step 3: Implement override layer + earned_by**

In `src/zira_dashboard/awards.py`, append:

```python
# ---- Override layer ----------------------------------------------------

def _load_overrides() -> list[dict]:
    """Read all override rows. Cheap query — table is tiny."""
    from . import db
    return db.query(
        "SELECT scope, group_name, wc_name, year, month, position, action, name "
        "FROM award_overrides"
    )


def _override_matches(o: dict, *, scope: str, group_name: str | None = None,
                      wc_name: str | None = None, year: int | None = None,
                      month: int | None = None, position: int | None = None) -> bool:
    if o["scope"] != scope:
        return False
    if (o["group_name"] or None) != group_name:
        return False
    if (o["wc_name"] or None) != wc_name:
        return False
    if (o["year"] or None) != year:
        return False
    if (o["month"] or None) != month:
        return False
    if position is not None and o["position"] != position:
        return False
    return True


def apply_overrides(slot_list: list[dict], *, scope: str, group_name: str | None = None,
                    wc_name: str | None = None, year: int | None = None,
                    month: int | None = None, overrides: list[dict] | None = None) -> list[dict]:
    """Apply replace/delete overrides to a list of position-keyed slots.
    Slots whose position has a 'delete' override are dropped; 'replace'
    swaps the name.
    """
    if overrides is None:
        overrides = _load_overrides()
    out = []
    for s in slot_list:
        match = next(
            (o for o in overrides if _override_matches(
                o, scope=scope, group_name=group_name, wc_name=wc_name,
                year=year, month=month, position=s["position"])),
            None,
        )
        if match is None:
            out.append(s)
            continue
        if match["action"] == "delete":
            continue
        if match["action"] == "replace":
            out.append({**s, "name": match["name"]})
            continue
        out.append(s)  # unknown action — passthrough, defensive
    return out


def apply_overrides_single(slot: dict | None, *, scope: str,
                           group_name: str | None = None,
                           wc_name: str | None = None,
                           year: int | None = None,
                           month: int | None = None,
                           overrides: list[dict] | None = None) -> dict | None:
    """Single-winner version (goat, best-avg). Returns None if deleted."""
    if overrides is None:
        overrides = _load_overrides()
    match = next(
        (o for o in overrides if _override_matches(
            o, scope=scope, group_name=group_name, wc_name=wc_name,
            year=year, month=month, position=1)),
        None,
    )
    if match is None:
        return slot
    if match["action"] == "delete":
        return None
    if match["action"] == "replace":
        if slot is None:
            # Replace can resurrect a None slot — return a minimal record.
            return {"name": match["name"]}
        return {**slot, "name": match["name"]}
    return slot


# ---- Reverse lookup for player card -----------------------------------

def awards_earned_by(name: str, today: date) -> list[dict]:
    """Return every award this person currently holds.

    Each entry: {type, group, wc, year, month, position, day, units, pph, days}
    where the irrelevant keys are None. type is one of:
      'goat' | 'trophy_top_day' | 'trophy_best_avg_group' |
      'trophy_best_avg_wc' | 'badge'.
    Order: goat first, then annual trophies (newest year first),
    then monthly badges (newest month first), within group order
    from registered_groups().
    """
    from . import work_centers_store
    overrides = _load_overrides()
    earned: list[dict] = []
    groups = work_centers_store.registered_groups()

    # GOATs
    for g in groups:
        live = goat(g)
        final = apply_overrides_single(live, scope="award_goat", group_name=g, overrides=overrides)
        if final and final.get("name") == name:
            earned.append({
                "type": "goat", "group": g, "wc": None,
                "year": None, "month": None, "position": 1,
                "day": final.get("day"), "units": final.get("units"),
                "pph": final.get("pph"), "days": None,
            })

    # Annual trophies + monthly badges — start from current year, scan backward
    # to a configurable horizon. For v1: scan current year + previous 2 years.
    years = [today.year, today.year - 1, today.year - 2]
    for y in years:
        for g in groups:
            top = apply_overrides(
                annual_top_days(g, y),
                scope="trophy_top_day", group_name=g, year=y, overrides=overrides,
            )
            for s in top:
                if s["name"] == name:
                    earned.append({
                        "type": "trophy_top_day", "group": g, "wc": None,
                        "year": y, "month": None, "position": s["position"],
                        "day": s["day"], "units": s["units"], "pph": s["pph"],
                        "days": None,
                    })

            ba = apply_overrides_single(
                annual_best_avg_group(g, y),
                scope="trophy_best_avg_group", group_name=g, year=y, overrides=overrides,
            )
            if ba and ba.get("name") == name:
                earned.append({
                    "type": "trophy_best_avg_group", "group": g, "wc": None,
                    "year": y, "month": None, "position": 1,
                    "day": None, "units": ba.get("units"),
                    "pph": ba.get("pph"), "days": ba.get("days"),
                })

        # Per-WC best avg, all WCs across all groups
        seen_wcs = set()
        for g in groups:
            for wc_name in _wc_names_for_group(g):
                if wc_name in seen_wcs:
                    continue
                seen_wcs.add(wc_name)
                bw = apply_overrides_single(
                    annual_best_avg_wc(wc_name, y),
                    scope="trophy_best_avg_wc", wc_name=wc_name, year=y, overrides=overrides,
                )
                if bw and bw.get("name") == name:
                    earned.append({
                        "type": "trophy_best_avg_wc", "group": None, "wc": wc_name,
                        "year": y, "month": None, "position": 1,
                        "day": None, "units": bw.get("units"),
                        "pph": bw.get("pph"), "days": bw.get("days"),
                    })

        # Monthly badges (only for current year and prior year)
        if y >= today.year - 1:
            for m in range(12, 0, -1):
                # Skip future months in current year
                if y == today.year and m > today.month:
                    continue
                for g in groups:
                    badges = apply_overrides(
                        monthly_badges(g, y, m),
                        scope="badge", group_name=g, year=y, month=m, overrides=overrides,
                    )
                    for s in badges:
                        if s["name"] == name:
                            earned.append({
                                "type": "badge", "group": g, "wc": None,
                                "year": y, "month": m, "position": s["position"],
                                "day": s["day"], "units": s["units"], "pph": s["pph"],
                                "days": None,
                            })
    return earned
```

- [ ] **Step 4: Run the tests**

```
.venv/Scripts/python.exe -m pytest tests/test_awards.py -v
```

Expected: 19 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/awards.py tests/test_awards.py
git commit -m "$(cat <<'EOF'
feat(awards): override layer + awards_earned_by reverse lookup

apply_overrides (list-of-slots) and apply_overrides_single
(one-slot) read award_overrides and return corrected results.
awards_earned_by walks every award type for the given person and
returns a flat list ordered for the player-card display.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: `/trophies` route + page + override endpoint

**Files:**
- Create: `src/zira_dashboard/routes/trophies.py`
- Create: `src/zira_dashboard/templates/trophy_case.html`
- Modify: `src/zira_dashboard/app.py`
- Create: `tests/test_trophies_route.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_trophies_route.py`:

```python
"""Endpoint tests for /trophies and /api/awards/override."""
from __future__ import annotations

import os
import pytest
from unittest.mock import MagicMock

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; this test needs Postgres.",
)


def _client(monkeypatch):
    """Stub awards.py so the trophy case page renders without DB."""
    from fastapi.testclient import TestClient
    from zira_dashboard import awards, work_centers_store
    from zira_dashboard.app import app
    monkeypatch.setattr(awards, "monthly_badges", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_top_days", lambda *a, **k: [])
    monkeypatch.setattr(awards, "annual_best_avg_group", lambda *a, **k: None)
    monkeypatch.setattr(awards, "annual_best_avg_wc", lambda *a, **k: None)
    monkeypatch.setattr(awards, "goat", lambda *a, **k: None)
    monkeypatch.setattr(awards, "_load_overrides", lambda: [])
    monkeypatch.setattr(work_centers_store, "registered_groups", lambda: ["Repairs"])
    return TestClient(app)


def test_trophies_page_renders_with_no_data(monkeypatch):
    """Empty data: page returns 200 and the body mentions 'Trophy'."""
    r = _client(monkeypatch).get("/trophies")
    assert r.status_code == 200
    assert "Trophy" in r.text


def test_override_endpoint_replace(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard import db, awards
    from zira_dashboard.app import app

    spy = MagicMock()
    monkeypatch.setattr(db, "execute", spy)

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={
            "scope": "badge", "group_name": "Repairs",
            "year": 2026, "month": 4, "position": 1,
            "action": "replace", "name": "Replacement",
        },
    )
    assert r.status_code == 200
    spy.assert_called_once()
    sql = spy.call_args.args[0]
    assert "INSERT INTO award_overrides" in sql
    assert "ON CONFLICT" in sql


def test_override_endpoint_reset_deletes_row(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard import db
    from zira_dashboard.app import app

    spy = MagicMock()
    monkeypatch.setattr(db, "execute", spy)

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={
            "scope": "badge", "group_name": "Repairs",
            "year": 2026, "month": 4, "position": 1,
            "action": "reset",
        },
    )
    assert r.status_code == 200
    spy.assert_called_once()
    sql = spy.call_args.args[0]
    assert "DELETE FROM award_overrides" in sql


def test_override_endpoint_validates_scope(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={"scope": "not_a_real_scope", "position": 1, "action": "replace", "name": "X"},
    )
    assert r.status_code == 400


def test_override_endpoint_validates_action(monkeypatch):
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    client = TestClient(app)
    r = client.post(
        "/api/awards/override",
        json={"scope": "badge", "group_name": "Repairs", "year": 2026,
              "month": 4, "position": 1, "action": "garbage"},
    )
    assert r.status_code == 400
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_trophies_route.py -v
```

Expected: 5 FAIL (route doesn't exist yet).

- [ ] **Step 3: Create the route module**

Create `src/zira_dashboard/routes/trophies.py`:

```python
"""Trophy Case page + override endpoint."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import awards, work_centers_store
from ..deps import templates

router = APIRouter()

VALID_SCOPES = {
    "badge",
    "trophy_top_day",
    "trophy_best_avg_group",
    "trophy_best_avg_wc",
    "award_goat",
}
VALID_ACTIONS = {"replace", "delete", "reset"}


@router.get("/trophies", response_class=HTMLResponse)
def trophies_page(
    request: Request,
    year: int | None = Query(default=None),
    month: int | None = Query(default=None),
):
    today = datetime.now(timezone.utc).date()
    y = year or today.year
    m = month or today.month
    groups = work_centers_store.registered_groups()
    overrides = awards._load_overrides()

    # GOATs section — one per group
    goats = []
    for g in groups:
        live = awards.goat(g)
        final = awards.apply_overrides_single(
            live, scope="award_goat", group_name=g, overrides=overrides,
        )
        goats.append({"group": g, "winner": final})

    # Annual section — for selected year, per group
    annual = []
    for g in groups:
        top = awards.apply_overrides(
            awards.annual_top_days(g, y),
            scope="trophy_top_day", group_name=g, year=y, overrides=overrides,
        )
        ba = awards.apply_overrides_single(
            awards.annual_best_avg_group(g, y),
            scope="trophy_best_avg_group", group_name=g, year=y, overrides=overrides,
        )
        wc_winners = []
        for wc_name in sorted({loc.name for loc in work_centers_store.members("group", g)}):
            wcw = awards.apply_overrides_single(
                awards.annual_best_avg_wc(wc_name, y),
                scope="trophy_best_avg_wc", wc_name=wc_name, year=y, overrides=overrides,
            )
            if wcw:
                wc_winners.append({"wc": wc_name, "winner": wcw})
        annual.append({
            "group": g, "top_days": top, "best_avg": ba, "wc_winners": wc_winners,
        })

    # Monthly section — for selected (year, month), per group
    monthly = []
    for g in groups:
        badges = awards.apply_overrides(
            awards.monthly_badges(g, y, m),
            scope="badge", group_name=g, year=y, month=m, overrides=overrides,
        )
        monthly.append({"group": g, "badges": badges})

    return templates.TemplateResponse(
        request,
        "trophy_case.html",
        {
            "active": "trophies",
            "today": today.isoformat(),
            "year": y,
            "month": m,
            "goats": goats,
            "annual": annual,
            "monthly": monthly,
        },
    )


@router.post("/api/awards/override")
async def award_override(request: Request):
    """Body (JSON):
        {scope, group_name?, wc_name?, year?, month?, position,
         action: 'replace' | 'delete' | 'reset', name?, note?}
    """
    from .. import db
    body = await request.json()
    scope = body.get("scope")
    if scope not in VALID_SCOPES:
        return JSONResponse({"ok": False, "error": "bad scope"}, status_code=400)
    action = body.get("action")
    if action not in VALID_ACTIONS:
        return JSONResponse({"ok": False, "error": "bad action"}, status_code=400)

    group_name = body.get("group_name") or None
    wc_name = body.get("wc_name") or None
    year = body.get("year") or None
    month = body.get("month") or None
    try:
        position = int(body.get("position") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "position required (int)"}, status_code=400)
    if position < 1:
        return JSONResponse({"ok": False, "error": "position must be >= 1"}, status_code=400)

    if action == "reset":
        db.execute(
            "DELETE FROM award_overrides "
            "WHERE scope = %s AND COALESCE(group_name, '') = COALESCE(%s, '') "
            "  AND COALESCE(wc_name, '') = COALESCE(%s, '') "
            "  AND COALESCE(year, 0) = COALESCE(%s, 0) "
            "  AND COALESCE(month, 0) = COALESCE(%s, 0) "
            "  AND position = %s",
            (scope, group_name, wc_name, year, month, position),
        )
        return JSONResponse({"ok": True})

    name = body.get("name")
    if action == "replace" and not name:
        return JSONResponse({"ok": False, "error": "replace requires name"}, status_code=400)
    note = body.get("note")

    db.execute(
        "INSERT INTO award_overrides "
        "  (scope, group_name, wc_name, year, month, position, action, name, note) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (scope, COALESCE(group_name,''), COALESCE(wc_name,''), "
        "             COALESCE(year,0), COALESCE(month,0), position) "
        "DO UPDATE SET action = EXCLUDED.action, name = EXCLUDED.name, note = EXCLUDED.note, "
        "              created_at = NOW()",
        (scope, group_name, wc_name, year, month, position, action, name, note),
    )
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Create the template**

Create `src/zira_dashboard/templates/trophy_case.html`:

```jinja
{% extends "_staffing_base.html" %}
{% block title %}Trophy Case{% endblock %}

{% block styles %}
  .tc-section { margin-bottom: 2rem; }
  .tc-section h2 { margin-bottom: 0.6rem; }
  .tc-goats { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0.8rem; }
  .tc-card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; }
  .tc-card .icon { font-size: 2rem; }
  .tc-card .head { display: flex; align-items: center; gap: 0.5rem; }
  .tc-card .name { font-size: 1.2rem; font-weight: 700; }
  .tc-card .meta { color: var(--muted); font-size: 0.85rem; margin-top: 0.3rem; }
  .tc-card .empty { color: var(--muted); font-style: italic; }
  .tc-pickers { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.8rem; }
  .tc-pickers select { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); border-radius: 6px; padding: 0.3rem 0.5rem; font: inherit; }
  .tc-group-block { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; margin-bottom: 0.8rem; }
  .tc-group-block h3 { margin: 0 0 0.6rem 0; }
  .tc-row { display: flex; align-items: center; gap: 0.6rem; padding: 0.3rem 0; border-bottom: 1px solid var(--border); }
  .tc-row:last-child { border-bottom: none; }
  .tc-row .icon { font-size: 1.4rem; }
  .tc-row .name { font-weight: 600; }
  .tc-row .meta { color: var(--muted); font-size: 0.85rem; margin-left: auto; }
  .tc-edit { background: transparent; border: 1px solid var(--border); border-radius: 4px; padding: 0.1rem 0.3rem; cursor: pointer; color: var(--muted); font-size: 0.8rem; }
  .tc-edit:hover { color: var(--fg); border-color: var(--muted); }
  .tc-modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.6); display: none; align-items: center; justify-content: center; z-index: 1000; }
  .tc-modal-backdrop.active { display: flex; }
  .tc-modal { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 1.2rem; min-width: 320px; max-width: 480px; }
  .tc-modal h3 { margin-top: 0; }
  .tc-modal label { display: block; margin: 0.5rem 0 0.2rem; color: var(--muted); font-size: 0.85rem; }
  .tc-modal input, .tc-modal select, .tc-modal textarea { width: 100%; padding: 0.4rem; background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); border-radius: 6px; font: inherit; }
  .tc-modal .actions { display: flex; gap: 0.5rem; margin-top: 1rem; justify-content: flex-end; }
  .tc-modal button { padding: 0.4rem 0.9rem; border-radius: 6px; border: 1px solid var(--border); background: var(--panel-2); color: var(--fg); font: inherit; cursor: pointer; }
  .tc-modal button.primary { background: var(--accent-dim); border-color: var(--accent-dim); color: var(--accent); font-weight: 600; }
  .tc-modal button.danger { background: var(--bad-dim, var(--panel-2)); }
{% endblock %}

{% block content %}
<h1 style="margin-top:0">🏆 Trophy Case</h1>

<!-- GOATs -->
<section class="tc-section" id="goats">
  <h2>🐐 GOATs (All-Time)</h2>
  <div class="tc-goats">
    {% for g in goats %}
    <div class="tc-card">
      <div class="head">
        <span class="icon">🐐</span>
        <span class="name">{{ g.group }}</span>
        <button class="tc-edit" style="margin-left:auto"
                data-scope="award_goat" data-group="{{ g.group }}"
                data-position="1">✏️</button>
      </div>
      {% if g.winner %}
      <div class="meta">
        <strong>{{ g.winner.name }}</strong> — {{ '{:,.0f}'.format(g.winner.units) }} units
        on {{ g.winner.day }} ({{ '{:,.1f}'.format(g.winner.pph) }} pph)
      </div>
      {% else %}
      <div class="empty">No record yet.</div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</section>

<!-- Annual -->
<section class="tc-section" id="annual">
  <h2>🏆 Annual ({{ year }})</h2>
  <div class="tc-pickers">
    <label>Year</label>
    <select id="year-picker">
      {% for y in range(today[:4]|int, today[:4]|int - 4, -1) %}
      <option value="{{ y }}" {% if y == year %}selected{% endif %}>{{ y }}</option>
      {% endfor %}
    </select>
  </div>
  {% for blk in annual %}
  <div class="tc-group-block" id="annual-{{ blk.group|lower }}">
    <h3>{{ blk.group }}</h3>
    {% if blk.top_days %}
    {% for s in blk.top_days %}
    <div class="tc-row">
      <span class="icon">{% if s.position == 1 %}🥇{% elif s.position == 2 %}🥈{% else %}🥉{% endif %}</span>
      <span class="name">{{ s.name }}</span>
      <span class="meta">{{ '{:,.0f}'.format(s.units) }} units · {{ s.day }}</span>
      <button class="tc-edit"
              data-scope="trophy_top_day" data-group="{{ blk.group }}"
              data-year="{{ year }}" data-position="{{ s.position }}">✏️</button>
    </div>
    {% endfor %}
    {% else %}
    <div class="tc-row"><span class="empty">No top-day winners for {{ year }}.</span></div>
    {% endif %}

    {% if blk.best_avg %}
    <div class="tc-row" style="margin-top:0.4rem">
      <span class="icon">🏆</span>
      <span class="name">Best {{ blk.group }} {{ year }}</span>
      <span class="meta">{{ blk.best_avg.name }} — {{ '{:,.1f}'.format(blk.best_avg.pph) }} pph ({{ blk.best_avg.days }} days)</span>
      <button class="tc-edit"
              data-scope="trophy_best_avg_group" data-group="{{ blk.group }}"
              data-year="{{ year }}" data-position="1">✏️</button>
    </div>
    {% endif %}

    {% if blk.wc_winners %}
    <div style="margin-top:0.4rem;color:var(--muted);font-size:0.8rem;text-transform:uppercase;letter-spacing:0.5px">Best of each WC</div>
    {% for w in blk.wc_winners %}
    <div class="tc-row">
      <span class="icon">🏆</span>
      <span class="name">{{ w.wc }}</span>
      <span class="meta">{{ w.winner.name }} — {{ '{:,.1f}'.format(w.winner.pph) }} pph ({{ w.winner.days }} days)</span>
      <button class="tc-edit"
              data-scope="trophy_best_avg_wc" data-wc="{{ w.wc }}"
              data-year="{{ year }}" data-position="1">✏️</button>
    </div>
    {% endfor %}
    {% endif %}
  </div>
  {% endfor %}
</section>

<!-- Monthly -->
<section class="tc-section" id="monthly">
  <h2>🥇 Monthly ({{ '%04d-%02d' % (year, month) }})</h2>
  <div class="tc-pickers">
    <label>Month</label>
    <select id="month-picker">
      {% for m in range(1, 13) %}
      <option value="{{ m }}" {% if m == month %}selected{% endif %}>{{ '%04d-%02d' % (year, m) }}</option>
      {% endfor %}
    </select>
  </div>
  {% for blk in monthly %}
  <div class="tc-group-block">
    <h3>{{ blk.group }}</h3>
    {% if blk.badges %}
    {% for s in blk.badges %}
    <div class="tc-row">
      <span class="icon">{% if s.position == 1 %}🥇{% elif s.position == 2 %}🥈{% else %}🥉{% endif %}</span>
      <span class="name">{{ s.name }}</span>
      <span class="meta">{{ '{:,.0f}'.format(s.units) }} units · {{ s.day }}</span>
      <button class="tc-edit"
              data-scope="badge" data-group="{{ blk.group }}"
              data-year="{{ year }}" data-month="{{ month }}"
              data-position="{{ s.position }}">✏️</button>
    </div>
    {% endfor %}
    {% else %}
    <div class="tc-row"><span class="empty">No badges for this month.</span></div>
    {% endif %}
  </div>
  {% endfor %}
</section>

<!-- Override modal -->
<div class="tc-modal-backdrop" id="tc-modal-bd">
  <div class="tc-modal">
    <h3 id="tc-modal-title">Edit award</h3>
    <label>Action</label>
    <select id="tc-action">
      <option value="replace">Reassign to…</option>
      <option value="delete">Delete this slot</option>
      <option value="reset">Reset to computed</option>
    </select>
    <div id="tc-name-row">
      <label>Name</label>
      <input type="text" id="tc-name" placeholder="Enter person's name">
    </div>
    <label>Note (optional)</label>
    <textarea id="tc-note" rows="2"></textarea>
    <div class="actions">
      <button id="tc-cancel">Cancel</button>
      <button class="primary" id="tc-save">Save</button>
    </div>
  </div>
</div>

<script>
(function () {
  var bd = document.getElementById('tc-modal-bd');
  var current = null;

  document.querySelectorAll('.tc-edit').forEach(function (btn) {
    btn.addEventListener('click', function () {
      current = {
        scope: btn.dataset.scope,
        group_name: btn.dataset.group || null,
        wc_name: btn.dataset.wc || null,
        year: btn.dataset.year ? parseInt(btn.dataset.year, 10) : null,
        month: btn.dataset.month ? parseInt(btn.dataset.month, 10) : null,
        position: parseInt(btn.dataset.position, 10),
      };
      document.getElementById('tc-action').value = 'replace';
      document.getElementById('tc-name').value = '';
      document.getElementById('tc-note').value = '';
      bd.classList.add('active');
    });
  });

  document.getElementById('tc-action').addEventListener('change', function (e) {
    document.getElementById('tc-name-row').style.display =
      (e.target.value === 'replace') ? 'block' : 'none';
  });

  document.getElementById('tc-cancel').addEventListener('click', function () {
    bd.classList.remove('active');
  });

  document.getElementById('tc-save').addEventListener('click', function () {
    if (!current) return;
    var action = document.getElementById('tc-action').value;
    var body = Object.assign({}, current, {
      action: action,
      name: document.getElementById('tc-name').value || null,
      note: document.getElementById('tc-note').value || null,
    });
    fetch('/api/awards/override', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    }).then(function (r) {
      if (r.ok) { window.location.reload(); }
      else { alert('Save failed.'); }
    }).catch(function () { alert('Network error.'); });
  });

  // Year + month pickers reload with new query string.
  var yp = document.getElementById('year-picker');
  if (yp) yp.addEventListener('change', function () {
    var u = new URL(window.location.href);
    u.searchParams.set('year', yp.value);
    window.location.assign(u.toString());
  });
  var mp = document.getElementById('month-picker');
  if (mp) mp.addEventListener('change', function () {
    var u = new URL(window.location.href);
    u.searchParams.set('month', mp.value);
    window.location.assign(u.toString());
  });
})();
</script>
{% endblock %}
```

- [ ] **Step 5: Register the route**

Open `src/zira_dashboard/app.py`. In the import list around line 22-36, add `trophies`:

```python
from .routes import (
    admin,
    api_layout,
    changelog,
    dashboard,
    leaderboards,
    past_schedules,
    people,
    settings,
    share,
    skills,
    staffing,
    time_off,
    trophies,
    value_streams,
)
```

Then find the `app.include_router(...)` calls (further down) and add `app.include_router(trophies.router)` near the other route registrations.

- [ ] **Step 6: Smoke-test**

```
.venv/Scripts/python.exe -c "
from zira_dashboard.app import app
paths = [r.path for r in app.routes]
assert '/trophies' in paths, 'page not registered'
assert '/api/awards/override' in paths, 'endpoint not registered'
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 7: Run the tests**

```
.venv/Scripts/python.exe -m pytest tests/test_trophies_route.py -v
```

Expected: 5 PASS.

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/routes/trophies.py src/zira_dashboard/templates/trophy_case.html src/zira_dashboard/app.py tests/test_trophies_route.py
git commit -m "$(cat <<'EOF'
feat(trophies): /trophies page + override endpoint

GOATs, Annual (year-pickered), and Monthly (month-pickered)
sections rendering all five award types from awards.py. Inline
✏️ buttons open a modal for replace/delete/reset; modal POSTs to
/api/awards/override which writes to award_overrides with
INSERT … ON CONFLICT DO UPDATE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Player card integration

**Files:**
- Modify: `src/zira_dashboard/routes/people.py`
- Modify: `src/zira_dashboard/templates/player_card.html`

- [ ] **Step 1: Add `awards_earned` to the route context**

In `src/zira_dashboard/routes/people.py::staffing_player_card`, find the `templates.TemplateResponse` context block. Just before the `return`, after `roster_names = sorted(...)`, add:

```python
    from .. import awards
    awards_earned = awards.awards_earned_by(name, today)
```

Add `"awards_earned": awards_earned,` to the context dict (right after `"roster_names": roster_names,`).

- [ ] **Step 2: Add the section to the template**

In `src/zira_dashboard/templates/player_card.html`, find the `{% if group_avgs %}` block (added by the recent player-card-stats commit). Immediately after that block's closing `{% endif %}`, before `{% if rows %}`, insert:

```jinja
{% if awards_earned %}
<section style="margin:1rem 0">
  <h3 style="margin:0 0 0.4rem 0">Trophy case</h3>
  <ul style="list-style:none;padding:0;margin:0;font-size:0.92rem;line-height:1.5">
    {% for a in awards_earned %}
    <li>
      {% if a.type == 'goat' %}
        <a href="/trophies#goats">🐐 GOAT — {{ a.group }}
          ({{ '{:,.0f}'.format(a.units) }} units, {{ a.day }})</a>
      {% elif a.type == 'trophy_best_avg_group' %}
        <a href="/trophies?year={{ a.year }}#annual-{{ a.group|lower }}">🏆 Best {{ a.group }} {{ a.year }} —
          {{ '{:,.1f}'.format(a.pph) }} pph ({{ a.days }} days)</a>
      {% elif a.type == 'trophy_best_avg_wc' %}
        <a href="/trophies?year={{ a.year }}#annual">🏆 Best {{ a.wc }} of {{ a.year }} —
          {{ '{:,.1f}'.format(a.pph) }} pph ({{ a.days }} days)</a>
      {% elif a.type == 'trophy_top_day' %}
        <a href="/trophies?year={{ a.year }}#annual-{{ a.group|lower }}">{% if a.position == 1 %}🥇{% elif a.position == 2 %}🥈{% else %}🥉{% endif %}
          {{ a.group }} top day {{ a.year }} —
          {{ '{:,.0f}'.format(a.units) }} units ({{ a.day }})</a>
      {% elif a.type == 'badge' %}
        <a href="/trophies?year={{ a.year }}&month={{ a.month }}#monthly">{% if a.position == 1 %}🥇{% elif a.position == 2 %}🥈{% else %}🥉{% endif %}
          {{ a.group }} — {{ '%04d-%02d' % (a.year, a.month) }}
          ({{ '{:,.0f}'.format(a.units) }} units)</a>
      {% endif %}
    </li>
    {% endfor %}
  </ul>
</section>
{% endif %}
```

- [ ] **Step 3: Smoke-test the template parses**

```
.venv/Scripts/python.exe -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'))
env.get_template('player_card.html')
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 4: Verify existing player card tests still pass**

```
.venv/Scripts/python.exe -m pytest tests/test_player_card.py tests/test_player_card_stats.py -v
```

The player card tests stub a few dependencies. They may need an additional patch for `awards.awards_earned_by` to return `[]` so the new section renders empty. If they fail with a "DATABASE_URL not set" or similar, find each `_stub_route_dependencies` (or equivalent setup) and add:

```python
from zira_dashboard import awards
monkeypatch.setattr(awards, "awards_earned_by", lambda *a, **k: [])
```

Re-run until green.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/people.py src/zira_dashboard/templates/player_card.html tests/test_player_card.py tests/test_player_card_stats.py
git commit -m "$(cat <<'EOF'
feat(player_card): trophy case section

Hides entirely when the person has no awards. Each line links to
the matching anchor on /trophies. Order: GOATs → annual trophies
(newest year first) → monthly badges (newest month first), driven
by awards.awards_earned_by.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Top-nav entry across all six templates

**Files:**
- Modify: `src/zira_dashboard/templates/_staffing_base.html`
- Modify: `src/zira_dashboard/templates/staffing.html`
- Modify: `src/zira_dashboard/templates/new_vs.html`
- Modify: `src/zira_dashboard/templates/recycling.html`
- Modify: `src/zira_dashboard/templates/settings.html`
- Modify: `src/zira_dashboard/templates/index.html`

- [ ] **Step 1: `_staffing_base.html`**

Find lines ~68-70:

```jinja
      <a href="/recycling">Dashboards</a>
      <a href="/staffing/leaderboards" class="{% if active == 'leaderboards' %}active{% endif %}">Leaderboards</a>
      <a href="/staffing" class="{% if active != 'leaderboards' %}active{% endif %}">Staffing</a>
```

Replace with:

```jinja
      <a href="/recycling">Dashboards</a>
      <a href="/staffing/leaderboards" class="{% if active == 'leaderboards' %}active{% endif %}">Leaderboards</a>
      <a href="/trophies" class="{% if active == 'trophies' %}active{% endif %}">Trophy Case</a>
      <a href="/staffing" class="{% if active not in ['leaderboards', 'trophies'] %}active{% endif %}">Staffing</a>
```

- [ ] **Step 2: `staffing.html`**

Find the existing `<a href="/recycling">Dashboards</a>` and `<a href="/staffing" class="active">Staffing</a>` lines (around 80-82). Insert a Trophy Case anchor between Leaderboards (which may or may not be there in this template) and Staffing. The literal text to replace will be present somewhere — locate the same nav block. If staffing.html doesn't have a Leaderboards link in its top nav (because it includes _staffing_base.html), no change needed for this file. (Check: `grep -n "Leaderboards" src/zira_dashboard/templates/staffing.html`.)

If there's a duplicated nav block, insert:

```jinja
      <a href="/trophies">Trophy Case</a>
```

between the Leaderboards and Staffing entries.

- [ ] **Step 3: `new_vs.html`**

Find lines ~19-21:

```jinja
      <a href="/recycling" class="active">Dashboards</a>
      ...
      <a href="/staffing">Staffing</a>
```

Insert before the Staffing entry:

```jinja
      <a href="/trophies">Trophy Case</a>
```

- [ ] **Step 4: `recycling.html`**

Same as `new_vs.html` — find lines ~19-21 and insert the Trophy Case anchor before Staffing.

- [ ] **Step 5: `settings.html`**

Find lines ~489-491:

```jinja
    <a href="/recycling">Dashboards</a>
    ...
    <a href="/staffing">Staffing</a>
```

Insert before the Staffing entry:

```jinja
    <a href="/trophies">Trophy Case</a>
```

(Match the existing indentation and any inline-style attributes the surrounding anchors use.)

- [ ] **Step 6: `index.html`**

Find lines ~179-181:

```jinja
      <a href="/recycling" style="...">Dashboards</a>
      ...
      <a href="/staffing" style="...">Staffing</a>
```

Insert before the Staffing entry:

```jinja
      <a href="/trophies" style="color:var(--muted);text-decoration:none;font-size:0.9rem;padding:0.25rem 0.6rem;border-radius:6px">Trophy Case</a>
```

- [ ] **Step 7: Smoke-test all six templates parse**

```
.venv/Scripts/python.exe -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'))
for t in ('_staffing_base.html', 'staffing.html', 'new_vs.html', 'recycling.html', 'settings.html', 'index.html'):
    env.get_template(t)
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/templates/_staffing_base.html src/zira_dashboard/templates/staffing.html src/zira_dashboard/templates/new_vs.html src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/settings.html src/zira_dashboard/templates/index.html
git commit -m "$(cat <<'EOF'
feat(nav): Trophy Case top-nav entry

Adds a Trophy Case link between Leaderboards and Staffing in all
six templates that duplicate the top nav. _staffing_base.html
also handles the active-tab class for the new path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Final test pass + CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full non-DB suite**

```
.venv/Scripts/python.exe -m pytest tests/test_progress.py tests/test_deps_window_dates.py tests/test_share_route.py tests/test_results.py tests/test_zira_persist.py tests/test_slack_client.py tests/test_late_report.py tests/test_wc_attributions.py tests/test_leaderboards_avg.py tests/test_production_history.py tests/test_leaderboards_person_days.py tests/test_player_card.py tests/test_player_card_stats.py tests/test_roster_filter.py tests/test_awards.py tests/test_trophies_route.py -q
```

Expected: all PASS (DB-bound tests skip).

- [ ] **Step 2: Get the time**

```
date "+%I:%M %p"
```

- [ ] **Step 3: Add the CHANGELOG entry**

Insert at the top of today's `## 2026-05-07` section:

```markdown
### {time-from-step-2}

- **Trophy system — badges, trophies, GOAT awards** — three tiers of recognition derived from production data: **monthly badges** (Gold/Silver/Bronze for top single-day units in each group's WCs), **annual trophies** (top-3 days per group + best avg pph per group + best avg per individual WC, both with a 30-day floor), and all-time **🐐 GOAT awards** (best single-day units in each group, holder displaced only by a strictly better day). New **Trophy Case** sub-tab between Leaderboards and Staffing with year/month pickers; player cards now show a **Trophy case** section listing every award the operator currently holds. Manual ✏️ button on every awarded slot lets you reassign, delete, or reset to computed (corrections persist in `award_overrides`).
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat: trophy system — badges, trophies, GOAT awards

Three tiers of recognition computed live from daily_records with a
small award_overrides table for manual reassignment. New /trophies
page (year/month pickers, override modal) and a player-card section
listing every award the person currently holds.

Spec: docs/superpowers/specs/2026-05-07-trophy-system-design.md
Plan: docs/superpowers/plans/2026-05-07-trophy-system.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

---

## Self-review checklist

- [x] Spec goal 1 (monthly badges per group) — Task 2 (`monthly_badges`) + Task 5 (Monthly section) ✓
- [x] Spec goal 2 (annual top-day trophies per group) — Task 2 (`annual_top_days`) + Task 5 (Annual section) ✓
- [x] Spec goal 3 (annual best-avg per group, ≥30 days) — Task 3 (`annual_best_avg_group`) + Task 5 ✓
- [x] Spec goal 4 (annual best-avg per WC, ≥30 days, skip if no qualifier) — Task 3 (`annual_best_avg_wc`) + Task 5 (gated on `if wcw:`) ✓
- [x] Spec goal 5 (GOAT all-time, first-to-set on tie) — Task 2 (`goat`) + tie-break test ✓
- [x] Spec goal 6 (manual override: replace/delete/reset) — Task 4 (`apply_overrides*`) + Task 5 (endpoint + modal) ✓
- [x] Spec goal 7 (player card section, hidden when empty) — Task 6 (`{% if awards_earned %}`) ✓
- [x] Spec goal 8 (Trophy Case page with year/month pickers) — Task 5 ✓
- [x] Tie-break rules (units desc → pph desc → name asc; for goat: units → day asc → name asc; for best-avg: pph desc → days desc → units desc → name asc) — encoded in `_rank_single_day`, `goat`, `_rank_avg`. Tests cover the units/pph/name and goat-day cases ✓
- [x] Schema scopes consistent across DDL (Task 1), engine (Task 4), endpoint (Task 5), modal (Task 5), reverse lookup (Task 4): `badge`, `trophy_top_day`, `trophy_best_avg_group`, `trophy_best_avg_wc`, `award_goat` ✓
- [x] No placeholders, no TBDs ✓
- [x] Type consistency: every public function returns dicts with documented keys (`name`, `day`, `units`, `pph`, `position`, `days`, `hours`); template renders those keys ✓
- [x] Endpoint URL `/api/awards/override` matches between route definition (Task 5), modal POST (Task 5), and tests (Task 5) ✓
