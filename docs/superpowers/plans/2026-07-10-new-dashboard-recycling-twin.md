# New Department Recycling-Twin Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/new` with the approved screenshot-matched, range-aware, editable Recycling-style dashboard powered initially by Junior #2's Zira data.

**Architecture:** Generalize the existing Recycling daily-data function just enough to compute named department groups, then wrap it with a behavior-preserving Recycling adapter and a new single-group New adapter. Reuse the existing range aggregator, GridStack persistence APIs, shared dashboard JavaScript, Recycling CSS, and extracted widget macros so the two pages render identical widget behavior while retaining independent `recycling` and `new` layout namespaces.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, Postgres/psycopg2, GridStack, vanilla JavaScript, pytest, Ruff.

## Global Constraints

- The desktop default is the supplied screenshot composition: stacked KPIs at left, New Work Centers in the center, Downtime Report at right, then full-width 15-minute and Daily Progress charts.
- The desktop toolbar contains Today, Yesterday, This Week, Last Week, This Month, Last Month, and Custom.
- Every desktop widget is draggable, resizable, editable, resettable, and auto-saved exactly like Recycling.
- New layout/customization persistence uses page key `new`; it must not read or overwrite page key `recycling`.
- Daily Progress uses cumulative bars plus a target line; it is not an area chart.
- Do not add an Unplanned Stops widget.
- Runtime Zira access remains limited to readings fields `units`, `event_date`, `status`, and `duration`; never fetch, log, or persist the raw data-source schema.
- Junior #2 is the sole New Zira station at launch. Future New locations appear automatically only after their `Location.meter_id` is configured.
- Preserve `/new?day=YYYY-MM-DD`, `/new-vs`, `/tv/new`, and `/tv/new-vs` compatibility.
- No new runtime dependencies.

---

## File Structure

### Create

- `src/zira_dashboard/templates/_department_dashboard_widgets.html` — shared production-bar, 15-minute-progress, and downtime macros extracted from Recycling.
- `tests/test_new_dashboard_data.py` — New daily/range calculation tests.
- `tests/test_new_dashboard_template.py` — toolbar, GridStack, layout, editor, and chart-markup tests.
- `scripts/preview_new_dashboard.py` — deterministic static preview for desktop and TV visual verification.

### Modify

- `src/zira_dashboard/routes/departments.py` — generic daily computation, New adapter, range-aware New route/context.
- `src/zira_dashboard/recycling_data.py` — parameterize downtime-row category selection while retaining the Recycling default.
- `src/zira_dashboard/templates/recycling.html` — consume extracted shared widget macros with byte-equivalent structure.
- `src/zira_dashboard/templates/new_dept.html` — replace static panels with the approved GridStack dashboard.
- `src/zira_dashboard/static/dashboard-grid.js` — documentation comment only, adding `new` to the supported page keys; behavior stays generic.
- `tests/test_tv_dashboards_vs.py` — New TV/static-grid coverage.
- `tests/test_recycling_toolbar_static.py` — shared-macro regression coverage.
- `tests/test_dashboards_polish.py` — New screen integration assertions.

---

### Task 1: Generalize daily department data without changing Recycling

**Files:**
- Modify: `src/zira_dashboard/routes/departments.py:139-408`
- Create: `tests/test_new_dashboard_data.py`
- Test: `tests/test_recycling_data.py`
- Test: `tests/test_dashboards_polish.py`

**Interfaces:**
- Consumes: existing `leaderboard`, `assignment_windows.resolve_segments`, `compute_per_wc_expected`, `progress_buckets`, and schedule/attendance helpers.
- Produces: `_department_day_data(d, now, is_today_d, *, stations, labor_department, group_categories, align_to_standard=False) -> dict`, `_recycling_day_data(d, now, is_today_d, align_to_standard=False) -> dict`, and `_new_day_data(d, now, is_today_d, align_to_standard=False) -> dict`.

- [ ] **Step 1: Add failing wrapper/shape tests**

Create `tests/test_new_dashboard_data.py` with focused adapter tests:

```python
from datetime import date, datetime, UTC
from types import SimpleNamespace

from zira_dashboard.routes import departments


DAY = date(2026, 7, 10)
NOW = datetime(2026, 7, 10, 15, 0, tzinfo=UTC)


def test_new_day_data_uses_one_new_group(monkeypatch):
    captured = {}

    def fake_compute(d, now, is_today_d, **kwargs):
        captured.update(kwargs)
        return {"group_buckets": {"New": []}}

    monkeypatch.setattr(departments, "_department_day_data", fake_compute)
    monkeypatch.setattr(
        departments.staffing,
        "LOCATIONS",
        (SimpleNamespace(
            name="Junior #2", skill="Junior", department="New",
            meter_id="42345",
        ),),
    )
    monkeypatch.setattr(
        departments.work_centers_store,
        "department",
        lambda loc: "New",
    )

    result = departments._new_day_data(DAY, NOW, True)

    assert captured["labor_department"] == "New"
    assert captured["group_categories"] == ("New",)
    assert [s.name for s in captured["stations"]] == ["Junior #2"]
    assert captured["stations"][0].category == "New"
    assert result["group_buckets"] == {"New": []}


def test_recycling_wrapper_preserves_two_groups(monkeypatch):
    captured = {}

    def fake_compute(d, now, is_today_d, **kwargs):
        captured.update(kwargs)
        return {"group_buckets": {"Dismantler": [], "Repair": []}}

    monkeypatch.setattr(departments, "_department_day_data", fake_compute)
    monkeypatch.setattr(departments, "recycling_stations", lambda: [])

    departments._recycling_day_data(DAY, NOW, True)

    assert captured["labor_department"] == "Recycled"
    assert captured["group_categories"] == ("Dismantler", "Repair")
```

- [ ] **Step 2: Run the focused tests and confirm the missing interface**

Run:

```bash
.venv/bin/pytest tests/test_new_dashboard_data.py -v
```

Expected: FAIL because `_department_day_data` and `_new_day_data` do not exist.

- [ ] **Step 3: Generalize `_recycling_day_data` and add thin adapters**

In `routes/departments.py`, rename the current implementation to this interface:

```python
def _department_day_data(
    d,
    now,
    is_today_d,
    *,
    stations: list[Station],
    labor_department: str,
    group_categories: tuple[str, ...],
    align_to_standard: bool = False,
):
    results = leaderboard(client, stations, d, now_utc=now if is_today_d else None)
```

Move the existing `_recycling_day_data` body beginning with
`sched = staffing.load_schedule(d)` and ending at its returned dictionary into
this function. Apply every substitution shown below and make no other semantic
change to that moved block.

Make these exact mechanical substitutions inside the moved body:

```python
# Delete: stations = recycling_stations()

# Replace the labor filter:
if loc.department != labor_department:
    continue

# Replace hard-coded dismantler/repair lists:
group_results = {
    category: sorted(
        [r for r in active_results if r.station.category == category],
        key=lambda r: r.station.name,
    )
    for category in group_categories
}

# Replace two target/progress calls:
group_buckets = {
    category: progress_buckets(
        rows,
        d,
        now,
        target_fn=_make_target_fn(rows),
        align_to_standard=align_to_standard,
    )
    for category, rows in group_results.items()
}

# Return this generic key instead of dism_buckets/repair_buckets:
"group_buckets": group_buckets,
```

Add wrappers immediately below it:

```python
def _recycling_day_data(d, now, is_today_d, align_to_standard=False):
    data = _department_day_data(
        d,
        now,
        is_today_d,
        stations=recycling_stations(),
        labor_department="Recycled",
        group_categories=("Dismantler", "Repair"),
        align_to_standard=align_to_standard,
    )
    data["dism_buckets"] = data["group_buckets"]["Dismantler"]
    data["repair_buckets"] = data["group_buckets"]["Repair"]
    return data


def _new_stations() -> list[Station]:
    return [
        Station(
            meter_id=loc.meter_id,
            name=loc.name,
            category="New",
            cell="New",
        )
        for loc in staffing.LOCATIONS
        if work_centers_store.department(loc) == "New" and loc.meter_id
    ]


def _new_day_data(d, now, is_today_d, align_to_standard=False):
    return _department_day_data(
        d,
        now,
        is_today_d,
        stations=_new_stations(),
        labor_department="New",
        group_categories=("New",),
        align_to_standard=align_to_standard,
    )
```

Do not alter the Zira client or introduce a schema call.

- [ ] **Step 4: Update direct Recycling bucket-key tests**

Where tests construct `_recycling_day_data` dictionaries, retain
`dism_buckets` and `repair_buckets`; wrappers preserve those compatibility
keys. Add `group_buckets` only to tests that call `_department_day_data`
directly.

- [ ] **Step 5: Run focused data regressions**

Run:

```bash
.venv/bin/pytest tests/test_new_dashboard_data.py tests/test_recycling_data.py tests/test_productive_minutes_window.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit the data adapter**

```bash
git add src/zira_dashboard/routes/departments.py tests/test_new_dashboard_data.py
git commit -m "refactor: share department dashboard day data"
```

---

### Task 2: Make `/new` range-aware and aggregate Junior #2 data

**Files:**
- Modify: `src/zira_dashboard/routes/departments.py:690-944`
- Modify: `src/zira_dashboard/recycling_data.py:126-158`
- Modify: `tests/test_new_dashboard_data.py`

**Interfaces:**
- Consumes: `_new_day_data`, `recycling_range.aggregate_range`, `aggregate_buckets`, `build_bars`, `build_downtime_rows`.
- Produces: `_render_new_dept(request, *, window, start, end, day, tv_mode, tv_theme)` and complete New template context.

- [ ] **Step 1: Add failing route/range tests**

Append tests that isolate rendering by monkeypatching `_new_day_data` and the
template renderer:

```python
def _empty_new_day():
    return {
        "total_units": 0,
        "total_downtime": 0,
        "elapsed": 0,
        "available": 0,
        "uptime_minutes": 0,
        "total_man_hours": 0.0,
        "total_recycling_people": 0,
        "per_wc_units": {},
        "per_wc_downtime": {},
        "per_wc_expected": {},
        "per_wc_who": {},
        "per_wc_state": {},
        "per_wc_category": {},
        "per_wc_station_obj": {},
        "active_wc_names": set(),
        "schedule_assignments": {},
        "group_buckets": {"New": []},
        "shift_start_label": "07:00",
    }


def test_new_legacy_day_becomes_single_day_range(monkeypatch):
    seen = []
    monkeypatch.setattr(
        departments,
        "_new_day_data",
        lambda d, *args, **kwargs: seen.append(d) or _empty_new_day(),
    )
    # Exercise through TestClient after the route signature changes.
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    response = TestClient(app).get("/new?day=2026-07-08")
    assert response.status_code == 200
    assert seen == [date(2026, 7, 8)]
    assert 'class="rc-chip rc-chip-on">Custom:' in response.text


def test_new_week_fans_out_inclusive_days(monkeypatch):
    seen = []
    monkeypatch.setattr(
        departments,
        "_new_day_data",
        lambda d, *args, **kwargs: seen.append(d) or _empty_new_day(),
    )
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app

    response = TestClient(app).get(
        "/new?start=2026-07-06&end=2026-07-08"
    )
    assert response.status_code == 200
    assert seen == [date(2026, 7, 6), date(2026, 7, 7), date(2026, 7, 8)]


def test_downtime_rows_can_select_new_category():
    from zira_dashboard.recycling_data import build_downtime_rows

    rows = build_downtime_rows(
        agg_active_names={"Junior #2", "Repair 1"},
        agg_category={"Junior #2": "New", "Repair 1": "Repair"},
        agg_downtime={"Junior #2": 12, "Repair 1": 4},
        total_elapsed=60,
        agg_who_today={"Junior #2": "Lauro", "Repair 1": "Alice"},
        is_range=False,
        categories=("New",),
    )

    assert [row["name"] for row in rows] == ["Junior #2"]
    assert rows[0]["down"] == 12
```

- [ ] **Step 2: Run the tests and confirm the old single-day route fails**

Run:

```bash
.venv/bin/pytest tests/test_new_dashboard_data.py -v
```

Expected: the range assertions fail because `/new` only accepts `day`.

- [ ] **Step 3: Parameterize the downtime-row category filter**

Change `recycling_data.build_downtime_rows` without changing its existing
callers:

```python
def build_downtime_rows(
    *,
    agg_active_names,
    agg_category: dict,
    agg_downtime: dict,
    total_elapsed: float,
    agg_who_today: dict,
    is_range: bool,
    categories: tuple[str, ...] = ("Dismantler", "Repair"),
) -> list[dict]:
    names = sorted(
        name
        for name in agg_active_names
        if agg_category.get(name) in categories
    )
    out = []
    for name in names:
        down = agg_downtime.get(name, 0)
        working = max(0, total_elapsed - down)
        total = total_elapsed if total_elapsed else 1
        out.append({
            "name": name,
            "who": agg_who_today.get(name) if not is_range else None,
            "working": working,
            "down": down,
            "working_pct": working / total * 100.0,
            "down_pct": down / total * 100.0,
        })
    return out
```

- [ ] **Step 4: Replace the New route signature and compatibility mapping**

Use this route shape:

```python
@router.get("/new", response_class=HTMLResponse)
def new_dept(
    request: Request,
    window: str = Query(default="today"),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    day: str | None = Query(default=None),
):
    if day and not (start and end):
        start = end = day
    return _render_new_dept(
        request,
        window=window,
        start=start,
        end=end,
        day=day,
        tv_mode=False,
        tv_theme="dark",
    )
```

Change `_render_new_dept` to resolve and cache ranges exactly like Recycling:

```python
today = plant_today()
start_d, end_d, custom_range_active = resolve_range(
    window, start, end, today
)
is_today = start_d == end_d == today
is_range = start_d != end_d
range_includes_today = start_d <= today <= end_d
cache_key = (
    "new_dept", start_d.isoformat(), end_d.isoformat(), tv_mode, tv_theme
)
```

Build inclusive `days`, use `_RANGE_POOL.map` when more than one day, then:

```python
aggregate = recycling_range.aggregate_range(per_day, days, is_range=is_range)
new_progress = aggregate_buckets([
    item["group_buckets"]["New"] for item in per_day
])
customs_all = widget_customizer.load_all("new")
new_bars = build_bars(
    "New",
    agg_active_names=aggregate.agg_active_names,
    agg_category=aggregate.agg_category,
    agg_units=aggregate.agg_units,
    agg_expected=aggregate.agg_expected,
    agg_who_today=aggregate.agg_who_today,
    is_range=is_range,
    agg_downtime=aggregate.agg_downtime,
)
new_bars = sort_bars(
    new_bars,
    "new-bars",
    customs_all=customs_all,
)
downtime_rows = build_downtime_rows(
    agg_active_names=aggregate.agg_active_names,
    agg_category=aggregate.agg_category,
    agg_downtime=aggregate.agg_downtime,
    total_elapsed=aggregate.total_elapsed,
    agg_who_today=aggregate.agg_who_today,
    is_range=is_range,
    categories=("New",),
)
```

Calculate the page-level values explicitly:

```python
total_units = aggregate.total_units
total_elapsed = aggregate.total_elapsed
total_available = aggregate.total_available
total_uptime_minutes = aggregate.total_uptime_minutes
total_man_hours = aggregate.total_man_hours
uptime_pct = (
    total_uptime_minutes / total_available * 100.0
    if total_available > 0 else 0.0
)
pph_per_person = (
    total_units / total_man_hours if total_man_hours > 0 else 0.0
)
elapsed_hours_total = total_elapsed / 60.0 if total_elapsed else 0.0
new_group_target = group_goal(
    "New",
    elapsed_hours_total=elapsed_hours_total,
    agg_expected=aggregate.agg_expected,
    agg_category=aggregate.agg_category,
)
new_people = sum(item["total_recycling_people"] for item in per_day)
operator_links_by_wc = {
    wc_name: wc_dashboard_data.dashboard_url_for_wc_day(wc_name, end_d)
    for wc_name in aggregate.agg_active_names
}
```

Pass these exact new context keys:

```python
{
    "window": window,
    "start": start_d.isoformat(),
    "end": end_d.isoformat(),
    "custom_range_active": custom_range_active,
    "is_range": is_range,
    "range_includes_today": range_includes_today,
    "total_units": total_units,
    "pph_per_person": round(pph_per_person, 1),
    "new_bars": new_bars,
    "new_progress": new_progress,
    "new_group_target": new_group_target,
    "new_people": new_people,
    "downtime_rows": downtime_rows,
    "uptime_pct": uptime_pct,
    "layout": layout_store.layout_map("new"),
    "layout_key": "new",
    "customs": widget_customizer.load_all("new"),
}
```

- [ ] **Step 5: Update TV calls to the new renderer contract**

`tv_new_dept` passes `window="today"`, `start=None`, `end=None`, and
`day=None`. Keep `/tv/new-vs` as a 301 that preserves its query string.

- [ ] **Step 6: Run route/range tests**

Run:

```bash
.venv/bin/pytest tests/test_new_dashboard_data.py tests/test_tv_dashboards_vs.py -v
```

Expected: all pass or only existing database-gated skips.

- [ ] **Step 7: Commit range-aware New data**

```bash
git add src/zira_dashboard/routes/departments.py src/zira_dashboard/recycling_data.py tests/test_new_dashboard_data.py tests/test_tv_dashboards_vs.py
git commit -m "feat: add range-aware new department data"
```

---

### Task 3: Extract shared department widget renderers

**Files:**
- Create: `src/zira_dashboard/templates/_department_dashboard_widgets.html`
- Modify: `src/zira_dashboard/templates/recycling.html:24-255,324-365`
- Modify: `tests/test_recycling_toolbar_static.py`

**Interfaces:**
- Consumes: template context `customs`, `is_range`, `tv_mode`, operator labels/links, and existing bar/progress row dictionaries.
- Produces: Jinja macros `department_bar_chart`, `department_progress_chart`, and `department_downtime_report`.

- [ ] **Step 1: Add failing extraction-regression tests**

Append:

```python
SHARED = ROOT / "src" / "zira_dashboard" / "templates" / "_department_dashboard_widgets.html"


def test_recycling_uses_shared_department_widget_macros():
    html = TEMPLATE.read_text(encoding="utf-8")
    shared = SHARED.read_text(encoding="utf-8")
    assert '_department_dashboard_widgets.html' in html
    assert "macro department_bar_chart" in shared
    assert "macro department_progress_chart" in shared
    assert "macro department_downtime_report" in shared


def test_shared_daily_progress_stays_bar_based():
    shared = SHARED.read_text(encoding="utf-8")
    assert 'class="bars"' in shared
    assert "cumulative_progress_chart" in TEMPLATE
```

- [ ] **Step 2: Verify the shared partial is missing**

Run:

```bash
.venv/bin/pytest tests/test_recycling_toolbar_static.py -v
```

Expected: FAIL because `_department_dashboard_widgets.html` does not exist.

- [ ] **Step 3: Move the existing macros without changing markup**

Create `_department_dashboard_widgets.html` by moving, not rewriting, the
existing `bar_chart` and `progress_chart` macro bodies from `recycling.html`.
Use these signatures:

```jinja2
{% macro department_bar_chart(widget_id, items) -%}
{%- endmacro %}

{% macro department_progress_chart(
    buckets, group_target_per_hour, bucket_target, widget_id=''
) -%}
{%- endmacro %}
```

Move the Downtime Report widget body into:

```jinja2
{% macro department_downtime_report(
    rows, elapsed_minutes, uptime_pct, widget_id='downtime-report'
) -%}
{%- endmacro %}
```

For `department_bar_chart`, the body is the current `bar_chart` statement
range beginning with `set c = customs.get(widget_id, {})` and ending with the
closing `widget-total` div. For `department_progress_chart`, the body begins
with `set pc = customs.get(widget_id, {})` and ends with the closing `progress`
div. For `department_downtime_report`, move the current downtime widget's
statement range beginning with `set total_down = downtime_rows |
sum(attribute='down')` and ending after `downtime-vbars`, then change the local
collection name from `downtime_rows` to the `rows` parameter. These are
mechanical moves of the existing tested Jinja; no HTML tags, classes, style
expressions, or conditions change.

At the top of `recycling.html`, import with context:

```jinja2
{% from "_department_dashboard_widgets.html" import
   department_bar_chart,
   department_progress_chart,
   department_downtime_report
   with context %}
```

Replace calls one-for-one:

```jinja2
{{ department_bar_chart('dismantler-bars', dismantler_bars) }}
{{ department_bar_chart('repair-bars', repair_bars) }}
{{ department_progress_chart(
    dismantler_progress,
    dismantler_group_target,
    (dismantler_group_target / 4)|round|int,
    'dismantler-progress'
) }}
{{ department_downtime_report(
    downtime_rows, elapsed_minutes, uptime_pct, 'downtime-report'
) }}
```

Do not edit `_cumulative_progress_chart.html`; continue calling it from
the page widgets.

- [ ] **Step 4: Run Recycling template and integration regressions**

Run:

```bash
.venv/bin/pytest tests/test_recycling_toolbar_static.py tests/test_dashboards_polish.py -v
```

Expected: all pass or database-gated skips.

- [ ] **Step 5: Commit the presentation extraction**

```bash
git add src/zira_dashboard/templates/_department_dashboard_widgets.html src/zira_dashboard/templates/recycling.html tests/test_recycling_toolbar_static.py
git commit -m "refactor: share department dashboard widgets"
```

---

### Task 4: Build the approved editable New dashboard template

**Files:**
- Replace: `src/zira_dashboard/templates/new_dept.html`
- Create: `tests/test_new_dashboard_template.py`
- Modify: `src/zira_dashboard/static/dashboard-grid.js:1-12`
- Modify: `tests/test_dashboards_polish.py`

**Interfaces:**
- Consumes: Task 2 context and Task 3 macros.
- Produces: GridStack items `kpi-pallets`, `kpi-palletshr`, `new-bars`, `downtime-report`, `new-progress`, `new-cumulative` under page key `new`.

- [ ] **Step 1: Add static failing template tests**

Create:

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "zira_dashboard" / "templates" / "new_dept.html"


def _html():
    return TEMPLATE.read_text(encoding="utf-8")


def test_new_has_full_recycling_range_toolbar():
    html = _html()
    for label in (
        "Today", "Yesterday", "This Week", "Last Week",
        "This Month", "Last Month", "Custom",
    ):
        assert label in html
    assert '<form class="rc-toolbar"' in html
    assert '<div class="edit-bar">' in html


def test_new_is_independent_editable_gridstack_page():
    html = _html()
    assert "/static/vendor/gridstack.min.css" in html
    assert "/static/vendor/gridstack-all.js" in html
    assert "/static/dashboard-grid.js" in html
    assert 'data-layout-page="new"' in html
    assert 'id="reset-layout"' in html


def test_new_default_layout_matches_reference():
    html = _html()
    expected = {
        "kpi-pallets": (0, 0, 2, 3),
        "kpi-palletshr": (0, 3, 2, 3),
        "new-bars": (2, 0, 5, 6),
        "downtime-report": (7, 0, 5, 6),
        "new-progress": (0, 6, 12, 5),
        "new-cumulative": (0, 11, 12, 5),
    }
    for widget_id, defaults in expected.items():
        assert f"widget_attrs('{widget_id}', {', '.join(map(str, defaults))})" in html


def test_new_daily_progress_is_cumulative_bars_and_no_stop_widget():
    html = _html()
    assert "cumulative_progress_chart(new_progress)" in html
    assert "Unplanned Stops" not in html
```

- [ ] **Step 2: Run the static tests and verify the static page fails**

Run:

```bash
.venv/bin/pytest tests/test_new_dashboard_template.py -v
```

Expected: FAIL on toolbar, GridStack, and widget IDs.

- [ ] **Step 3: Replace `new_dept.html` with the approved structure**

Use the same document head and screen/TV branching as `recycling.html`:

```jinja2
<link rel="stylesheet" href="/static/vendor/gridstack.min.css?v={{ static_v('vendor/gridstack.min.css') }}">
<link rel="stylesheet" href="/static/recycling.css?v={{ static_v('recycling.css') }}">
{% from "_widget_edit_controls.html" import edit_controls with context %}
{% from "_department_dashboard_widgets.html" import
   department_bar_chart,
   department_progress_chart,
   department_downtime_report
   with context %}
{% from "_cumulative_progress_chart.html" import cumulative_progress_chart %}
```

Copy the Recycling `rc-toolbar` exactly, preserving the named windows and
Custom popover. Define the standard layout macro and the approved grid:

```jinja2
<div class="grid-stack"
     data-layout-page="new"
     data-tv-mode="{{ '1' if tv_mode else '0' }}"
     data-fallback-rows="16">

  <div class="grid-stack-item" {{ widget_attrs('kpi-pallets', 0, 0, 2, 3) }}>
    <div class="grid-stack-item-content align-center">
      {% if not tv_mode %}{{ edit_controls('kpi-pallets', 'Total Pallets Processed', 'kpi') }}{% endif %}
      <div class="label">{{ widget_title('kpi-pallets', 'Total Pallets Processed') }}</div>
      <div class="val">{{ '{:,}'.format(total_units) }}</div>
    </div>
  </div>

  <div class="grid-stack-item" {{ widget_attrs('kpi-palletshr', 0, 3, 2, 3) }}>
    <div class="grid-stack-item-content align-center">
      {% if not tv_mode %}{{ edit_controls('kpi-palletshr', 'pallets/hr/person', 'kpi') }}{% endif %}
      <div class="label">{{ widget_title('kpi-palletshr', 'pallets/hr/person') }}</div>
      <div class="val">{{ pph_per_person }}</div>
    </div>
  </div>

  <div class="grid-stack-item" {{ widget_attrs('new-bars', 2, 0, 5, 6) }}>
    <div class="grid-stack-item-content">
      {% if not tv_mode %}{{ edit_controls('new-bars', 'New Work Centers', 'bars') }}{% endif %}
      <h3>{{ widget_title('new-bars', 'New Work Centers') }}</h3>
      <div class="widget-body">{{ department_bar_chart('new-bars', new_bars) }}</div>
    </div>
  </div>

  <div class="grid-stack-item" {{ widget_attrs('downtime-report', 7, 0, 5, 6) }}>
    <div class="grid-stack-item-content">
      {% if not tv_mode %}{{ edit_controls('downtime-report', 'Downtime Report', 'downtime') }}{% endif %}
      <h3>{{ widget_title('downtime-report', 'Downtime Report') }}</h3>
      {{ department_downtime_report(downtime_rows, elapsed_minutes, uptime_pct) }}
    </div>
  </div>

  <div class="grid-stack-item" {{ widget_attrs('new-progress', 0, 6, 12, 5) }}>
    <div class="grid-stack-item-content">
      {% if not tv_mode %}{{ edit_controls('new-progress', 'All New — 15-minute progress', 'progress') }}{% endif %}
      <span class="people-count">{{ new_people }}{% if is_range %} pd{% endif %}</span>
      <h3>{{ widget_title('new-progress', 'All New — 15-minute progress') }}</h3>
      <div class="widget-body">{{ department_progress_chart(
          new_progress, new_group_target,
          (new_group_target / 4)|round|int, 'new-progress'
      ) }}</div>
    </div>
  </div>

  <div class="grid-stack-item" {{ widget_attrs('new-cumulative', 0, 11, 12, 5) }}>
    <div class="grid-stack-item-content">
      {% if not tv_mode %}{{ edit_controls('new-cumulative', 'All New — Daily Progress', 'progress') }}{% endif %}
      <span class="people-count">{{ new_people }}{% if is_range %} pd{% endif %}</span>
      <h3>{{ widget_title('new-cumulative', 'All New — Daily Progress') }}</h3>
      <div class="widget-body">{{ cumulative_progress_chart(new_progress) }}</div>
    </div>
  </div>
</div>
```

Load GridStack and `dashboard-grid.js` at the bottom. Retain the existing
assign-popover, guarded TV refresh, GOAT alert banner, footer, top nav, and
dashboard subnav behavior.

- [ ] **Step 4: Update shared-JS documentation and integration assertions**

Change only the supported-page comment in `dashboard-grid.js`:

```javascript
 *                       and /api/widget/{page}/{id}
 *                       ("recycling" | "new" | "operator")
```

In `test_dashboards_polish.py`, assert `/new` contains `grid-stack`,
`data-layout-page="new"`, `data-widget="new-bars"`, and the range toolbar.

- [ ] **Step 5: Run template/integration tests**

Run:

```bash
.venv/bin/pytest tests/test_new_dashboard_template.py tests/test_dashboards_polish.py tests/test_recycling_toolbar_static.py -v
```

Expected: all pass or database-gated skips.

- [ ] **Step 6: Commit the editable template**

```bash
git add src/zira_dashboard/templates/new_dept.html src/zira_dashboard/static/dashboard-grid.js tests/test_new_dashboard_template.py tests/test_dashboards_polish.py
git commit -m "feat: match new dashboard to recycling layout"
```

---

### Task 5: Lock down TV mode, future-meter expansion, and empty states

**Files:**
- Modify: `tests/test_tv_dashboards_vs.py`
- Modify: `tests/test_new_dashboard_data.py`
- Modify: `src/zira_dashboard/templates/new_dept.html`

**Interfaces:**
- Consumes: completed New route/template.
- Produces: verified read-only TV mode and deterministic automatic inclusion of future New Zira meters.

- [ ] **Step 1: Add failing behavior tests**

Add:

```python
def test_tv_new_uses_static_new_grid(monkeypatch):
    _stub_data(monkeypatch)
    with patch("zira_dashboard.routes.departments._new_day_data", return_value=_empty_new_day()):
        response = TestClient(app).get("/tv/new")
    assert response.status_code == 200
    assert 'data-layout-page="new"' in response.text
    assert 'data-tv-mode="1"' in response.text
    assert 'class="rc-toolbar"' not in response.text
    assert 'id="reset-layout"' not in response.text
    assert "tv-refresh.js" in response.text


def test_new_station_discovery_expands_from_location_meter(monkeypatch):
    locations = (
        SimpleNamespace(name="Junior #2", skill="Junior", department="New", meter_id="42345"),
        SimpleNamespace(name="Hand Build #1", skill="Hand Build", department="New", meter_id="hb-1"),
        SimpleNamespace(name="Woodpecker #1", skill="Woodpecker", department="New", meter_id="wp-1"),
        SimpleNamespace(name="Hand Build #2", skill="Hand Build", department="New", meter_id=None),
    )
    monkeypatch.setattr(departments.staffing, "LOCATIONS", locations)
    monkeypatch.setattr(departments.work_centers_store, "department", lambda loc: loc.department)

    assert [s.name for s in departments._new_stations()] == [
        "Junior #2", "Hand Build #1", "Woodpecker #1",
    ]
```

- [ ] **Step 2: Run and observe any missing TV/empty-state behavior**

Run:

```bash
.venv/bin/pytest tests/test_tv_dashboards_vs.py tests/test_new_dashboard_data.py -v
```

Expected: new assertions fail until TV chrome and empty-state guards are final.

- [ ] **Step 3: Finish empty-state and TV guards**

In `new_dept.html`:

```jinja2
{% if not tv_mode %}
  <form class="rc-toolbar" method="get" action="/new">
    {% set windows = [
      ('today', 'Today'),
      ('yesterday', 'Yesterday'),
      ('week', 'This Week'),
      ('last_week', 'Last Week'),
      ('month', 'This Month'),
      ('last_month', 'Last Month'),
    ] %}
    {% for wval, wlabel in windows %}
      <a href="/new?window={{ wval }}"
         class="rc-chip{% if window == wval and not custom_range_active %} rc-chip-on{% endif %}">{{ wlabel }}</a>
    {% endfor %}
    <details class="rc-custom-popover">
      <summary class="rc-chip{% if custom_range_active %} rc-chip-on{% endif %}">{% if custom_range_active %}Custom: {{ start }} → {{ end }}{% else %}Custom{% endif %}</summary>
      <div class="rc-custom-panel">
        <label>Start <input type="date" name="start" value="{{ start if custom_range_active else '' }}"></label>
        <label>End <input type="date" name="end" value="{{ end if custom_range_active else '' }}"></label>
        <button type="submit" class="rc-chip rc-custom-apply">Apply</button>
      </div>
    </details>
    <div class="edit-bar">
      <span class="save-indicator" id="save-indicator">Drag / resize — layout auto-saves</span>
      <button type="button" id="reset-layout">Reset Layout</button>
    </div>
  </form>
{% endif %}

{% if new_bars %}
  {{ department_bar_chart('new-bars', new_bars) }}
{% else %}
  <div class="empty-state">
    No New work centers have Zira production data for this range.
  </div>
{% endif %}
```

Do not render zero-goal target markers; rely on `build_bars` returning
`target_pct=None` when expected output is zero.

- [ ] **Step 4: Run TV/data tests**

Run:

```bash
.venv/bin/pytest tests/test_tv_dashboards_vs.py tests/test_new_dashboard_data.py tests/test_new_dashboard_template.py -v
```

Expected: all pass or database-gated skips.

- [ ] **Step 5: Commit TV and expansion guards**

```bash
git add src/zira_dashboard/templates/new_dept.html tests/test_tv_dashboards_vs.py tests/test_new_dashboard_data.py
git commit -m "test: cover new dashboard tv and meter expansion"
```

---

### Task 6: Add deterministic visual preview and complete verification

**Files:**
- Create: `scripts/preview_new_dashboard.py`
- Verify: `src/zira_dashboard/routes/departments.py`
- Verify: `src/zira_dashboard/templates/_department_dashboard_widgets.html`
- Verify: `src/zira_dashboard/templates/recycling.html`
- Verify: `src/zira_dashboard/templates/new_dept.html`
- Verify: `src/zira_dashboard/static/dashboard-grid.js`
- Verify: `tests/test_new_dashboard_data.py`
- Verify: `tests/test_new_dashboard_template.py`
- Verify: `tests/test_tv_dashboards_vs.py`

**Interfaces:**
- Consumes: finished New dashboard.
- Produces: reproducible desktop/TV HTML fixtures for visual QA and final test evidence.

- [ ] **Step 1: Create a deterministic New-dashboard preview script**

Use `_new_day_data` as the only patched seam and emit editor/TV variants:

```python
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AUTH_DISABLED", "1")
os.environ.setdefault("SESSION_SECRET", "preview-secret-32-bytes-of-data!!!!")
os.environ.setdefault("ZIRA_API_KEY", "preview-dummy")

from fastapi.testclient import TestClient
from zira_dashboard.app import app
from zira_dashboard.routes import departments
from zira_dashboard.stations import Station


OUT = Path(__file__).parent / "_preview_new_out"


def _busy_day(d, now, is_today_d, align_to_standard=False):
    station = Station("42345", "Junior #2", "New", "New")
    buckets = [
        {"label": "7:00", "actual": 47, "target": 48, "in_progress": False},
        {"label": "7:15", "actual": 51, "target": 48, "in_progress": True},
    ]
    return {
        "total_units": 98,
        "total_downtime": 0,
        "elapsed": 30,
        "available": 30,
        "uptime_minutes": 30,
        "total_man_hours": 0.5,
        "total_recycling_people": 1,
        "per_wc_units": {"Junior #2": 98},
        "per_wc_downtime": {"Junior #2": 0},
        "per_wc_expected": {"Junior #2": 96.0},
        "per_wc_who": {"Junior #2": "Lauro"},
        "per_wc_state": {"Junior #2": "working"},
        "per_wc_category": {"Junior #2": "New"},
        "per_wc_station_obj": {"Junior #2": station},
        "active_wc_names": {"Junior #2"},
        "schedule_assignments": {"Junior #2": ["Lauro"]},
        "group_buckets": {"New": buckets},
        "shift_start_label": "07:00",
    }
```

Render `/new`, `/tv/new?theme=dark`, and `/tv/new?theme=light`; create the
same static-assets symlink strategy as `preview_recycling.py`.

- [ ] **Step 2: Generate preview HTML**

Run:

```bash
.venv/bin/python scripts/preview_new_dashboard.py
```

Expected: three HTML files written under `scripts/_preview_new_out/` with no
network calls.

- [ ] **Step 3: Inspect the desktop preview at the reference viewport**

Serve `scripts/_preview_new_out` locally and verify at approximately
`1986 × 1248`:

- KPI cards are stacked at left;
- New Work Centers occupies the center of the top row;
- Downtime Report occupies the right;
- 15-minute and Daily Progress are full width below;
- Daily Progress is bar-based;
- drag handles/edit menus and full range toolbar are visible;
- no Unplanned Stops widget is present.

- [ ] **Step 4: Run focused tests and lint**

Run:

```bash
.venv/bin/pytest tests/test_new_dashboard_data.py tests/test_new_dashboard_template.py tests/test_tv_dashboards_vs.py tests/test_recycling_toolbar_static.py tests/test_dashboards_polish.py -v
.venv/bin/ruff check src/zira_dashboard/routes/departments.py tests/test_new_dashboard_data.py tests/test_new_dashboard_template.py tests/test_tv_dashboards_vs.py scripts/preview_new_dashboard.py
```

Expected: all focused tests pass or documented DB-gated skips; Ruff exits 0.

- [ ] **Step 5: Run the complete regression suite**

Run:

```bash
.venv/bin/pytest -q
```

Expected: the complete suite passes with only the repository's documented
environment-gated skips.

- [ ] **Step 6: Commit preview and any final verification fixes**

```bash
git add scripts/preview_new_dashboard.py
git commit -m "test: add new dashboard visual preview"
```

---

## Final Verification Checklist

- [ ] `/new` defaults to Today and renders Junior #2 from Zira meter `42345`.
- [ ] The full time-range toolbar is above the canvas.
- [ ] The default composition matches the supplied Recycling screenshot.
- [ ] All six widgets drag, resize, edit, reset, and auto-save under page key `new`.
- [ ] Daily Progress is cumulative bars with a target line.
- [ ] Downtime uses the existing Recycling calculation and presentation.
- [ ] No Unplanned Stops widget or raw Zira schema data appears.
- [ ] `/tv/new` is static, chrome-free, themed, and auto-refreshing.
- [ ] Legacy `/new?day=YYYY-MM-DD`, `/new-vs`, and `/tv/new-vs` behavior remains valid.
- [ ] Recycling's rendered behavior and test suite pass without changed assertions.
- [ ] Ruff and the complete pytest suite pass.
