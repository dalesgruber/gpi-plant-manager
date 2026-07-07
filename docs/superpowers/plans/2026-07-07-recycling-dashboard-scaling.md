# Recycling Dashboard Scaling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the recycling dashboard render with no clipped/overflowing content and no dead whitespace across 16:9 HDTVs (720p→4K) and laptop/desktop browsers, in both the TV view (`/tv/recycling`) and the editor view (`/recycling`).

**Architecture:** Keep the existing Gridstack + container-query + `fitGridToViewport` model. The fix is CSS-only for the layout (make widget internals fully proportional by removing fixed pixel `min-height`/`max-height` floors that fight the fit-to-viewport shrink; delete a harmful/dead legacy media query) plus one small JS re-fit on font load. A committed preview script renders the real template with representative data so we can verify at every target resolution, and a static-CSS regression test locks the fixes.

**Tech Stack:** FastAPI + Jinja2 templates, Gridstack.js, CSS container queries, pytest (TestClient against a local pgserver Postgres), Claude Preview headless browser for the resolution matrix.

**Spec:** `docs/superpowers/specs/2026-07-07-recycling-dashboard-scaling-design.md`

**Environment note (local render/verify):** the render path needs a Postgres `DATABASE_URL`. A local pgserver datadir is already running at the socket in `pg_uri.txt`. Every local Python command in this plan is prefixed:

```
DATABASE_URL='postgresql://postgres:@/postgres?host=/Users/dalegruber/Projects/gpi-plant-manager/pgdata_review' ZIRA_API_KEY=test .venv/bin/python ...
```

If the socket is gone, re-create it per the pgserver recipe in the project memory (`local-dev-and-deploy`), then `db.init_pool(); db.bootstrap_schema()` once.

---

## Files

- **Modify:** `src/zira_dashboard/static/recycling.css` — proportional internals + media-query cleanup.
- **Modify:** `src/zira_dashboard/static/dashboard-grid.js` — re-fit TV grid on `document.fonts.ready`.
- **Create:** `scripts/preview_recycling.py` — render the real recycling template (busy + empty fixtures, editor + TV dark/light) to static HTML in a serve dir for browser verification.
- **Create:** `tests/test_recycling_scaling_static.py` — static regression guard over `recycling.css`.
- **Modify:** `.gitignore` — ignore the preview output dir.
- **Modify:** `.claude/launch.json` — add a static-server config for the preview dir (create the file if absent).

No Python route/data/template changes.

---

## Task 1: Preview harness + capture the BEFORE baseline

Builds the reusable render script and captures current (pre-fix) screenshots so we have evidence of the clipping and an apples-to-apples before/after.

**Files:**
- Create: `scripts/preview_recycling.py`
- Modify: `.gitignore`
- Modify/Create: `.claude/launch.json`

- [ ] **Step 1: Write the preview render script**

`_recycling_day_data(d, now, is_today_d, align_to_standard=False)` is the single seam that produces every widget's data; the route aggregates its per-day dict into the page. Monkeypatch it to return a representative dict → fully-populated page without touching Odoo/Zira. Return-dict keys (from its docstring + the route's aggregation loop): `total_units, total_downtime, elapsed, available, uptime_minutes, total_man_hours, total_recycling_people, per_wc_units, per_wc_downtime, per_wc_expected, per_wc_who, per_wc_state, dism_buckets, repair_buckets, shift_start_label, schedule_assignments, active_wc_names, per_wc_category, per_wc_station_obj`. Progress/cumulative buckets are dicts shaped `{label, actual, target, in_progress}`.

Create `scripts/preview_recycling.py`:

```python
"""Render the recycling dashboard to static HTML for cross-resolution QA.

Renders the REAL template through the app (TestClient) with a representative
"busy" data fixture (6 dismantlers + repairs + downtime + a full day of
15-min progress buckets) and an "empty/weekend" fixture, for the editor view
and both TV themes. Output goes to scripts/_preview_out/ with a `static`
symlink to the real static assets, ready to serve with `python -m http.server`.

Run:
    DATABASE_URL='postgresql://postgres:@/postgres?host=<...>/pgdata_review' \
    ZIRA_API_KEY=test .venv/bin/python scripts/preview_recycling.py
Then serve + browse (see .claude/launch.json 'recycling-preview').
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("AUTH_DISABLED", "1")
os.environ.setdefault("SESSION_SECRET", "preview-secret-32-bytes-of-data!!!!")
os.environ.setdefault("ZIRA_API_KEY", "preview-dummy")

from fastapi.testclient import TestClient  # noqa: E402

from zira_dashboard.app import app  # noqa: E402
from zira_dashboard.routes import departments  # noqa: E402
from zira_dashboard.stations import Station  # noqa: E402

OUT = Path(__file__).parent / "_preview_out"


def _buckets(hi_lo):
    """15-min progress buckets 07:00-15:00 (32 buckets). hi_lo scales actuals."""
    out = []
    for i in range(32):
        hh = 7 + i // 4
        mm = (i % 4) * 15
        actual = 0 if hi_lo == 0 else int(9 + (i % 5) + hi_lo)
        out.append({
            "label": f"{hh}:{mm:02d}",
            "actual": actual,
            "target": 12,
            "in_progress": i == 31,
        })
    return out


def _busy_day(d, now, is_today_d, align_to_standard=False):
    dnames = [f"Dismantler {i}" for i in range(1, 7)]
    rnames = [f"Repair-{i}" for i in range(1, 5)]
    per_units = {**{n: 40 + i * 7 for i, n in enumerate(dnames)},
                 **{n: 55 + i * 5 for i, n in enumerate(rnames)}}
    per_dt = {**{n: (i * 6) % 25 for i, n in enumerate(dnames)},
              **{n: (i * 4) % 20 for i, n in enumerate(rnames)}}
    per_exp = {n: 60.0 for n in per_units}
    per_cat = {**{n: "Dismantler" for n in dnames}, **{n: "Repair" for n in rnames}}
    per_obj = {n: Station(meter_id=f"m{n}", name=n, category=per_cat[n], cell="Recycling")
               for n in per_units}
    who = {**{n: f"Operator {chr(65+i)}" for i, n in enumerate(dnames)},
           **{n: f"Operator {chr(75+i)}" for i, n in enumerate(rnames)}}
    return {
        "total_units": sum(per_units.values()),
        "total_downtime": sum(per_dt.values()),
        "elapsed": 360, "available": 360 * len(per_units),
        "uptime_minutes": 360 * len(per_units) - sum(per_dt.values()),
        "total_man_hours": 60.0, "total_recycling_people": 10,
        "per_wc_units": per_units, "per_wc_downtime": per_dt,
        "per_wc_expected": per_exp, "per_wc_who": who,
        "per_wc_state": {n: "working" for n in per_units},
        "dism_buckets": _buckets(3), "repair_buckets": _buckets(1),
        "shift_start_label": "7:00 AM",
        "schedule_assignments": {n: [who[n]] for n in per_units},
        "active_wc_names": set(per_units.keys()),
        "per_wc_category": per_cat, "per_wc_station_obj": per_obj,
    }


def _empty_day(d, now, is_today_d, align_to_standard=False):
    return {
        "total_units": 0, "total_downtime": 0, "elapsed": 0, "available": 0,
        "uptime_minutes": 0, "total_man_hours": 0.0, "total_recycling_people": 0,
        "per_wc_units": {}, "per_wc_downtime": {}, "per_wc_expected": {},
        "per_wc_who": {}, "per_wc_state": {},
        "dism_buckets": [], "repair_buckets": [],
        "shift_start_label": "7:00 AM", "schedule_assignments": {},
        "active_wc_names": set(), "per_wc_category": {}, "per_wc_station_obj": {},
    }


def _render(client, url):
    r = client.get(url)
    assert r.status_code == 200, (url, r.status_code, r.text[:500])
    return r.text


def main():
    OUT.mkdir(exist_ok=True)
    # `static` symlink so root-absolute /static/... refs resolve when served.
    link = OUT / "static"
    real_static = Path(__file__).resolve().parent.parent / "src/zira_dashboard/static"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(real_static)

    client = TestClient(app)
    variants = [
        ("editor_busy.html", _busy_day, "/recycling"),
        ("editor_empty.html", _empty_day, "/recycling"),
        ("tv_dark_busy.html", _busy_day, "/tv/recycling?theme=dark"),
        ("tv_light_busy.html", _busy_day, "/tv/recycling?theme=light"),
        ("tv_dark_empty.html", _empty_day, "/tv/recycling?theme=dark"),
    ]
    for fname, fixture, url in variants:
        with patch.object(departments, "_recycling_day_data", fixture):
            # bypass the per-variant response cache between renders
            from zira_dashboard import _http_cache
            _http_cache.invalidate_all_cache()
            html = _render(client, url)
        (OUT / fname).write_text(html)
        print("wrote", OUT / fname, len(html), "bytes")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Ignore the preview output dir**

Append to `.gitignore`:

```
# Recycling scaling QA render output (scripts/preview_recycling.py)
scripts/_preview_out/
```

- [ ] **Step 3: Run the render script**

Run:
```bash
DATABASE_URL='postgresql://postgres:@/postgres?host=/Users/dalegruber/Projects/gpi-plant-manager/pgdata_review' ZIRA_API_KEY=test .venv/bin/python scripts/preview_recycling.py
```
Expected: five `wrote scripts/_preview_out/<name>.html <N> bytes` lines, all N > 10000. If a render 500s, the assertion prints the URL + first 500 chars of the error — fix the fixture dict before proceeding.

- [ ] **Step 4: Add the preview server to `.claude/launch.json`**

If `.claude/launch.json` does not exist, create it. If it exists, add this object to its `configurations` array:

```json
{
  "name": "recycling-preview",
  "runtimeExecutable": "python3",
  "runtimeArgs": ["-m", "http.server", "8137", "--directory", "scripts/_preview_out"],
  "port": 8137
}
```

- [ ] **Step 5: Capture BEFORE screenshots at the resolution matrix**

Start the server with the Claude Preview tool (`preview_start` name `recycling-preview`). For each variant file (`tv_dark_busy.html`, `tv_light_busy.html`, `tv_dark_empty.html`, `editor_busy.html`, `editor_empty.html`) navigate to `http://localhost:8137/<file>` and, at each size, screenshot + run the overflow probe:

Sizes (use `preview_resize` custom width/height; for TV files it's the whole viewport, for editor files the same): 1280×800, 1366×768, 1440×900, 1920×1080, 3840×2160.

Overflow probe (`preview_eval`) — lists any widget whose content exceeds its box:
```js
[...document.querySelectorAll('.grid-stack-item-content')]
  .filter(e => e.scrollHeight > e.clientHeight + 1)
  .map(e => (e.querySelector('h3,.label')||{}).textContent || '(?)')
```
Record which widgets overflow at which sizes (expect the progress/cumulative charts to overflow at the smaller heights — that's the bug). Save a note of the results in the task's commit message. Stop the server (`preview_stop`).

- [ ] **Step 6: Commit**

```bash
git add scripts/preview_recycling.py .gitignore .claude/launch.json
git commit -m "test(recycling): add cross-resolution preview harness

Renders the real recycling template with a busy + empty fixture (editor +
TV dark/light) to static HTML for browser QA at 720p-4K + laptop sizes.
Captures the pre-fix overflow baseline."
```

---

## Task 2: Static regression guard (failing test first)

**Files:**
- Create: `tests/test_recycling_scaling_static.py`

- [ ] **Step 1: Write the failing test**

```python
"""Static guards that the recycling dashboard keeps its widget internals
fully proportional — no fixed pixel floors/caps that fight TV
fit-to-viewport, and no fixed-height chart override in a narrow-width
media query. See docs/superpowers/specs/2026-07-07-recycling-dashboard-scaling-design.md.
"""
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent
       / "src/zira_dashboard/static/recycling.css").read_text()


def test_progress_plot_has_no_pixel_min_height_floor():
    # .progress .plot / .cum-progress .plot must shrink with the widget.
    assert "min-height: 60px" not in CSS
    assert "min-height: 80px" not in CSS


def test_bar_track_has_no_fixed_pixel_min_or_max_height():
    # scoped bar-track must be proportional (no 14px floor, no 200px cap).
    assert "min-height: 14px" not in CSS
    assert "max-height: 200px" not in CSS


def test_no_fixed_progress_bars_height_in_media_query():
    # The harmful `@media (max-width:600px){ .progress .bars{height:110px} }`
    # pinned the flex chart to a fixed height on narrow windows.
    assert "height: 110px" not in CSS
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_recycling_scaling_static.py -v
```
Expected: all three FAIL (the floors/cap/rule are still present in the CSS).

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_recycling_scaling_static.py
git commit -m "test(recycling): guard proportional widget internals (red)"
```

---

## Task 3: Make widget internals proportional (CSS)

**Files:**
- Modify: `src/zira_dashboard/static/recycling.css`

- [ ] **Step 1: Remove the bar-track pixel floor + cap**

In the `.grid-stack-item-content .bar-track, .grid-stack-item-content .stacked-track` rule (around line 152), replace:

```css
  .grid-stack-item-content .bar-track,
  .grid-stack-item-content .stacked-track {
    height: 100%;
    min-height: 14px;
    max-height: 200px;
  }
```

with:

```css
  .grid-stack-item-content .bar-track,
  .grid-stack-item-content .stacked-track {
    height: 100%;
    min-height: 0;   /* proportional — never force overflow of a short widget */
  }
```

- [ ] **Step 2: Remove the `.progress .plot` pixel floor**

In the `.progress .plot` rule (around line 586), replace `min-height: 60px;` with `min-height: 0;`:

```css
  .progress .plot {
    position: relative;
    flex: 1 1 auto;
    min-height: 0;   /* was 60px — floor clipped the chart on short TVs */
    padding: 0.25rem 0;
    border-bottom: 1px solid var(--border);
  }
```

- [ ] **Step 3: Remove the `.cum-progress .plot` pixel floor**

In the `.cum-progress .plot` rule (around line 722), replace `min-height: 80px;` with `min-height: 0;`:

```css
  .cum-progress .plot {
    position: relative;
    flex: 1 1 auto;
    min-height: 0;   /* was 80px — floor clipped the chart on short TVs */
    margin-top: 14px;
    border-bottom: 1px solid var(--border);
  }
```

- [ ] **Step 4: Delete the dead + harmful media-query rules**

In the `@media (max-width: 1400px)` block (around line 673), delete the three dead selectors (`.kpi`, `.kpi .val`, `.panel` are not rendered on this page); keep the live layout tightening. Result:

```css
  @media (max-width: 1400px) {
    header { padding: 0.7rem 0.9rem; }
    .sub-nav { padding-left: 0.9rem; padding-right: 0.9rem; }
    main { padding: 0.6rem 0.85rem 0.85rem; }
  }
```

In the `@media (max-width: 600px)` block (around line 682), delete the dead `.kpi .val` rule and the harmful `.progress .bars` rule; keep the `.bar-row` tweak. Result:

```css
  @media (max-width: 600px) {
    .bar-row { grid-template-columns: 5.5rem 1fr 3.5rem; gap: 0.5rem; }
  }
```

- [ ] **Step 5: Run the regression guard — verify it passes**

Run:
```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_recycling_scaling_static.py -v
```
Expected: all three PASS.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/static/recycling.css
git commit -m "fix(recycling): proportional widget internals for any screen size

Remove fixed pixel min-height floors (.progress/.cum-progress .plot,
.bar-track) and the .bar-track 200px cap that fought the TV
fit-to-viewport shrink and clipped charts below 1080p. Delete the dead
.kpi/.panel media rules and the harmful narrow-width
.progress .bars{height:110px} override."
```

---

## Task 4: Re-fit the TV grid after fonts settle (JS)

**Files:**
- Modify: `src/zira_dashboard/static/dashboard-grid.js`

- [ ] **Step 1: Add a `document.fonts.ready` re-fit**

In `dashboard-grid.js`, inside the `if (tvMode) { ... }` block, the current tail is:

```js
    fitGridToViewport();
    requestAnimationFrame(fitGridToViewport);
    window.addEventListener('resize', fitGridToViewport);
    return; // TVs are read-only — none of the editor wiring below applies.
```

Replace with:

```js
    fitGridToViewport();
    requestAnimationFrame(fitGridToViewport);
    // Fonts change the measured TV-header height; re-fit once they settle so
    // cellHeight isn't computed against a stale (pre-font) header measurement.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(fitGridToViewport);
    }
    window.addEventListener('resize', fitGridToViewport);
    return; // TVs are read-only — none of the editor wiring below applies.
```

- [ ] **Step 2: Sanity-check the JS parses**

Run:
```bash
node --check src/zira_dashboard/static/dashboard-grid.js
```
Expected: no output, exit 0. (If `node` is unavailable, skip — Step 3's browser load will surface a syntax error.)

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/static/dashboard-grid.js
git commit -m "fix(recycling): re-fit TV grid on document.fonts.ready

Guards against cellHeight being computed against a pre-font header height."
```

---

## Task 5: Verify the AFTER state across the resolution matrix

**Files:** none (verification only)

- [ ] **Step 1: Re-render with the fixes applied**

Run:
```bash
DATABASE_URL='postgresql://postgres:@/postgres?host=/Users/dalegruber/Projects/gpi-plant-manager/pgdata_review' ZIRA_API_KEY=test .venv/bin/python scripts/preview_recycling.py
```
Expected: five files rewritten.

- [ ] **Step 2: Browse the matrix and assert zero overflow**

Start `recycling-preview` (`preview_start`). For each of the five variant files, at each size (1280×800, 1366×768, 1440×900, 1920×1080, 3840×2160), run the overflow probe from Task 1 Step 5:

```js
[...document.querySelectorAll('.grid-stack-item-content')]
  .filter(e => e.scrollHeight > e.clientHeight + 1)
  .map(e => (e.querySelector('h3,.label')||{}).textContent || '(?)')
```
Expected: `[]` (empty) for every variant × size. Also screenshot `tv_dark_busy.html` at 1920×1080 and 3840×2160 and `editor_busy.html` at 1440×900 for the before/after record. For the TV files, additionally confirm no vertical scroll:
```js
document.documentElement.scrollHeight <= window.innerHeight + 1
```
Expected: `true`. Stop the server (`preview_stop`).

- [ ] **Step 3: If any widget still overflows**

Use superpowers:systematic-debugging. Likely remaining culprits: another fixed pixel dimension inside the overflowing widget (grep `recycling.css` and `_cumulative_progress_chart.html` for `px`), or a `line-height`/`gap` that doesn't scale. Fix proportionally, extend the Task 2 guard if a new floor is found, re-run Step 2. Do not proceed until every cell reports `[]`.

---

## Task 6: Full suite + finish

**Files:** none

- [ ] **Step 1: Run the affected tests + a broad render sanity check**

Run:
```bash
DATABASE_URL='postgresql://postgres:@/postgres?host=/Users/dalegruber/Projects/gpi-plant-manager/pgdata_review' ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_recycling_scaling_static.py tests/test_recycling_toolbar_static.py tests/test_dashboards_polish.py tests/test_tv_dashboards_vs.py -q
```
Expected: pass (the DB-debt-skipped ones stay skipped). Then the full suite:
```bash
DATABASE_URL='postgresql://postgres:@/postgres?host=/Users/dalegruber/Projects/gpi-plant-manager/pgdata_review' ZIRA_API_KEY=test .venv/bin/python -m pytest -q
```
Expected: green (same skip set as a normal run).

- [ ] **Step 2: Lint**

Run:
```bash
.venv/bin/python -m ruff check src tests scripts
```
Expected: no errors (CI selects pyflakes `F`; an unused import fails). Fix any before finishing.

- [ ] **Step 3: Finish the branch**

Use superpowers:finishing-a-development-branch to choose merge/PR. (This repo auto-deploys `main` on push and a concurrent process may rebase branches — prefer a squash PR via `gh` per project memory.)

---

## Self-review

**Spec coverage:**
- Proportional internals (remove pixel floors) → Task 3 Steps 1–3. ✅
- Media-query cleanup (harmful + dead, keep laptop tightening) → Task 3 Step 4. ✅
- `fitGridToViewport` `fonts.ready` re-fit → Task 4. ✅
- Verification matrix (5 sizes × editor/TV dark+light, busy + empty fixtures, no-overflow + no-scroll asserts) → Tasks 1 & 5. ✅
- Static regression guard → Task 2 + Step 5 of Task 3. ✅
- TV overscan explicitly out of scope → spec Non-goals; not in plan. ✅

**Placeholder scan:** No TBD/TODO; every code + command step shows full content. ✅

**Type/name consistency:** The fixture returns exactly the `_recycling_day_data` keys listed in that function's docstring; bucket dicts use `{label, actual, target, in_progress}` matching the `progress_chart`/`cumulative_progress_chart` macros; `Station(meter_id, name, category, cell)` matches its usage in `tests/test_dashboards_polish.py`. The overflow probe and CSS selectors match the classes in `recycling.html`/`recycling.css`. ✅
