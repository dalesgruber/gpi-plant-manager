# Leaderboards Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Convert per-category aggregated leaderboards into per-WC top-5 single-day records with drag-reorderable sections, an "Inactive" pile (auto + manual), name-`(N)` count, and a custom date range form.

**Architecture:** New `leaderboard_wc_settings` table (server-side per-WC sort_order + is_inactive). New `production_history.daily_records()` returns un-aggregated per-day per-person per-WC records. Route rewritten to compute top-5 per WC, sort by metric (units OR pct), tiebreak oldest-first. Three new POST endpoints for ordering + active/inactive toggles. Template gets per-section drag handles, ✕ button, collapsible Inactive section, and a custom from/to date form alongside the existing window radio.

**Tech Stack:** psycopg2 + raw SQL, FastAPI sync, Jinja + native HTML5 drag-and-drop.

**Dependencies:** Postgres infra is in place. The leaderboards route currently exists and uses `production_history.attribution_range` + `rank_by_category`.

---

## File Structure

- New: `src/zira_dashboard/leaderboard_settings_store.py`
- Modified: `src/zira_dashboard/db.py` (append `leaderboard_wc_settings` DDL)
- Modified: `src/zira_dashboard/production_history.py` (add `daily_records`)
- Modified: `src/zira_dashboard/routes/leaderboards.py` (rewrite ranking + 3 new endpoints)
- Modified: `src/zira_dashboard/templates/leaderboards.html` (new sections, drag, inactive, custom range)
- New: `tests/test_leaderboard_settings_store.py`

---

### Task 1: Schema + leaderboard_settings_store

**Files:**
- Modify: `src/zira_dashboard/db.py`
- Create: `src/zira_dashboard/leaderboard_settings_store.py`
- Create: `tests/test_leaderboard_settings_store.py`

- [ ] **Step 1: Append DDL to `_SCHEMA_DDL` in `db.py`** (before the closing `"""`):

```sql

-- Per-WC display settings for the leaderboards page ------------------

CREATE TABLE IF NOT EXISTS leaderboard_wc_settings (
  wc_name      TEXT PRIMARY KEY,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  is_inactive  BOOLEAN NOT NULL DEFAULT FALSE,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Implement `leaderboard_settings_store.py`**

Public API:

```python
def snapshot() -> dict[str, dict]: ...        # {wc_name: {sort_order, is_inactive}}
def set_order(wc_names: list[str]) -> None: ...
def set_inactive(wc_name: str, value: bool) -> None: ...
```

Implementation:

```python
"""Per-WC layout settings for the leaderboards page (sort order + manual
inactive flag). Server-side, shared across users/devices.
"""

from __future__ import annotations


def snapshot() -> dict[str, dict]:
    from . import db
    rows = db.query(
        "SELECT wc_name, sort_order, is_inactive FROM leaderboard_wc_settings"
    )
    return {r["wc_name"]: {"sort_order": r["sort_order"], "is_inactive": r["is_inactive"]} for r in rows}


def set_order(wc_names: list[str]) -> None:
    """Upsert sort_order for each name in the list, indexed left-to-right.
    WCs not in the list are untouched (their existing order survives)."""
    from . import db
    with db.cursor() as cur:
        for i, name in enumerate(wc_names):
            if not isinstance(name, str) or not name.strip():
                continue
            cur.execute(
                "INSERT INTO leaderboard_wc_settings (wc_name, sort_order) "
                "VALUES (%s, %s) "
                "ON CONFLICT (wc_name) DO UPDATE SET "
                "sort_order = EXCLUDED.sort_order, updated_at = now()",
                (name.strip(), i),
            )


def set_inactive(wc_name: str, value: bool) -> None:
    from . import db
    db.execute(
        "INSERT INTO leaderboard_wc_settings (wc_name, is_inactive) "
        "VALUES (%s, %s) "
        "ON CONFLICT (wc_name) DO UPDATE SET "
        "is_inactive = EXCLUDED.is_inactive, updated_at = now()",
        (wc_name.strip(), bool(value)),
    )
```

- [ ] **Step 3: Tests in `tests/test_leaderboard_settings_store.py`**

```python
import os
import pytest

from zira_dashboard import db, leaderboard_settings_store as store

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


@pytest.fixture(autouse=True)
def _clean():
    db.execute("DELETE FROM leaderboard_wc_settings WHERE wc_name LIKE 'TestWC%'")
    yield
    db.execute("DELETE FROM leaderboard_wc_settings WHERE wc_name LIKE 'TestWC%'")


def test_snapshot_empty():
    assert all(not k.startswith("TestWC") for k in store.snapshot())


def test_set_order_assigns_indices():
    store.set_order(["TestWC1", "TestWC2", "TestWC3"])
    snap = store.snapshot()
    assert snap["TestWC1"]["sort_order"] == 0
    assert snap["TestWC2"]["sort_order"] == 1
    assert snap["TestWC3"]["sort_order"] == 2


def test_set_inactive_toggles():
    store.set_inactive("TestWC1", True)
    snap = store.snapshot()
    assert snap["TestWC1"]["is_inactive"] is True
    store.set_inactive("TestWC1", False)
    assert store.snapshot()["TestWC1"]["is_inactive"] is False


def test_set_order_is_idempotent():
    store.set_order(["TestWC1", "TestWC2"])
    store.set_order(["TestWC1", "TestWC2"])
    snap = store.snapshot()
    assert snap["TestWC1"]["sort_order"] == 0
    assert snap["TestWC2"]["sort_order"] == 1


def test_set_order_preserves_inactive():
    store.set_inactive("TestWC1", True)
    store.set_order(["TestWC1"])
    snap = store.snapshot()
    assert snap["TestWC1"]["is_inactive"] is True  # not clobbered
    assert snap["TestWC1"]["sort_order"] == 0
```

- [ ] **Step 4: Run tests with live Postgres, verify PASS**

```bash
DATABASE_URL=$("C:/Users/dale.gruber/AppData/Roaming/npm/railway.cmd" variables --service Postgres --kv 2>/dev/null | grep "^DATABASE_PUBLIC_URL=" | cut -d= -f2-) && export DATABASE_URL && .venv/Scripts/python.exe -c "from zira_dashboard import db; db.init_pool(); db.bootstrap_schema()" && .venv/Scripts/python.exe -m pytest tests/test_leaderboard_settings_store.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/db.py src/zira_dashboard/leaderboard_settings_store.py tests/test_leaderboard_settings_store.py
git commit -m "feat(leaderboards): leaderboard_wc_settings table + store"
```

---

### Task 2: `production_history.daily_records()`

**Files:**
- Modify: `src/zira_dashboard/production_history.py`

- [ ] **Step 1: Add `daily_records()`**

Read the current `attribution_range()` to understand how it iterates days + builds per-day attributions. The new function follows the same pattern but DOESN'T aggregate.

```python
def daily_records(
    start_d: date, end_d: date, client
) -> list[dict]:
    """Return one record per (day, person, wc) where attributed units > 0.

    Used by the leaderboards top-5 single-day computation.
    Each record: {day: date, person: str, wc: str,
                  units: float, downtime: float, hours: float}
    """
    out: list[dict] = []
    d = start_d
    while d <= end_d:
        per_day = _attribute_one_day(d, client)
        for person, wcs in per_day.items():
            for wc, totals in wcs.items():
                if (totals.get("units") or 0) <= 0:
                    continue
                out.append({
                    "day": d,
                    "person": person,
                    "wc": wc,
                    "units": float(totals.get("units", 0)),
                    "downtime": float(totals.get("downtime", 0)),
                    "hours": float(totals.get("hours", 0)),
                })
        d += timedelta(days=1)
    return out
```

If the existing `attribution_range` uses an internal helper like `_attribute_one_day(day, client)`, reuse it. If it inlines the per-day logic, factor it out into a reusable helper.

- [ ] **Step 2: Verify with a smoke call**

```bash
DATABASE_URL=... ZIRA_BASE_URL=... ZIRA_API_KEY=... .venv/Scripts/python.exe -c "
from datetime import date
from zira_dashboard import production_history
from zira_dashboard.deps import client
records = production_history.daily_records(date(2026, 4, 22), date(2026, 4, 29), client)
print(f'{len(records)} records')
for r in records[:5]:
    print(r)
"
```

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/production_history.py
git commit -m "feat(leaderboards): production_history.daily_records — un-aggregated per-day records"
```

---

### Task 3: Route rewrite

**Files:**
- Modify: `src/zira_dashboard/routes/leaderboards.py`

- [ ] **Step 1: Rewrite `staffing_leaderboards` GET handler**

Full rewrite. New responsibilities:
- Parse `?window=`, `?metric=`, `?start=`, `?end=` query params.
  - If `start` and `end` both present → use them. Otherwise window-based.
- Fetch `daily_records` for the resolved date range.
- For each WC (from `staffing.LOCATIONS`), compute top-5 single-day rows:
  - Filter records where `wc == loc.name`.
  - Compute metric per record. For `pct`, expected_per_day = `settings_store.station_target_per_day(...)` for the loc; pct = units/expected. If expected=0, pct=0.
  - Sort by metric desc, tiebreak by ascending day (oldest first).
  - Take top 5.
  - For each top-5 row, compute `name_count` = `sum(1 for r in records_for_wc if r.person == row.person)`.
- Read `leaderboard_settings_store.snapshot()` for sort_order + is_inactive.
- Section model:
  ```python
  {
      "loc_name": str,
      "rows": [{rank, name, name_count, day, day_label, units, expected, pct}, ...],
      "is_inactive": bool,           # composite: manual OR auto-empty
      "is_manually_inactive": bool,
      "sort_order": int,
  }
  ```
- Active sections = is_inactive False, sorted by sort_order.
- Inactive sections = is_inactive True, sorted same way.
- Pass to template: `{active_sections, inactive_sections, window, metric, start, end, today, custom_range_active}`.

`day_label` formatting: use `day.strftime("%a %-m/%-d")` on Linux, `day.strftime("%a %#m/%#d")` on Windows. Use `f"{day.strftime('%a')} {day.month}/{day.day}"` for portability.

- [ ] **Step 2: Add 3 new POST endpoints**

```python
@router.post("/staffing/leaderboards/order")
async def leaderboards_set_order(request: Request):
    from .. import leaderboard_settings_store as lstore
    body = await request.json()
    order = body.get("order") or []
    if not isinstance(order, list):
        return JSONResponse({"ok": False, "error": "order must be a list"}, status_code=400)
    lstore.set_order([str(x) for x in order if isinstance(x, str)])
    return JSONResponse({"ok": True})


@router.post("/staffing/leaderboards/wc/{name}/inactive")
def leaderboards_set_inactive(name: str):
    from .. import leaderboard_settings_store as lstore
    lstore.set_inactive(name, True)
    return JSONResponse({"ok": True})


@router.post("/staffing/leaderboards/wc/{name}/active")
def leaderboards_set_active(name: str):
    from .. import leaderboard_settings_store as lstore
    lstore.set_inactive(name, False)
    return JSONResponse({"ok": True})
```

- [ ] **Step 3: Smoke-import**

```bash
.venv/Scripts/python.exe -c "from zira_dashboard.routes import leaderboards; print('OK')"
```

- [ ] **Step 4: Run full test suite**

```bash
DATABASE_URL=... .venv/Scripts/python.exe -m pytest tests/ --ignore=tests/test_dashboards_polish.py
```

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/leaderboards.py
git commit -m "feat(leaderboards): per-WC top-5 single-day rows + order/active endpoints"
```

---

### Task 4: Template rewrite

**Files:**
- Modify: `src/zira_dashboard/templates/leaderboards.html`

- [ ] **Step 1: Read the current template** to capture existing styling and toolbar conventions.

- [ ] **Step 2: Build new toolbar**

Date range chips (Today / Week / Month / Quarter / Year) + custom-range form (two date inputs + Apply button). The chips link via `?window=...`; the custom form GETs `?start=...&end=...`. The active selection is highlighted.

Metric radio (Units / % of target) — % default. Submit-via-link (changing the radio sets `?metric=...` and reloads).

- [ ] **Step 3: Build active section list**

For each `s in active_sections`:

```html
<div class="lb-section" data-wc="{{ s.loc_name }}" draggable="true">
  <div class="lb-section-header">
    <span class="lb-drag-handle" title="Drag to reorder">☰</span>
    <h3>{{ s.loc_name }}</h3>
    <button type="button" class="lb-hide-btn" title="Mark inactive">✕</button>
  </div>
  <table class="lb-table">
    <thead>
      <tr>
        <th>#</th><th>Operator</th><th>Date</th>
        <th>Units</th><th>Expected</th><th>%</th>
      </tr>
    </thead>
    <tbody>
      {% for r in s.rows %}
      <tr>
        <td>{{ r.rank }}</td>
        <td>{{ r.name }} <span class="lb-name-count">({{ r.name_count }})</span></td>
        <td>{{ r.day_label }}</td>
        <td>{{ r.units|round|int }}</td>
        <td>{{ r.expected|round|int }}</td>
        <td>{{ '%.0f' % (r.pct * 100) }}%</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

- [ ] **Step 4: Build inactive section** (collapsible)

```html
<details class="lb-inactive-wrap" id="lb-inactive">
  <summary>▸ Inactive ({{ inactive_sections|length }})</summary>
  {% for s in inactive_sections %}
    <div class="lb-section lb-section-inactive" data-wc="{{ s.loc_name }}" draggable="true">
      <div class="lb-section-header">
        <span class="lb-drag-handle" title="Drag to reorder">☰</span>
        <h3>{{ s.loc_name }}</h3>
        {% if s.is_manually_inactive %}
          <button type="button" class="lb-show-btn" title="Mark active">↶</button>
        {% else %}
          <span class="lb-auto-empty" title="No data in this range — auto-hidden">auto-empty</span>
        {% endif %}
      </div>
      {# Don't render the table for empty sections — just the header. #}
      {% if s.rows %}
        ... same table as above ...
      {% endif %}
    </div>
  {% endfor %}
</details>
```

- [ ] **Step 5: CSS**

Add to the `<style>` block:

```css
.lb-section {
  background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
  margin-bottom: 0.7rem; padding: 0.6rem 0.8rem;
}
.lb-section.dragging { opacity: 0.5; }
.lb-section-header {
  display: flex; align-items: center; gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.lb-section-header h3 {
  margin: 0; font-size: 0.95rem; flex: 1;
}
.lb-drag-handle {
  cursor: move; color: var(--muted); font-size: 1rem;
  user-select: none;
}
.lb-hide-btn, .lb-show-btn {
  background: transparent; border: none; color: var(--muted);
  cursor: pointer; font-size: 0.95rem; padding: 0.2rem 0.4rem;
  border-radius: 4px;
}
.lb-hide-btn:hover { color: var(--bad); background: var(--panel-2); }
.lb-show-btn:hover { color: var(--accent); background: var(--panel-2); }
.lb-auto-empty {
  font-size: 0.7rem; color: var(--muted); font-style: italic;
}
.lb-table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
.lb-table th, .lb-table td { padding: 0.25rem 0.4rem; text-align: left; }
.lb-table th { color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.5px; border-bottom: 1px solid var(--border); }
.lb-table td { border-bottom: 1px solid var(--panel-2); }
.lb-name-count { color: var(--muted); font-size: 0.78rem; }
.lb-inactive-wrap { margin-top: 1.2rem; }
.lb-inactive-wrap > summary {
  cursor: pointer; font-size: 0.85rem; color: var(--muted);
  padding: 0.4rem 0.6rem;
}
.lb-inactive-wrap[open] > summary { color: var(--fg); }
```

- [ ] **Step 6: JS — drag-and-drop + button handlers**

```js
(function initLeaderboards() {
  let dragged = null;

  document.querySelectorAll('.lb-section').forEach(sec => {
    sec.addEventListener('dragstart', (e) => {
      dragged = sec;
      sec.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    sec.addEventListener('dragend', () => {
      if (dragged) dragged.classList.remove('dragging');
      dragged = null;
      saveOrder();
    });
    sec.addEventListener('dragover', (e) => {
      e.preventDefault();
      if (!dragged || dragged === sec) return;
      const rect = sec.getBoundingClientRect();
      const after = (e.clientY - rect.top) > rect.height / 2;
      sec.parentNode.insertBefore(dragged, after ? sec.nextSibling : sec);
    });
  });

  function saveOrder() {
    const active = [...document.querySelectorAll('.lb-active-list .lb-section')]
      .map(s => s.dataset.wc);
    const inactive = [...document.querySelectorAll('.lb-inactive-wrap .lb-section')]
      .map(s => s.dataset.wc);
    fetch('/staffing/leaderboards/order', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({order: [...active, ...inactive]}),
    }).catch(() => {});
    // If a section moved between active/inactive containers, also POST the
    // inactive flag.
    document.querySelectorAll('.lb-active-list .lb-section').forEach(s => {
      if (s.dataset.wasInactive === '1') {
        fetch(`/staffing/leaderboards/wc/${encodeURIComponent(s.dataset.wc)}/active`, {method: 'POST'}).catch(() => {});
        delete s.dataset.wasInactive;
      }
    });
    document.querySelectorAll('.lb-inactive-wrap .lb-section').forEach(s => {
      if (s.dataset.wasActive === '1') {
        fetch(`/staffing/leaderboards/wc/${encodeURIComponent(s.dataset.wc)}/inactive`, {method: 'POST'}).catch(() => {});
        delete s.dataset.wasActive;
      }
    });
  }

  document.querySelectorAll('.lb-hide-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const sec = btn.closest('.lb-section');
      const name = sec.dataset.wc;
      const resp = await fetch(`/staffing/leaderboards/wc/${encodeURIComponent(name)}/inactive`, {method: 'POST'});
      if (resp.ok) window.location.reload();
    });
  });

  document.querySelectorAll('.lb-show-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const sec = btn.closest('.lb-section');
      const name = sec.dataset.wc;
      const resp = await fetch(`/staffing/leaderboards/wc/${encodeURIComponent(name)}/active`, {method: 'POST'});
      if (resp.ok) window.location.reload();
    });
  });
})();
```

The active-list and inactive-wrap need wrapper containers with classes `.lb-active-list` and `.lb-inactive-wrap` so the JS can compute correct order.

- [ ] **Step 7: Smoke render**

```bash
DATABASE_URL=... .venv/Scripts/python.exe -c "
from fastapi.testclient import TestClient
from zira_dashboard.app import app
c = TestClient(app)
r = c.get('/staffing/leaderboards?window=week')
print('status:', r.status_code, 'len:', len(r.text))
print('lb-section:', 'lb-section' in r.text)
print('lb-inactive-wrap:', 'lb-inactive-wrap' in r.text)
"
```

- [ ] **Step 8: Commit**

```bash
git add src/zira_dashboard/templates/leaderboards.html
git commit -m "feat(leaderboards): per-WC top-5 layout with drag, inactive section, custom range"
```

---

### Task 5: Push + verify

- [ ] **Step 1: Push everything**

```bash
git push origin main
```

- [ ] **Step 2: Wait for redeploy**

- [ ] **Step 3: Browser verify**

- Page renders with sections per WC, top-5 rows, dates, name(N).
- Metric toggle works.
- Window chips work; custom from/to inputs override.
- Drag a section — order persists across reload.
- ✕ moves a section to Inactive — sticks across reload.
- Inactive section is collapsed by default; expanding shows hidden WCs + auto-empty WCs.
- ↶ on a manually-inactive WC moves it back; auto-empty WCs are not show-able (they self-promote when data appears).

---

## Done criteria

- All 5 tasks committed and pushed.
- Live page matches the design spec.
- Persistence verified across reload + across redeploys (Postgres-backed).
- Test suite green.
