# Dashboards Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four small UI polish items on the dashboards: per-row labels show operator + WC stacked; headline switches to `pallets/hr/person`; progress chart axis labels every 30 min; top nav renames "Value Streams" → "Dashboards" and promotes "Work Centers" to a third subnav tab.

**Architecture:** Pure UI / template / one route-side calc. No data-model or storage changes. Each task is independently shippable. Subagents follow TDD: route-level tests render the page through `TestClient` and assert HTML substrings; backend calc tests are pure unit tests against the `recycling` view function math.

**Tech Stack:** FastAPI / Jinja2 templates / pytest with `httpx.AsyncClient` or FastAPI `TestClient` for route tests.

---

## File Structure

- `src/zira_dashboard/routes/value_streams.py` — modified: add `pph_per_person` calc; add `who_by_wc` to `_downtime_rows` for per-row labels
- `src/zira_dashboard/templates/recycling.html` — modified: bar_chart macro per-row label HTML + CSS; downtime widget per-row label; headline label; axis-tick interval
- `src/zira_dashboard/templates/new_vs.html` — modified: same per-row label changes; same headline label
- `src/zira_dashboard/templates/index.html` — modified: include `_value_streams_subnav.html` and pass `active_vs="work_centers"`
- `src/zira_dashboard/templates/_staffing_base.html` — modified: drop "Work Centers" link; rename "Value Streams" → "Dashboards"
- `src/zira_dashboard/templates/_value_streams_subnav.html` — modified: add "Work Centers" tab and `active_vs="work_centers"` branch
- `src/zira_dashboard/routes/dashboard.py` — modified: pass `active_vs="work_centers"` to template
- `tests/test_dashboards_polish.py` — new file with route-level + calc tests

---

### Task 1: `pallets/hr/person` calculation

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py` (recycling and new_vs handlers)
- Test: `tests/test_dashboards_polish.py`

- [ ] **Step 1: Write failing test for the per-person calc**

Append to `tests/test_dashboards_polish.py`:

```python
from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from zira_dashboard import staffing
from zira_dashboard.app import app


def test_recycling_headline_uses_per_person_rate(monkeypatch):
    # 100 units across 1.0 elapsed hour with 2 scheduled people = 50 / hr / person
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"], "Repair-2": ["Bob"]},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb, \
         patch("zira_dashboard.routes.value_streams.shift_elapsed_minutes", return_value=60):
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        s2 = Station(meter_id="m2", name="Repair-2", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=50, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
            StationTotal(s2, units=50, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        resp = client.get("/recycling")
    assert resp.status_code == 200
    html = resp.text
    # Headline label changed AND value reflects /person denominator
    assert "pallets/hr/person" in html
    assert ">50.0<" in html or ">50<" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboards_polish.py::test_recycling_headline_uses_per_person_rate -v`
Expected: FAIL — current template shows `pallets/hr` and the value is total `pallets / hour` (100), not `/ person` (50).

- [ ] **Step 3: Implement the calc**

In `src/zira_dashboard/routes/value_streams.py`, inside the `recycling` handler, after the `pallets_per_hour` line, add:

```python
people_count = sum(
    len(ops) for wc, ops in sched_for_labels.assignments.items()
    if wc != staffing.TIME_OFF_KEY and ops and wc in active_wc_names
)
pph_per_person = (
    pallets_per_hour / people_count if people_count > 0 else 0.0
)
```

Add `"pph_per_person": round(pph_per_person, 1),` to the template context.

Repeat in the `new_vs` handler (same pattern, using `stations` and the schedule loaded inside that handler).

- [ ] **Step 4: Update template label to render the new value**

In `src/zira_dashboard/templates/recycling.html`, find the headline tile rendering `pallets_per_hour`. Replace its label string `pallets/hr` with `pallets/hr/person` and replace the value binding with `{{ pph_per_person }}`. Same change in `new_vs.html`.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_dashboards_polish.py::test_recycling_headline_uses_per_person_rate -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py src/zira_dashboard/templates/recycling.html src/zira_dashboard/templates/new_vs.html tests/test_dashboards_polish.py
git commit -m "feat(dashboards): switch headline to pallets/hr/person"
```

---

### Task 2: Bar widget per-row labels (recycling)

**Files:**
- Modify: `src/zira_dashboard/templates/recycling.html` (bar_chart macro + CSS)
- Test: `tests/test_dashboards_polish.py`

- [ ] **Step 1: Write failing test asserting stacked label HTML**

Append:

```python
def test_recycling_bar_row_renders_person_and_wc_stacked(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"]},
    ))
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
        html = client.get("/recycling").text
    assert "name-primary" in html
    assert "name-secondary" in html
    assert "Alice" in html and "Repair-1" in html


def test_recycling_bar_row_no_assignment_fallback(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True, assignments={},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb:
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=20, reading_count=1, truncated=False, downtime_minutes=0,
                         active_minutes=60, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        html = client.get("/recycling").text
    assert "(no assignment)" in html
    assert "Repair-1" in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_dashboards_polish.py -k bar_row -v`
Expected: FAIL — neither `name-primary` class nor "(no assignment)" text exists yet.

- [ ] **Step 3: Update the bar_chart macro to emit stacked label**

In `src/zira_dashboard/templates/recycling.html`, locate the bar_chart macro (currently around line 580). Replace this single block:

```html
<div class="name">{{ b.who if b.who else b.name }}</div>
```

With:

```html
<div class="name">
  {% if b.who and b.who != b.name %}
    <span class="name-primary">{{ b.who }}</span>
    <span class="name-secondary">{{ b.name }}</span>
  {% else %}
    <span class="name-primary">{{ b.name }}</span>
    <span class="name-secondary"><em>(no assignment)</em></span>
  {% endif %}
</div>
```

Apply the same replacement inside the vertical bar (`vbars`) block (`<div class="vbar-name">{{ b.who if b.who else b.name }}</div>`):

```html
<div class="vbar-name">
  {% if b.who and b.who != b.name %}
    <span class="name-primary">{{ b.who }}</span>
    <span class="name-secondary">{{ b.name }}</span>
  {% else %}
    <span class="name-primary">{{ b.name }}</span>
    <span class="name-secondary"><em>(no assignment)</em></span>
  {% endif %}
</div>
```

- [ ] **Step 4: Add CSS for the new spans**

In the same template's `<style>` block, add near the existing `.bar-row .name` rule:

```css
.bar-row .name { display: flex; flex-direction: column; line-height: 1.15; }
.name-primary  { font-weight: 500; }
.name-secondary{ color: var(--muted); font-size: 0.78em; }
.name-secondary em { font-style: italic; }
.vbar-name     { display: flex; flex-direction: column; align-items: center; line-height: 1.1; }
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_dashboards_polish.py -k bar_row -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/templates/recycling.html tests/test_dashboards_polish.py
git commit -m "feat(dashboards): stack person+WC in bar widget rows"
```

---

### Task 3: Downtime widget per-row labels

**Files:**
- Modify: `src/zira_dashboard/routes/value_streams.py` (`_downtime_rows`)
- Modify: `src/zira_dashboard/templates/recycling.html` (downtime widget block)
- Test: `tests/test_dashboards_polish.py`

- [ ] **Step 1: Write failing test for downtime stacked labels**

```python
def test_recycling_downtime_row_renders_person_and_wc_stacked(monkeypatch):
    monkeypatch.setattr(staffing, "load_schedule", lambda d: staffing.Schedule(
        day=d, published=True,
        assignments={"Repair-1": ["Alice"]},
    ))
    with patch("zira_dashboard.routes.value_streams.leaderboard") as lb:
        from zira_dashboard.leaderboard import StationTotal
        from zira_dashboard.stations import Station
        s1 = Station(meter_id="m1", name="Repair-1", category="Repair", cell="Recycling")
        lb.return_value = [
            StationTotal(s1, units=5, reading_count=1, truncated=False, downtime_minutes=12,
                         active_minutes=48, last_reading_at=None, last_status=None,
                         samples=(), active_intervals=()),
        ]
        client = TestClient(app)
        html = client.get("/recycling").text
    # downtime widget rendered with stacked label
    downtime_block = html.split("downtime-widget", 1)[-1] if "downtime-widget" in html else html
    assert "Alice" in downtime_block and "Repair-1" in downtime_block
```

(If the downtime widget has no stable selector to slice on, change the assertion to count occurrences of "Alice" / "Repair-1" appearing more than once in the page — once in the bar widget, once in the downtime widget.)

- [ ] **Step 2: Run test, verify FAIL**

Run: `pytest tests/test_dashboards_polish.py -k downtime_row -v`

- [ ] **Step 3: Add `who` to `_downtime_rows`**

In `routes/value_streams.py`, change `_downtime_rows`:

```python
def _downtime_rows(items: list) -> list[dict]:
    out = []
    for r in items:
        working = max(0, elapsed - r.downtime_minutes)
        total = elapsed if elapsed else 1
        out.append(
            {
                "name": r.station.name,
                "who": who_by_wc.get(r.station.name),
                "working": working,
                "down": r.downtime_minutes,
                "working_pct": working / total * 100.0,
                "down_pct": r.downtime_minutes / total * 100.0,
            }
        )
    return out
```

- [ ] **Step 4: Update the downtime widget HTML in `recycling.html`**

Find the downtime widget block (around line where `downtime_rows` are iterated). Replace:

```html
<div class="name">{{ d.name }}</div>
```

with the same stacked pattern from Task 2:

```html
<div class="name">
  {% if d.who and d.who != d.name %}
    <span class="name-primary">{{ d.who }}</span>
    <span class="name-secondary">{{ d.name }}</span>
  {% else %}
    <span class="name-primary">{{ d.name }}</span>
    <span class="name-secondary"><em>(no assignment)</em></span>
  {% endif %}
</div>
```

- [ ] **Step 5: Run, verify PASS**

Run: `pytest tests/test_dashboards_polish.py -k downtime_row -v`

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/value_streams.py src/zira_dashboard/templates/recycling.html tests/test_dashboards_polish.py
git commit -m "feat(dashboards): stack person+WC in downtime widget rows"
```

---

### Task 4: New VS template gets the same row treatment

**Files:**
- Modify: `src/zira_dashboard/templates/new_vs.html`

- [ ] **Step 1: Apply the same label replacement**

Replace `<div class="name">{{ b.who if b.who else b.name }}</div>` (around line 109) with the stacked block from Task 2. Replace `<div class="name">{{ d.name }}</div>` (around line 131) with the stacked block from Task 3 (using `d.who`).

Add the same CSS rules (`.name-primary`, `.name-secondary`, `.bar-row .name { display: flex; flex-direction: column; line-height: 1.15; }`) to the `<style>` block in this template.

- [ ] **Step 2: Manual verify**

Start the dev server, hit `/new-vs`, confirm rows render with person + WC stacked. (If `new_vs` has no scheduled stations today, hit it on a day where there is data via `?day=` to validate visually.)

- [ ] **Step 3: Add `who` to the `new_vs` handler's downtime data shape**

The `new_vs` handler in `routes/value_streams.py` builds its own bars/rows. Ensure it produces a `who` field in downtime rows just like Task 3 did for `recycling`. Look for the `downtime_rows` construction in the handler and add `"who": who_by_wc.get(...)`. If the handler doesn't have a `who_by_wc` map yet, copy the same construction from the `recycling` handler.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/new_vs.html src/zira_dashboard/routes/value_streams.py
git commit -m "feat(dashboards): stack person+WC on new_vs page"
```

---

### Task 5: 30-min progress chart axis ticks

**Files:**
- Modify: `src/zira_dashboard/templates/recycling.html` (progress chart axis loop)

- [ ] **Step 1: Locate the progress chart axis label loop**

Search `recycling.html` for the progress chart rendering — the loop that iterates `dismantler_progress` / `repair_progress` buckets and emits axis tick labels. The current pattern labels every 4th bucket (every hour from 15-min buckets).

Look for a Jinja conditional like `{% if loop.index0 % 4 == 0 %}` or similar.

- [ ] **Step 2: Change the modulo from 4 to 2**

Change `% 4 == 0` to `% 2 == 0`. If labels are rendered with a different pattern, find the equivalent control point (might be inline CSS `nth-child(4n+1)` — change to `2n+1`).

- [ ] **Step 3: Manual verify**

Hit `/recycling`, confirm the progress chart axis shows a label every other 15-min bucket (so every 30 min).

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/recycling.html
git commit -m "feat(dashboards): show progress chart axis label every 30 min"
```

---

### Task 6: Top nav rename + Work Centers tab promotion

**Files:**
- Modify: `src/zira_dashboard/templates/_staffing_base.html`
- Modify: `src/zira_dashboard/templates/_value_streams_subnav.html`
- Test: `tests/test_dashboards_polish.py`

- [ ] **Step 1: Write failing test asserting nav structure**

```python
def test_top_nav_renamed_and_work_centers_dropped():
    client = TestClient(app)
    html = client.get("/recycling").text
    assert ">Dashboards<" in html
    # The top-nav "Work Centers" link is gone (subnav still has it)
    # We can assert by counting: there should be exactly one "Work Centers" string,
    # and it should be inside the subnav (next to "Recycling VS" / "New VS").
    assert html.count("Work Centers") == 1
    assert ">Recycling VS<" in html
    assert ">New VS<" in html


def test_work_centers_subnav_active_on_index():
    client = TestClient(app)
    html = client.get("/").text
    # subnav appears on the index page
    assert ">Recycling VS<" in html
    assert ">New VS<" in html
    assert ">Work Centers<" in html
    # "Work Centers" tab is active
    import re
    m = re.search(r'class="[^"]*active[^"]*"[^>]*>\s*Work Centers', html)
    assert m, "Work Centers tab should be active on index page"
```

- [ ] **Step 2: Run, verify FAIL**

Run: `pytest tests/test_dashboards_polish.py -k nav -v`

- [ ] **Step 3: Update `_staffing_base.html`**

Find:
```html
<a href="/">Work Centers</a>
<a href="/recycling">Value Streams</a>
```

Replace with:
```html
<a href="/recycling">Dashboards</a>
```

- [ ] **Step 4: Update `_value_streams_subnav.html`**

Current:
```html
<a href="/recycling" class="{% if active_vs == 'recycling' %}active{% endif %}">Recycling VS</a>
<a href="/new-vs"    class="{% if active_vs == 'new'        %}active{% endif %}">New VS</a>
```

Add a third link:
```html
<a href="/recycling" class="{% if active_vs == 'recycling' %}active{% endif %}">Recycling VS</a>
<a href="/new-vs"    class="{% if active_vs == 'new'        %}active{% endif %}">New VS</a>
<a href="/"          class="{% if active_vs == 'work_centers' %}active{% endif %}">Work Centers</a>
```

- [ ] **Step 5: Update `routes/dashboard.py` to pass `active_vs`**

In the `index` handler, add `"active_vs": "work_centers"` to the template context dict.

- [ ] **Step 6: Update `templates/index.html` to include the subnav**

Find the place in `index.html` where the page body starts (after `_staffing_base.html` blocks if any). Add (or move) the include:

```html
{% include "_value_streams_subnav.html" %}
```

If `index.html` doesn't already extend `_staffing_base.html` or include the subnav, add the include just below the top nav. Match the position used by `recycling.html` and `new_vs.html`.

- [ ] **Step 7: Run, verify PASS**

Run: `pytest tests/test_dashboards_polish.py -k nav -v`

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/templates/_staffing_base.html src/zira_dashboard/templates/_value_streams_subnav.html src/zira_dashboard/templates/index.html src/zira_dashboard/routes/dashboard.py tests/test_dashboards_polish.py
git commit -m "feat(dashboards): rename nav to Dashboards and promote Work Centers to subnav"
```

---

### Task 7: Final polish pass — full-page rendering smoke test

**Files:**
- Test: `tests/test_dashboards_polish.py`

- [ ] **Step 1: Add a smoke test that all three dashboard pages render**

```python
def test_all_three_dashboard_pages_render_200():
    client = TestClient(app)
    for path in ("/", "/recycling", "/new-vs"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} returned {resp.status_code}"
        # subnav is present on all three
        assert ">Recycling VS<" in resp.text
        assert ">New VS<" in resp.text
        assert ">Work Centers<" in resp.text
        # top nav rename
        assert ">Dashboards<" in resp.text
```

- [ ] **Step 2: Run full test file**

Run: `pytest tests/test_dashboards_polish.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_dashboards_polish.py
git commit -m "test(dashboards): full-page render smoke for polish bundle"
```

---

## Done criteria

All seven tasks committed; `pytest tests/test_dashboards_polish.py -v` is green; manual visual check on `/`, `/recycling`, `/new-vs` confirms:

- Bars and downtime rows show person on top, WC grayed below; (no assignment) fallback renders italic
- Headline reads `pallets/hr/person` with the per-person value
- Progress chart axis labels appear every 30 min
- Top nav reads "Dashboards"; "Work Centers" link is gone from top
- Subnav: Recycling VS / New VS / Work Centers — visible and clickable on all three
