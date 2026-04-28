# Custom Day Hours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-day overrides for shift start, shift end, and the break list — set from the scheduler — that automatically reshape uptime, group goals, bar widget expected, progress chart bucket targets, and leaderboard / player-card percentages.

**Architecture:** Add one `custom_hours: dict | None` field on `staffing.Schedule` (the per-day JSON record). Add day-aware twins of `shift_start()` / `shift_end()` / `breaks()` / `productive_minutes_per_day()` / `in_shift()` in `shift_config` that consult the per-day override and fall back to the global schedule. Swap the ~6 dashboard / analytics call sites that already have a `day` in scope to use the day-aware variants. UI is a "Hours" pill in the scheduler title bar opening an inline editor, with a banner in the day-notes section when an override is active and a confirm popup gating saves on past days.

**Tech Stack:** Python 3.11+ • FastAPI • Jinja2 • pytest • existing `staffing` / `shift_config` / `schedule_store` modules.

---

## File structure

**Modified:**
- `src/zira_dashboard/staffing.py` — `Schedule` dataclass gains `custom_hours`; `load_schedule` / `save_schedule` round-trip it; `snapshot_of` carries it.
- `src/zira_dashboard/shift_config.py` — new `shift_start_for(day)`, `shift_end_for(day)`, `breaks_for(day)`, `productive_minutes_for(day)`, `in_shift_on(local_dt)`. `shift_elapsed_minutes(day, now)` rewires internally.
- `src/zira_dashboard/leaderboard.py` — `fetch_station_day` swaps `shift_end()` → `shift_end_for(day_local)` for the eval-end cap.
- `src/zira_dashboard/progress.py` — `progress_buckets` swaps to `shift_start_for(day)` / `shift_end_for(day)` / `breaks_for(day)`.
- `src/zira_dashboard/routes/value_streams.py` — `recycling()` and `new_vs()` swap to `*_for(d)` for productive intervals + first-60-min grace + break subtraction.
- `src/zira_dashboard/routes/staffing.py` — staffing GET passes effective hours + `custom_hours` flag to the template; new `POST /staffing/hours` handler.
- `src/zira_dashboard/templates/staffing.html` — Hours pill in title-bar, inline editor, banner in day-notes section, past-day confirm guard.
- `tests/test_shift_config_for.py` (new) — pytest coverage for the new day-aware helpers.
- `tests/test_staffing_custom_hours.py` (new) — pytest coverage for `custom_hours` round-trip + snapshot.

---

## Phase 1 — Data layer (`custom_hours` round-trip)

### Task 1: Add `custom_hours` field with default None

**Files:**
- Modify: `src/zira_dashboard/staffing.py`
- Test: `tests/test_staffing_custom_hours.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_staffing_custom_hours.py`:

```python
from datetime import date
from zira_dashboard.staffing import Schedule


def test_schedule_custom_hours_defaults_to_none():
    s = Schedule(day=date(2026, 4, 28))
    assert s.custom_hours is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_staffing_custom_hours.py::test_schedule_custom_hours_defaults_to_none -v`
Expected: AttributeError or AssertionError ("custom_hours" not present).

- [ ] **Step 3: Add the field**

In `src/zira_dashboard/staffing.py`, in the `Schedule` dataclass (around line 252), add `custom_hours` after `published_snapshot`:

```python
@dataclass
class Schedule:
    day: date
    published: bool = False
    assignments: dict[str, list[str]] = field(default_factory=dict)
    notes: str = ""
    wc_notes: dict[str, str] = field(default_factory=dict)
    testing_day: bool = False
    published_snapshot: dict | None = None
    # Per-day shift override: {"start": "HH:MM", "end": "HH:MM",
    # "breaks": [{"start": "HH:MM", "end": "HH:MM", "name": "..."}, ...]}.
    # None means "use the global schedule from schedule_store".
    custom_hours: dict | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_staffing_custom_hours.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/staffing.py tests/test_staffing_custom_hours.py
git commit -m "feat: add custom_hours field to staffing.Schedule"
```

### Task 2: load_schedule reads custom_hours from JSON

**Files:**
- Modify: `src/zira_dashboard/staffing.py`
- Test: `tests/test_staffing_custom_hours.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_staffing_custom_hours.py`:

```python
import json
from zira_dashboard import staffing


def test_load_schedule_reads_custom_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    payload = {
        "day": d.isoformat(),
        "published": True,
        "assignments": {"Repair 1": ["Jose"]},
        "custom_hours": {
            "start": "09:00",
            "end": "13:00",
            "breaks": [{"start": "11:00", "end": "11:15", "name": "Stand-up"}],
        },
    }
    (tmp_path / f"{d.isoformat()}.json").write_text(json.dumps(payload), encoding="utf-8")
    sched = staffing.load_schedule(d)
    assert sched.custom_hours == {
        "start": "09:00",
        "end": "13:00",
        "breaks": [{"start": "11:00", "end": "11:15", "name": "Stand-up"}],
    }


def test_load_schedule_treats_missing_custom_hours_as_none(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    (tmp_path / f"{d.isoformat()}.json").write_text(
        json.dumps({"day": d.isoformat(), "published": False, "assignments": {}}),
        encoding="utf-8",
    )
    sched = staffing.load_schedule(d)
    assert sched.custom_hours is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_staffing_custom_hours.py -v`
Expected: First test FAILs (custom_hours is None even when in JSON), second PASSes already.

- [ ] **Step 3: Read the field in load_schedule**

In `src/zira_dashboard/staffing.py`, locate `load_schedule()` (around line 280). In the `try` block, after reading `snap_raw`, add:

```python
            ch_raw = data.get("custom_hours")
            custom_hours = ch_raw if isinstance(ch_raw, dict) else None
```

Then update the `Schedule(...)` constructor call to pass it:

```python
            return Schedule(
                day=day,
                published=bool(data.get("published", False)),
                assignments=assignments,
                notes=str(notes) if isinstance(notes, str) else "",
                wc_notes=wc_notes,
                testing_day=bool(data.get("testing_day", False)),
                published_snapshot=snap,
                custom_hours=custom_hours,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_staffing_custom_hours.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/staffing.py tests/test_staffing_custom_hours.py
git commit -m "feat: load_schedule round-trips custom_hours from JSON"
```

### Task 3: save_schedule writes custom_hours

**Files:**
- Modify: `src/zira_dashboard/staffing.py`
- Test: `tests/test_staffing_custom_hours.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_save_schedule_writes_custom_hours(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    sched = staffing.Schedule(
        day=d,
        published=False,
        assignments={"Repair 1": ["Jose"]},
        custom_hours={"start": "09:00", "end": "13:00", "breaks": []},
    )
    staffing.save_schedule(sched)
    raw = (tmp_path / f"{d.isoformat()}.json").read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["custom_hours"] == {"start": "09:00", "end": "13:00", "breaks": []}


def test_save_schedule_omits_custom_hours_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(staffing, "SCHEDULES_DIR", tmp_path)
    d = date(2026, 4, 28)
    sched = staffing.Schedule(day=d, published=False, assignments={}, custom_hours=None)
    staffing.save_schedule(sched)
    parsed = json.loads((tmp_path / f"{d.isoformat()}.json").read_text(encoding="utf-8"))
    assert "custom_hours" not in parsed or parsed["custom_hours"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_staffing_custom_hours.py -v`
Expected: first new test FAILs, second PASSes.

- [ ] **Step 3: Write custom_hours in save_schedule**

In `src/zira_dashboard/staffing.py`, locate `save_schedule()` (around line 306). Find the dict that builds `payload` and add `custom_hours` to it. Look for the existing payload-building lines (they look like `"day": ..., "published": ..., "assignments": ..., ...`). Add after `published_snapshot`:

```python
            "custom_hours": schedule.custom_hours,
```

If the current code conditionally omits keys (e.g., skips `published_snapshot` when None), keep `custom_hours` consistent — write it out only when not None. Concretely, after the existing payload construction, add:

```python
        if schedule.custom_hours is not None:
            payload["custom_hours"] = schedule.custom_hours
```

(If the payload includes other Optional fields by-default-with-None, follow whichever pattern is already there.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_staffing_custom_hours.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/staffing.py tests/test_staffing_custom_hours.py
git commit -m "feat: save_schedule writes custom_hours to JSON"
```

---

## Phase 2 — `shift_config` day-aware helpers

### Task 4: `shift_start_for(day)` — falls back to global

**Files:**
- Modify: `src/zira_dashboard/shift_config.py`
- Test: `tests/test_shift_config_for.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_shift_config_for.py`:

```python
from datetime import date, time
from zira_dashboard import shift_config, staffing


def test_shift_start_for_default_falls_back_to_global(monkeypatch):
    """No override → returns the global shift_start()."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.shift_start_for(date(2026, 4, 28)) == shift_config.shift_start()


def test_shift_start_for_uses_override(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "09:30", "end": "15:00", "breaks": []},
        ))
    assert shift_config.shift_start_for(date(2026, 4, 28)) == time(9, 30)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shift_config_for.py::test_shift_start_for_default_falls_back_to_global -v`
Expected: AttributeError ("module has no attribute 'shift_start_for'").

- [ ] **Step 3: Implement `shift_start_for`**

Append to `src/zira_dashboard/shift_config.py`:

```python
def shift_start_for(day: date) -> time:
    """Return the shift start for `day`, honoring per-day custom_hours
    overrides set in the per-day schedule. Falls back to the global
    schedule when no override is set."""
    # Lazy import to avoid the shift_config → staffing → schedule_store cycle.
    from . import staffing
    sched = staffing.load_schedule(day)
    ch = sched.custom_hours
    if ch and isinstance(ch.get("start"), str):
        try:
            return time.fromisoformat(ch["start"])
        except ValueError:
            pass
    return shift_start()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/shift_config.py tests/test_shift_config_for.py
git commit -m "feat: shift_start_for(day) honors per-day custom_hours"
```

### Task 5: `shift_end_for(day)`

**Files:**
- Modify: `src/zira_dashboard/shift_config.py`
- Test: `tests/test_shift_config_for.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shift_config_for.py`:

```python
def test_shift_end_for_default_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.shift_end_for(date(2026, 4, 28)) == shift_config.shift_end()


def test_shift_end_for_uses_override(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "07:00", "end": "11:30", "breaks": []},
        ))
    assert shift_config.shift_end_for(date(2026, 4, 28)) == time(11, 30)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: AttributeError.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/shift_config.py`:

```python
def shift_end_for(day: date) -> time:
    from . import staffing
    sched = staffing.load_schedule(day)
    ch = sched.custom_hours
    if ch and isinstance(ch.get("end"), str):
        try:
            return time.fromisoformat(ch["end"])
        except ValueError:
            pass
    return shift_end()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/shift_config.py tests/test_shift_config_for.py
git commit -m "feat: shift_end_for(day) honors per-day custom_hours"
```

### Task 6: `breaks_for(day)` returns Break objects

**Files:**
- Modify: `src/zira_dashboard/shift_config.py`
- Test: `tests/test_shift_config_for.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from zira_dashboard.schedule_store import Break


def test_breaks_for_default_falls_back_to_global(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.breaks_for(date(2026, 4, 28)) == shift_config.breaks()


def test_breaks_for_uses_override(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={
                "start": "07:00", "end": "15:00",
                "breaks": [
                    {"start": "10:00", "end": "10:30", "name": "Meeting"},
                    {"start": "12:00", "end": "12:30", "name": "Lunch"},
                ],
            },
        ))
    out = shift_config.breaks_for(date(2026, 4, 28))
    assert out == (
        Break(time(10, 0), time(10, 30), "Meeting"),
        Break(time(12, 0), time(12, 30), "Lunch"),
    )


def test_breaks_for_empty_list_means_no_breaks(monkeypatch):
    """Empty list override means 'no breaks today' — not 'fall back to global'."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "07:00", "end": "11:00", "breaks": []},
        ))
    assert shift_config.breaks_for(date(2026, 4, 28)) == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: AttributeError.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/shift_config.py`:

```python
def breaks_for(day: date) -> tuple:
    """Return the breaks tuple for `day`, honoring per-day custom_hours.

    A custom_hours override with an empty `breaks` list means "no breaks
    today" — not "fall back to global." Only when custom_hours itself is
    None (or omits the breaks key) do we use the global break list.
    """
    from . import staffing
    from .schedule_store import Break
    sched = staffing.load_schedule(day)
    ch = sched.custom_hours
    if ch and isinstance(ch.get("breaks"), list):
        out = []
        for b in ch["breaks"]:
            if not isinstance(b, dict):
                continue
            try:
                bs = time.fromisoformat(b["start"])
                be = time.fromisoformat(b["end"])
            except (ValueError, KeyError, TypeError):
                continue
            name = str(b.get("name") or "Break")
            out.append(Break(bs, be, name))
        return tuple(out)
    return breaks()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/shift_config.py tests/test_shift_config_for.py
git commit -m "feat: breaks_for(day) honors per-day custom_hours"
```

### Task 7: `productive_minutes_for(day)`

**Files:**
- Modify: `src/zira_dashboard/shift_config.py`
- Test: `tests/test_shift_config_for.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_productive_minutes_for_default_matches_global(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(day=d, published=False, custom_hours=None))
    assert shift_config.productive_minutes_for(date(2026, 4, 28)) == shift_config.productive_minutes_per_day()


def test_productive_minutes_for_half_day_with_one_break(monkeypatch):
    """09:00 → 13:00 = 240 min, minus a 30-min break = 210 min."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={
                "start": "09:00", "end": "13:00",
                "breaks": [{"start": "11:00", "end": "11:30", "name": "Lunch"}],
            },
        ))
    assert shift_config.productive_minutes_for(date(2026, 4, 28)) == 210
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: AttributeError.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/shift_config.py`:

```python
def productive_minutes_for(day: date) -> int:
    """Total productive minutes for `day` (shift duration minus breaks),
    honoring custom_hours."""
    def _mins(t): return t.hour * 60 + t.minute
    s, e = shift_start_for(day), shift_end_for(day)
    total = _mins(e) - _mins(s)
    for b in breaks_for(day):
        total -= _mins(b.end) - _mins(b.start)
    return max(0, total)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/shift_config.py tests/test_shift_config_for.py
git commit -m "feat: productive_minutes_for(day) honors per-day custom_hours"
```

### Task 8: `in_shift_on(local_dt)` derives day from the dt

**Files:**
- Modify: `src/zira_dashboard/shift_config.py`
- Test: `tests/test_shift_config_for.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
from datetime import datetime
from zira_dashboard.shift_config import SITE_TZ


def test_in_shift_on_respects_custom_hours(monkeypatch):
    """09:30 → 13:00 override; 09:00 should be out, 10:00 should be in."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={"start": "09:30", "end": "13:00", "breaks": []},
        ))
    monkeypatch.setattr(shift_config, "work_weekdays", lambda: frozenset(range(7)))
    early = datetime(2026, 4, 28, 9, 0, tzinfo=SITE_TZ)
    inside = datetime(2026, 4, 28, 10, 0, tzinfo=SITE_TZ)
    assert shift_config.in_shift_on(early) is False
    assert shift_config.in_shift_on(inside) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: AttributeError.

- [ ] **Step 3: Implement**

Append to `src/zira_dashboard/shift_config.py`:

```python
def in_shift_on(local_dt: datetime) -> bool:
    """Day-aware twin of in_shift(): derives the day from local_dt and
    consults per-day custom_hours."""
    if local_dt.weekday() not in work_weekdays():
        return False
    day = local_dt.date()
    t = local_dt.time()
    if t < shift_start_for(day) or t >= shift_end_for(day):
        return False
    for b in breaks_for(day):
        if b.start <= t < b.end:
            return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_shift_config_for.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/shift_config.py tests/test_shift_config_for.py
git commit -m "feat: in_shift_on(local_dt) honors per-day custom_hours"
```

### Task 9: `shift_elapsed_minutes` rewires to day-aware helpers

**Files:**
- Modify: `src/zira_dashboard/shift_config.py`
- Test: `tests/test_shift_config_for.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_shift_elapsed_minutes_respects_custom_hours(monkeypatch):
    """Custom 09:00 → 13:00 with a 30-min break at 11:00. As of 12:00,
    elapsed = 9-11 (120 min) + 11:30-12:00 (30 min) = 150 min."""
    monkeypatch.setattr(staffing, "load_schedule",
        lambda d: staffing.Schedule(
            day=d, published=False,
            custom_hours={
                "start": "09:00", "end": "13:00",
                "breaks": [{"start": "11:00", "end": "11:30", "name": "Lunch"}],
            },
        ))
    monkeypatch.setattr(shift_config, "work_weekdays", lambda: frozenset(range(7)))
    d = date(2026, 4, 28)
    now = datetime(2026, 4, 28, 12, 0, tzinfo=SITE_TZ)
    assert shift_config.shift_elapsed_minutes(d, now) == 150
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_shift_config_for.py::test_shift_elapsed_minutes_respects_custom_hours -v`
Expected: FAIL — current `shift_elapsed_minutes` uses `shift_start()` / `shift_end()` / `breaks()` which return globals.

- [ ] **Step 3: Rewire `shift_elapsed_minutes` internally**

In `src/zira_dashboard/shift_config.py`, replace `shift_elapsed_minutes` with:

```python
def shift_elapsed_minutes(day: date, now: datetime) -> int:
    """Productive shift minutes elapsed on `day` as of `now` (site-local).
    Honors per-day custom_hours."""
    if day.weekday() not in work_weekdays():
        return 0
    s = shift_start_for(day)
    e = shift_end_for(day)
    start = datetime.combine(day, s, tzinfo=SITE_TZ)
    end = datetime.combine(day, e, tzinfo=SITE_TZ)
    effective = min(now.astimezone(SITE_TZ), end)
    if effective <= start:
        return 0
    total = int((effective - start).total_seconds() // 60)
    for b in breaks_for(day):
        bs_dt = datetime.combine(day, b.start, tzinfo=SITE_TZ)
        be_dt = datetime.combine(day, b.end, tzinfo=SITE_TZ)
        lo = max(bs_dt, start)
        hi = min(be_dt, effective)
        if hi > lo:
            total -= int((hi - lo).total_seconds() // 60)
    return max(0, total)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: every test PASS, including the new ones and the 27 pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/shift_config.py tests/test_shift_config_for.py
git commit -m "feat: shift_elapsed_minutes honors per-day custom_hours"
```

---

## Phase 3 — Cascade swap to day-aware helpers

### Task 10: `leaderboard.py` swaps to `shift_end_for(day)`

**Files:**
- Modify: `src/zira_dashboard/leaderboard.py`

- [ ] **Step 1: Locate the existing call site**

Run: `grep -n "shift_end\b" src/zira_dashboard/leaderboard.py`

You should see one or two references inside `fetch_station_day` (around the `eval_end`/`shift_end_local` lines).

- [ ] **Step 2: Update the import**

Change:
```python
from .shift_config import SITE_TZ, in_shift, shift_end
```
to:
```python
from .shift_config import SITE_TZ, in_shift, shift_end_for
```

- [ ] **Step 3: Replace `shift_end()` with `shift_end_for(day_local)`**

In `fetch_station_day`, find the lines that compute `shift_end_local`:
```python
day_local = end_of_day.astimezone(SITE_TZ).date()
shift_end_local = datetime.combine(day_local, shift_end(), tzinfo=SITE_TZ)
```
Change to:
```python
day_local = end_of_day.astimezone(SITE_TZ).date()
shift_end_local = datetime.combine(day_local, shift_end_for(day_local), tzinfo=SITE_TZ)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/leaderboard.py
git commit -m "refactor: leaderboard caps active intervals via shift_end_for(day)"
```

### Task 11: `progress.py` swaps to day-aware helpers

**Files:**
- Modify: `src/zira_dashboard/progress.py`

- [ ] **Step 1: Locate the existing call sites**

Run: `grep -n "shift_start\|shift_end\|breaks\b" src/zira_dashboard/progress.py`

You should see imports plus calls inside `progress_buckets` and `_in_any_break`.

- [ ] **Step 2: Update imports**

Change:
```python
from .shift_config import SITE_TZ, breaks, shift_end, shift_start, work_weekdays
```
to:
```python
from .shift_config import SITE_TZ, breaks_for, shift_end_for, shift_start_for, work_weekdays
```

- [ ] **Step 3: Replace zero-arg calls with day-aware variants**

In `progress_buckets()`, replace:
```python
    start = datetime.combine(day, shift_start(), tzinfo=SITE_TZ)
    end = datetime.combine(day, shift_end(), tzinfo=SITE_TZ)
```
with:
```python
    start = datetime.combine(day, shift_start_for(day), tzinfo=SITE_TZ)
    end = datetime.combine(day, shift_end_for(day), tzinfo=SITE_TZ)
```

Update `_in_any_break(t: time)` to take a day param:
```python
def _in_any_break(day: date, t: time) -> bool:
    return any(b.start <= t < b.end for b in breaks_for(day))
```

In `progress_buckets`, change the `_in_any_break(b_start.time())` call to `_in_any_break(day, b_start.time())`.

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/progress.py
git commit -m "refactor: progress.py uses day-aware shift helpers"
```

### Task 12: `routes/value_streams.py` recycling() swaps to day-aware

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py`

- [ ] **Step 1: Locate the existing call sites**

Run: `grep -n "shift_config\." src/zira_dashboard/routes/value_streams.py`

Look for references to `shift_config.shift_start()`, `shift_config.shift_end()`, `shift_config.breaks()` (likely in the productive-intervals + grace + break-subtraction blocks of `recycling()` and possibly `new_vs()`).

- [ ] **Step 2: In `recycling()`, replace zero-arg calls with day-aware**

Find the block around productive-intervals computation:
```python
    shift_start_local = datetime.combine(d, shift_config.shift_start(), tzinfo=shift_config.SITE_TZ)
    shift_end_local   = datetime.combine(d, shift_config.shift_end(),   tzinfo=shift_config.SITE_TZ)
```
Change to:
```python
    shift_start_local = datetime.combine(d, shift_config.shift_start_for(d), tzinfo=shift_config.SITE_TZ)
    shift_end_local   = datetime.combine(d, shift_config.shift_end_for(d),   tzinfo=shift_config.SITE_TZ)
```

Find the break-subtraction block:
```python
    for b in shift_config.breaks():
        ...
```
Change to:
```python
    for b in shift_config.breaks_for(d):
        ...
```

- [ ] **Step 3: Same swap in `new_vs()` if present**

If `new_vs()` has any of those calls, apply the same swap. (It may not — confirm with `grep -n "shift_config\." src/zira_dashboard/routes/value_streams.py` after Step 2.)

- [ ] **Step 4: Run all tests + boot check**

Run: `python -m pytest tests/ -v`
Expected: all PASS.
Run: `python -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py
git commit -m "refactor: VS routes use day-aware shift helpers"
```

---

## Phase 4 — UI: pill, editor, banner, route handler

### Task 13: Pass effective hours + custom_hours flag into the staffing template context

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`

- [ ] **Step 1: Locate the staffing GET handler**

Run: `grep -n "def staffing\|TemplateResponse" src/zira_dashboard/routes/staffing.py | head`

You'll see the GET handler that calls `templates.TemplateResponse(request, "staffing.html", {...})`.

- [ ] **Step 2: Compute and pass the effective hours**

Add these lines just before the `TemplateResponse` call (where `d` is already in scope):

```python
    eff_start = shift_config.shift_start_for(d)
    eff_end   = shift_config.shift_end_for(d)
    eff_breaks = [
        {"start": b.start.strftime("%H:%M"),
         "end":   b.end.strftime("%H:%M"),
         "name":  b.name}
        for b in shift_config.breaks_for(d)
    ]
    has_custom_hours = sched.custom_hours is not None
    eff_hours_label = f"{eff_start.strftime('%H:%M')}–{eff_end.strftime('%H:%M')}"
```

(Make sure `shift_config` is imported at the top of the file. If not, add `from .. import shift_config`.)

Add these to the dict passed to `TemplateResponse`:

```python
            "eff_hours_start": eff_start.strftime("%H:%M"),
            "eff_hours_end": eff_end.strftime("%H:%M"),
            "eff_breaks": eff_breaks,
            "has_custom_hours": has_custom_hours,
            "eff_hours_label": eff_hours_label,
```

- [ ] **Step 3: Boot check**

Run: `python -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "feat(staffing): pass effective hours + custom-hours flag to template"
```

### Task 14: Add the Hours pill + editor popover to the title-bar

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html`

- [ ] **Step 1: Locate the title-bar block**

Run: `grep -n 'class="title-bar"\|testing-pill\|class=\"title-actions\"' src/zira_dashboard/templates/staffing.html | head`

You should see the existing title-bar containing the date picker, Today button, Next Day button, view-toggle, etc.

- [ ] **Step 2: Add CSS for the pill, editor, and banner**

In the `<style>` block of `staffing.html`, append (near the other pill/popover styles):

```css
  /* Hours pill — shows effective hours for the day, accent-colored when an
     override is set. */
  .hours-pill {
    display: inline-flex; align-items: center; gap: 0.3rem;
    padding: 0.2rem 0.6rem;
    background: var(--panel-2); border: 1px solid var(--border);
    border-radius: 999px;
    font-size: 0.78rem; font-weight: 600;
    color: var(--muted);
    cursor: pointer;
  }
  .hours-pill:hover { color: var(--fg); border-color: var(--muted); }
  .hours-pill.custom { color: var(--accent); border-color: var(--accent); background: var(--accent-dim); }
  .hours-pill .label { text-transform: uppercase; letter-spacing: 0.5px; font-size: 0.65rem; }

  /* Hours editor — inline popover, same DOM as Override popups. */
  .hours-editor {
    position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
    background: var(--panel); color: var(--fg);
    border: 1px solid var(--border); border-radius: 12px;
    padding: 1.1rem 1.25rem;
    width: min(28rem, 90vw);
    box-shadow: 0 12px 36px rgba(0,0,0,0.5);
    z-index: 1200;
  }
  .hours-editor h4 { margin: 0 0 0.6rem; font-size: 0.95rem; font-weight: 700; }
  .hours-editor .row { display: flex; gap: 0.4rem; align-items: center; margin-bottom: 0.45rem; }
  .hours-editor .row label { color: var(--muted); font-size: 0.78rem; min-width: 4rem; }
  .hours-editor input[type=time] {
    background: var(--panel-2); color: var(--fg);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 0.25rem 0.4rem; font: inherit; font-size: 0.85rem; color-scheme: dark;
  }
  .hours-editor input[type=text] {
    background: var(--panel-2); color: var(--fg);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 0.25rem 0.5rem; font: inherit; font-size: 0.85rem; flex: 1 1 auto;
  }
  .hours-editor .breaks-list { max-height: 14rem; overflow-y: auto; padding-right: 0.2rem; }
  .hours-editor .break-row {
    display: grid;
    grid-template-columns: 5rem 5rem 1fr 1.6rem;
    gap: 0.35rem; align-items: center;
    margin-bottom: 0.35rem;
  }
  .hours-editor .break-row .remove-btn {
    background: transparent; color: var(--muted);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 0.1rem 0; font: inherit; font-size: 0.85rem; cursor: pointer;
  }
  .hours-editor .break-row .remove-btn:hover { color: var(--bad); border-color: var(--bad); }
  .hours-editor .add-break {
    background: var(--panel-2); color: var(--accent);
    border: 1px dashed var(--accent); border-radius: 6px;
    padding: 0.3rem 0.6rem; font: inherit; font-size: 0.8rem; cursor: pointer;
    margin-top: 0.3rem;
  }
  .hours-editor .actions { display: flex; gap: 0.5rem; justify-content: flex-end; margin-top: 0.7rem; flex-wrap: wrap; }
  .hours-editor .actions .reset {
    margin-right: auto;
    background: transparent; color: var(--muted);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 0.35rem 0.7rem; font: inherit; font-size: 0.82rem; cursor: pointer;
  }
  .hours-editor .actions .reset:hover { color: var(--bad); border-color: var(--bad); }
  .hours-editor .actions .cancel,
  .hours-editor .actions .save {
    border-radius: 6px; padding: 0.35rem 0.85rem;
    font: inherit; font-size: 0.85rem; font-weight: 600; cursor: pointer;
  }
  .hours-editor .actions .cancel { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); }
  .hours-editor .actions .save { background: var(--accent); color: #0e1116; border: 1px solid var(--accent); }

  /* Custom-hours banner shown in the day-notes section. */
  .custom-hours-banner {
    background: var(--accent-dim); color: var(--accent);
    border: 1px solid var(--accent); border-radius: 8px;
    padding: 0.45rem 0.75rem; margin-bottom: 0.5rem;
    font-size: 0.85rem;
  }
  .custom-hours-banner b { font-weight: 700; }
```

- [ ] **Step 3: Add the pill + editor markup in the title-bar**

In the title-bar block, add this just before the existing `<span id="testing-pill">…</span>` (or right after it — anywhere visible in the title-bar row):

```html
        <button type="button" class="hours-pill {% if has_custom_hours %}custom{% endif %}" id="hours-pill"
                title="Click to edit shift hours for this day">
          <span class="label">Hours</span>
          <span>{{ eff_hours_label }}</span>
          {% if has_custom_hours %}<span>· {{ eff_breaks|length }} break{{ eff_breaks|length != 1 and 's' or '' }}</span>{% endif %}
        </button>

        <div id="hours-editor" class="hours-editor" hidden>
          <h4>Custom hours for {{ day }}</h4>
          <div class="row">
            <label>Shift</label>
            <input type="time" id="hours-start" value="{{ eff_hours_start }}" step="300">
            <span style="color:var(--muted)">→</span>
            <input type="time" id="hours-end"   value="{{ eff_hours_end }}"   step="300">
          </div>
          <div class="row" style="margin-bottom:0.25rem"><label>Breaks</label></div>
          <div id="hours-breaks-list" class="breaks-list">
            {% for b in eff_breaks %}
              <div class="break-row">
                <input type="time" class="b-start" value="{{ b.start }}" step="60">
                <input type="time" class="b-end"   value="{{ b.end }}"   step="60">
                <input type="text" class="b-name"  value="{{ b.name }}" maxlength="40">
                <button type="button" class="remove-btn" title="Remove break">×</button>
              </div>
            {% endfor %}
          </div>
          <button type="button" id="hours-add-break" class="add-break">+ Add break</button>
          <div class="actions">
            <button type="button" class="reset"  id="hours-reset">Reset to defaults</button>
            <button type="button" class="cancel" id="hours-cancel">Cancel</button>
            <button type="button" class="save"   id="hours-save">Save</button>
          </div>
        </div>
```

- [ ] **Step 4: Add the banner near day-notes**

Locate the day-notes section in `staffing.html` (near `<textarea id="notes-textarea">…`). Just **above** that textarea (or above the `<label class="notes-label">`), add:

```html
        {% if has_custom_hours %}
        <div class="custom-hours-banner">
          <b>Custom hours today:</b> {{ eff_hours_start }}–{{ eff_hours_end }}
          {% if eff_breaks %}· {{ eff_breaks|length }} break{{ eff_breaks|length != 1 and 's' or '' }}: 
          {% for b in eff_breaks %}{{ b.name }} ({{ b.start }}–{{ b.end }}){% if not loop.last %}, {% endif %}{% endfor %}
          {% endif %}
        </div>
        {% endif %}
```

- [ ] **Step 5: Verify the page renders**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; e = Environment(loader=FileSystemLoader('src/zira_dashboard/templates')); e.get_template('staffing.html')"`
Expected: silent success.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/templates/staffing.html
git commit -m "feat(staffing): Hours pill + inline editor + banner markup"
```

### Task 15: Add the editor open/close + add-break + reset JS

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html`

- [ ] **Step 1: Add the JS block**

In the existing `<script>` section near the bottom of `staffing.html`, append:

```javascript
  // ---------- Custom hours editor ----------
  (function() {
    const pill   = document.getElementById('hours-pill');
    const editor = document.getElementById('hours-editor');
    const list   = document.getElementById('hours-breaks-list');
    const addBtn = document.getElementById('hours-add-break');
    const cancel = document.getElementById('hours-cancel');
    const reset  = document.getElementById('hours-reset');
    const save   = document.getElementById('hours-save');
    if (!pill || !editor) return;

    function open()  { editor.hidden = false; }
    function close() { editor.hidden = true; }
    pill.addEventListener('click', open);
    cancel.addEventListener('click', close);

    addBtn.addEventListener('click', () => {
      const row = document.createElement('div');
      row.className = 'break-row';
      row.innerHTML =
        '<input type="time" class="b-start" step="60">'
      + '<input type="time" class="b-end"   step="60">'
      + '<input type="text" class="b-name" placeholder="Name" maxlength="40">'
      + '<button type="button" class="remove-btn" title="Remove">×</button>';
      list.appendChild(row);
    });

    list.addEventListener('click', (e) => {
      const btn = e.target.closest('.remove-btn');
      if (!btn) return;
      btn.closest('.break-row').remove();
    });

    reset.addEventListener('click', () => {
      // Reset clears the override entirely on save. We mark a flag and hit save.
      save.dataset.resetMode = '1';
      save.click();
    });

    function collect() {
      const start = document.getElementById('hours-start').value;
      const end   = document.getElementById('hours-end').value;
      const breaks = [...list.querySelectorAll('.break-row')].map(r => ({
        start: r.querySelector('.b-start').value,
        end:   r.querySelector('.b-end').value,
        name:  r.querySelector('.b-name').value.trim() || 'Break',
      })).filter(b => b.start && b.end);
      return { start, end, breaks };
    }

    save.addEventListener('click', async () => {
      const reset_mode = save.dataset.resetMode === '1';
      save.dataset.resetMode = '';
      const body = new FormData();
      const day = '{{ day }}';
      body.append('day', day);
      if (reset_mode) {
        body.append('reset', '1');
      } else {
        const c = collect();
        if (!c.start || !c.end || c.start >= c.end) {
          alert('Shift start must be before shift end.');
          return;
        }
        for (const b of c.breaks) {
          if (b.start >= b.end) { alert('Each break must start before it ends.'); return; }
          if (b.start < c.start || b.end > c.end) { alert('Breaks must fall within the shift.'); return; }
        }
        body.append('start', c.start);
        body.append('end',   c.end);
        for (const b of c.breaks) {
          body.append('break_start', b.start);
          body.append('break_end',   b.end);
          body.append('break_name',  b.name);
        }
      }

      const r = await fetch('/staffing/hours', { method: 'POST', body });
      if (r.ok) location.reload();
      else alert('Save failed: ' + (await r.text()));
    });
  })();
```

- [ ] **Step 2: Verify the page parses**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; e = Environment(loader=FileSystemLoader('src/zira_dashboard/templates')); e.get_template('staffing.html')"`
Expected: silent success.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/staffing.html
git commit -m "feat(staffing): hours editor open/close + add-break + reset JS"
```

### Task 16: Add `POST /staffing/hours` route handler

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`

- [ ] **Step 1: Locate the existing POST handler**

Run: `grep -n "@router.post" src/zira_dashboard/routes/staffing.py | head`

You'll see existing POST handlers in the file. Add the new one near them.

- [ ] **Step 2: Add the route**

Insert this in `src/zira_dashboard/routes/staffing.py`, near the other POST handlers. Make sure `staffing` (the module, not the router variable) is imported at top — should already be:

```python
@router.post("/staffing/hours")
async def staffing_hours_save(request: Request):
    """Persist a per-day shift override (or clear it via reset=1).

    Body fields (multipart/form-data):
      day:          ISO date (required)
      reset:        "1" → clear custom_hours and exit
      start, end:   "HH:MM" shift bookends
      break_start, break_end, break_name: parallel lists, one entry per break
    """
    form = await request.form()
    day_raw = (form.get("day") or "").strip()
    try:
        d = date.fromisoformat(day_raw)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad day"}, status_code=400)

    sched = staffing.load_schedule(d)

    if form.get("reset") == "1":
        sched.custom_hours = None
        staffing.save_schedule(sched)
        return JSONResponse({"ok": True, "reset": True})

    start_s = (form.get("start") or "").strip()
    end_s = (form.get("end") or "").strip()
    if not start_s or not end_s or start_s >= end_s:
        return JSONResponse({"ok": False, "error": "shift start must be before end"}, status_code=400)

    starts = form.getlist("break_start")
    ends   = form.getlist("break_end")
    names  = form.getlist("break_name")
    breaks_out: list[dict] = []
    for bs, be, bn in zip(starts, ends, names):
        bs, be = bs.strip(), be.strip()
        if not bs or not be or bs >= be:
            return JSONResponse({"ok": False, "error": f"bad break: {bs}–{be}"}, status_code=400)
        if bs < start_s or be > end_s:
            return JSONResponse({"ok": False, "error": f"break {bs}–{be} outside shift"}, status_code=400)
        breaks_out.append({"start": bs, "end": be, "name": (bn or "Break").strip()[:40]})

    sched.custom_hours = {"start": start_s, "end": end_s, "breaks": breaks_out}
    staffing.save_schedule(sched)
    return JSONResponse({"ok": True})
```

If `Request`, `JSONResponse`, `date`, or `staffing` aren't already imported at the top of `routes/staffing.py`, add them.

- [ ] **Step 3: Boot check**

Run: `python -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py
git commit -m "feat(staffing): POST /staffing/hours saves custom hours / resets"
```

---

## Phase 5 — Past-day confirm guard

### Task 17: Confirm popup on Save when day < today

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html`

- [ ] **Step 1: Find the existing save handler**

Find the `save.addEventListener('click', async () => { ... })` block added in Task 15.

- [ ] **Step 2: Inject the past-day confirm guard**

Right at the top of the async click handler (just inside the `async () => {` line), prepend:

```javascript
      // Past-day edits retroactively reshuffle leaderboards + player cards
      // for any window that contains this day. Make the user confirm.
      const today_iso = '{{ today }}';
      const day_iso = '{{ day }}';
      if (day_iso < today_iso) {
        if (!confirm("Editing past-day hours updates leaderboards and player cards for any window that includes " + day_iso + ". Continue?")) {
          save.dataset.resetMode = '';
          return;
        }
      }
```

(Make sure `today` is in the staffing template context — it already is, used by the existing `Today` link.)

- [ ] **Step 3: Verify the page parses**

Run: `python -c "from jinja2 import Environment, FileSystemLoader; e = Environment(loader=FileSystemLoader('src/zira_dashboard/templates')); e.get_template('staffing.html')"`
Expected: silent success.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/staffing.html
git commit -m "feat(staffing): confirm guard for past-day hours edits"
```

---

## Phase 6 — End-to-end verification

### Task 18: Restart, smoke-test, push

- [ ] **Step 1: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all PASS, including 7 new tests in `test_shift_config_for.py` and 4 in `test_staffing_custom_hours.py`.

- [ ] **Step 2: Boot check**

Run: `python -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Manually exercise the feature in a browser**

Stop the running uvicorn (Ctrl+C in its window) and re-launch via `run_dashboard.bat`.

Smoke checklist (do each):
- Open `/staffing?day=<today>` — title-bar shows "Hours · 7:00–15:00" pill in muted gray, no banner.
- Click the pill — editor opens, pre-filled with global defaults.
- Add a break: 10:00–10:30 "All-hands meeting" → Save → page reloads.
- Pill now reads "Hours · 7:00–15:00 · 4 breaks" in accent color; banner appears in the day-notes area.
- Open `/recycling` for the same day — productive intervals reflect the new break (Pallets-by-WC bars and progress chart goal line should drop in the 10:00–10:30 window).
- Click the pill again, click Reset to defaults → page reloads, pill returns to muted, banner gone.
- Edit a past day (any day before today) → Save fires the confirm popup mentioning that day.

- [ ] **Step 4: Commit any final fixes**

```bash
git status
# If anything changed during smoke, commit
```

- [ ] **Step 5: Push**

```bash
git -C C:/Users/dale.gruber/Projects/zira push
```

This triggers Railway redeploy.

---

## Self-review notes

- **Spec coverage:**
  - Bookends + break list overrides — Tasks 1–3 (data) + Task 16 (POST) + Task 14 (UI).
  - Total replacement semantics — Task 6 makes empty-list `breaks_for` return `()` (not fall back to global); Task 16's POST writes the entire override.
  - Day-aware twins (`shift_start_for`, `shift_end_for`, `breaks_for`, `productive_minutes_for`, `in_shift_on`) — Tasks 4–8.
  - `shift_elapsed_minutes` rewires internally — Task 9.
  - Cascade swap at every dashboard-side call site — Tasks 10–12.
  - First-60-min grace + active-interval cap honor custom hours — falls out of Tasks 9, 10, 12 (those are the call sites that compute it).
  - Hours pill in title-bar with two visual states — Task 14.
  - Inline editor with bookends + breaks list + Add/Remove/Reset/Cancel/Save — Tasks 14–15.
  - Banner in day-notes section when override active — Task 14.
  - Past-day confirm guard — Task 17.
  - POST `/staffing/hours` validating start<end, break orderings, breaks within shift — Task 16.

- **Placeholder scan:** Each step has either a runnable command or a complete code block. No `TODO`, `TBD`, or "similar to". The break-handling JS in Task 15 references `b.start` / `b.end` as `HH:MM` strings (consistent with the JSON shape from Task 6) and the validation in Task 16 also compares them as strings (works because `HH:MM` sorts lexicographically when zero-padded).

- **Type consistency:**
  - `custom_hours` shape is identical across reading (Task 2), writing (Task 3), `breaks_for` consumption (Task 6), `productive_minutes_for` (Task 7), POST payload (Task 16), template render (Task 14).
  - `Break` instances returned from `breaks_for` use the `(start: time, end: time, name: str)` shape from `schedule_store` — verified consistent across Tasks 6, 9, 11.
  - `eff_hours_start` / `eff_hours_end` are pre-formatted `HH:MM` strings in the template context and are written back in the same format by the POST.
