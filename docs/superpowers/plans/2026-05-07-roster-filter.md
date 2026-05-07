# Roster Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-person `excluded` flag that hides someone from current roster views (People Matrix, scheduler pickers, late report) while preserving their historical assignments and attendance data. Toggleable from a new Roster Filter sub-tab in Settings.

**Architecture:** Single new column on `people` (additive, idempotent DDL). `staffing.load_roster()` becomes the single funnel point — adding `WHERE NOT excluded` there cascades the filter to every current-roster surface. Settings page gains a new section + a tiny POST endpoint to flip the flag per person.

**Tech Stack:** Python 3.12 / FastAPI / Jinja2 / Postgres (psycopg2). Test runner: `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-05-07-roster-filter-design.md`

---

## File map

| File | Change |
|---|---|
| `src/zira_dashboard/db.py` | Idempotent DDL: `ALTER TABLE people ADD COLUMN IF NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE`. |
| `src/zira_dashboard/staffing.py` | `load_roster()` SELECT adds `WHERE NOT p.excluded`. |
| `src/zira_dashboard/routes/settings.py` | Accept `roster_filter` as a section value; load `roster_filter_rows` for that section; new POST `/api/settings/roster-filter/toggle`. |
| `src/zira_dashboard/templates/settings.html` | New sidebar entry + new panel + inline JS. |
| `tests/test_roster_filter.py` (new) | Roster-filter unit + endpoint tests. |
| `CHANGELOG.md` | Entry for the deploy. |

---

### Task 1: Schema — `excluded` column on `people`

**Files:**
- Modify: `src/zira_dashboard/db.py`

- [ ] **Step 1: Add the DDL**

In `src/zira_dashboard/db.py`, find the `_SCHEMA_DDL` string (around line 136). Locate the `people` table definition (around line 139). Immediately AFTER the `people` table block (and any indexes on it), add:

```sql
ALTER TABLE people ADD COLUMN IF NOT EXISTS excluded BOOLEAN NOT NULL DEFAULT FALSE;
```

- [ ] **Step 2: Verify the SQL parses**

```
.venv/Scripts/python.exe -c "
import zira_dashboard.db as db
assert 'ALTER TABLE people ADD COLUMN IF NOT EXISTS excluded' in db._SCHEMA_DDL
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/db.py
git commit -m "schema: add excluded flag to people

Local-only flag for the upcoming Roster Filter — defaults False so
existing rows behave exactly as today. Idempotent.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `staffing.load_roster()` filters out excluded people

**Files:**
- Modify: `src/zira_dashboard/staffing.py`
- Modify: `tests/test_roster_filter.py` (create if it doesn't exist)

- [ ] **Step 1: Write the failing test**

Create `tests/test_roster_filter.py`:

```python
import os
import pytest

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; this test needs Postgres.",
)


@requires_db
def test_load_roster_skips_excluded_people():
    """staffing.load_roster() returns only NOT excluded rows."""
    from zira_dashboard import db, staffing

    # Seed a known person, mark excluded, confirm they don't appear.
    db.execute(
        "INSERT INTO people (odoo_id, name, active, excluded) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (odoo_id) DO UPDATE SET excluded = EXCLUDED.excluded, "
        "  name = EXCLUDED.name, active = EXCLUDED.active",
        (999991, "EXCLUDED Test Person", True, True),
    )
    db.execute(
        "INSERT INTO people (odoo_id, name, active, excluded) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (odoo_id) DO UPDATE SET excluded = EXCLUDED.excluded, "
        "  name = EXCLUDED.name, active = EXCLUDED.active",
        (999992, "VISIBLE Test Person", True, False),
    )
    staffing._invalidate_roster_cache()

    roster = staffing.load_roster()
    names = {p.name for p in roster}
    assert "VISIBLE Test Person" in names
    assert "EXCLUDED Test Person" not in names

    # Cleanup.
    db.execute("DELETE FROM people WHERE odoo_id IN (%s, %s)", (999991, 999992))
    staffing._invalidate_roster_cache()


@requires_db
def test_load_roster_includes_inactive_but_not_excluded():
    """Inactive people are still returned (sorted to bottom). Excluded
    are dropped regardless of active status."""
    from zira_dashboard import db, staffing

    db.execute(
        "INSERT INTO people (odoo_id, name, active, excluded) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (odoo_id) DO UPDATE SET excluded = EXCLUDED.excluded, "
        "  name = EXCLUDED.name, active = EXCLUDED.active",
        (999993, "INACTIVE Test", False, False),
    )
    db.execute(
        "INSERT INTO people (odoo_id, name, active, excluded) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (odoo_id) DO UPDATE SET excluded = EXCLUDED.excluded, "
        "  name = EXCLUDED.name, active = EXCLUDED.active",
        (999994, "INACTIVE+EXCLUDED Test", False, True),
    )
    staffing._invalidate_roster_cache()

    roster = staffing.load_roster()
    names = {p.name for p in roster}
    assert "INACTIVE Test" in names
    assert "INACTIVE+EXCLUDED Test" not in names

    db.execute("DELETE FROM people WHERE odoo_id IN (%s, %s)", (999993, 999994))
    staffing._invalidate_roster_cache()
```

- [ ] **Step 2: Run the failing tests**

```
.venv/Scripts/python.exe -m pytest tests/test_roster_filter.py -v -k "load_roster"
```

Without `DATABASE_URL`: tests SKIP. With `DATABASE_URL`: tests FAIL because the SELECT in `load_roster()` doesn't filter yet.

- [ ] **Step 3: Add the WHERE clause**

Open `src/zira_dashboard/staffing.py`. Find `load_roster()` (around line 227). Find the SQL inside `db.query(...)` and add `WHERE NOT p.excluded` between the JOIN block and the GROUP BY. The full updated SQL string:

```python
    rows = db.query(
        "SELECT p.id, p.name, p.active, p.reserve, p.odoo_id, "
        "  COALESCE(json_object_agg(s.name, ps.level) "
        "           FILTER (WHERE s.name IS NOT NULL), '{}'::json)::text AS skills_json "
        "FROM people p "
        "LEFT JOIN person_skills ps ON ps.person_id = p.id "
        "LEFT JOIN skills s ON s.id = ps.skill_id "
        "WHERE NOT p.excluded "
        "GROUP BY p.id "
        "ORDER BY (NOT p.active), lower(p.name)"
    )
```

Update the docstring on the function to mention the new filter:

```python
def load_roster() -> list[Person]:
    """Load all NON-EXCLUDED people from Postgres. Inactive people
    are returned too (sorted to the bottom). Excluded people are
    filtered out — they're hidden from current views via the
    Settings → Roster Filter UI. Cached in-process for 60 s;
    invalidated on save_roster()."""
```

- [ ] **Step 4: Run the tests**

```
.venv/Scripts/python.exe -m pytest tests/test_roster_filter.py -v -k "load_roster"
```

Without `DATABASE_URL`: skip. With it: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/staffing.py tests/test_roster_filter.py
git commit -m "feat(staffing): load_roster filters out excluded people

Single funnel point. WHERE NOT p.excluded in the SELECT cascades the
filter to every current-roster surface that goes through
load_roster — People Matrix, scheduler picker, player-card
picklist, late-report unscheduled detection, etc.

Historical paths (past_schedules, leaderboards, attendance) query
schedule_assignments / production_history directly by name and don't
go through load_roster, so they remain unaffected (intentional —
old assignment data still references excluded people for
retrospective reporting).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Settings route accepts `roster_filter` section + loads rows

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py`

- [ ] **Step 1: Update `settings_page` to accept the new section value**

Find the existing whitelist (around line 50):

```python
    if section not in ("work_centers", "schedule", "integrations"):
        section = "work_centers"
```

Replace with:

```python
    if section not in ("work_centers", "schedule", "integrations", "roster_filter"):
        section = "work_centers"
```

- [ ] **Step 2: Load `roster_filter_rows` when that section is active**

Just before the existing `integration_status = None` block, add:

```python
    roster_filter_rows: list[dict] = []
    if section == "roster_filter":
        from .. import db
        roster_filter_rows = db.query(
            "SELECT odoo_id, name, excluded "
            "FROM people "
            "WHERE odoo_id IS NOT NULL "
            "ORDER BY lower(name)"
        )
```

This bypasses `load_roster()` deliberately — the filter UI must show *excluded* people too, otherwise you can't un-exclude them.

- [ ] **Step 3: Pass the new context to the template**

Find the `templates.TemplateResponse` context dict (around line 158). Add:

```python
            "roster_filter_rows": roster_filter_rows,
```

(Place it right after `"active_section": section,`.)

- [ ] **Step 4: Add the toggle endpoint**

At the bottom of `routes/settings.py`, append:

```python
@router.post("/api/settings/roster-filter/toggle")
async def roster_filter_toggle(request: Request):
    """Flip the `excluded` flag on a single person.

    Body (JSON): {odoo_id: int, excluded: bool}
    Side effects: UPDATE people SET excluded = $excluded WHERE odoo_id = $odoo_id;
    invalidate the roster cache so the next /staffing render picks up
    the change.
    """
    from .. import db, staffing
    body = await request.json()
    odoo_id_raw = body.get("odoo_id")
    excluded_raw = body.get("excluded")
    try:
        odoo_id = int(odoo_id_raw)
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "odoo_id required (int)"}, status_code=400)
    if not isinstance(excluded_raw, bool):
        return JSONResponse({"ok": False, "error": "excluded must be true or false"}, status_code=400)
    db.execute(
        "UPDATE people SET excluded = %s WHERE odoo_id = %s",
        (excluded_raw, odoo_id),
    )
    staffing._invalidate_roster_cache()
    return JSONResponse({"ok": True})
```

- [ ] **Step 5: Smoke-test the import**

```
.venv/Scripts/python.exe -c "
from zira_dashboard.app import app
paths = [r.path for r in app.routes]
assert '/api/settings/roster-filter/toggle' in paths
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/settings.py
git commit -m "feat(settings): roster_filter section + toggle endpoint

Settings page now accepts ?section=roster_filter and loads
roster_filter_rows directly from the people table (bypasses
load_roster so excluded people still appear with their checkbox
unchecked). New POST /api/settings/roster-filter/toggle flips the
excluded flag per (odoo_id) and invalidates the roster cache.

Template change comes in the next commit; the new section and
endpoint are inert until then.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Settings template — sidebar entry, panel, JS

**Files:**
- Modify: `src/zira_dashboard/templates/settings.html`

- [ ] **Step 1: Add the sidebar entry**

Find the `<aside class="settings-sidebar"` block (around line 501). It currently has three `<a>` entries (Work Centers & Goals, Company Schedule, Integrations). Insert a new entry after Work Centers & Goals (so the new tab sits between Work Centers and Schedule):

```jinja
    <a href="?section=work_centers"
       class="settings-nav-item {% if active_section == 'work_centers' %}active{% endif %}">
      Work Centers &amp; Goals
    </a>
    <a href="?section=roster_filter"
       class="settings-nav-item {% if active_section == 'roster_filter' %}active{% endif %}">
      Roster Filter
    </a>
    <a href="?section=schedule"
       class="settings-nav-item {% if active_section == 'schedule' %}active{% endif %}">
      Company Schedule
    </a>
```

- [ ] **Step 2: Add the panel HTML + CSS rule**

Find the existing Integrations panel (around `<section class="panel" id="integrations-panel"` near line 783). Insert this new panel BEFORE it, inside the same `<div class="settings-content">` parent:

```jinja
  <!-- Roster Filter -->
  <section class="panel" id="roster-filter-panel"
           {% if active_section != 'roster_filter' %}style="display:none"{% endif %}>
    <h2>Roster Filter</h2>
    <p class="note" style="margin-bottom:0.8rem">
      Uncheck to hide a person from active roster views (People Matrix,
      scheduler dropdowns, late/absence report, etc.). Historical data
      stays intact — past schedules, leaderboards, and attendance
      records still show them.
    </p>
    {% if roster_filter_rows %}
    <ul class="roster-filter-list" style="list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill, minmax(20rem, 1fr));gap:0.4rem 1rem">
      {% for p in roster_filter_rows %}
      <li class="roster-filter-row" data-odoo-id="{{ p.odoo_id }}"
          style="padding:0.3rem 0.5rem;border-radius:6px">
        <label style="display:flex;align-items:center;gap:0.5rem;cursor:pointer">
          <input type="checkbox"
                 class="roster-filter-toggle"
                 {% if not p.excluded %}checked{% endif %}>
          <span class="roster-filter-name" style="flex:1">{{ p.name }}</span>
          <span class="roster-filter-meta" style="color:var(--muted);font-size:0.78rem">Odoo #{{ p.odoo_id }}</span>
        </label>
      </li>
      {% endfor %}
    </ul>
    {% else %}
    <p style="color:var(--muted);font-style:italic">No Odoo-synced people yet. Run an Odoo sync first.</p>
    {% endif %}
  </section>
```

- [ ] **Step 3: Add the toggle JS**

Find the existing `<script>` block at the bottom of `settings.html` (search for `document.querySelectorAll('.target-input')` — that's an inline script near the bottom of the file). After the existing script's closing `</script>`, add a new `<script>` block:

```html
<script>
document.querySelectorAll('.roster-filter-toggle').forEach(function (cb) {
  cb.addEventListener('change', function () {
    var li = cb.closest('.roster-filter-row');
    if (!li) return;
    var odoo_id = parseInt(li.dataset.odooId, 10);
    var excluded = !cb.checked;
    var origColor = li.style.background;
    li.style.background = 'var(--accent-dim)';
    fetch('/api/settings/roster-filter/toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({odoo_id: odoo_id, excluded: excluded}),
    }).then(function (r) {
      if (!r.ok) {
        cb.checked = !cb.checked;  // roll back the visual
        li.style.background = 'var(--bad-dim)';
        setTimeout(function () { li.style.background = origColor; }, 1200);
      } else {
        setTimeout(function () { li.style.background = origColor; }, 600);
      }
    }).catch(function () {
      cb.checked = !cb.checked;
      li.style.background = 'var(--bad-dim)';
      setTimeout(function () { li.style.background = origColor; }, 1200);
    });
  });
});
</script>
```

- [ ] **Step 4: Smoke-test that the template parses**

```
.venv/Scripts/python.exe -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('src/zira_dashboard/templates'))
env.get_template('settings.html')
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/settings.html
git commit -m "feat(settings): Roster Filter panel + sidebar entry

New sub-tab between Work Centers & Goals and Company Schedule.
Renders one row per Odoo-synced person with a checkbox; click a
checkbox and the toggle endpoint flips excluded for that
(odoo_id). On success the row briefly tints accent-green; on
failure it tints red and the visual rolls back.

Used inline styles for the grid + visual feedback to keep the
change self-contained — no new CSS class definitions needed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Endpoint + sync-preserve regression tests

**Files:**
- Modify: `tests/test_roster_filter.py`
- Modify: `tests/test_odoo_sync.py` (extend if it exists; otherwise create)

- [ ] **Step 1: Append endpoint tests to `tests/test_roster_filter.py`**

```python
def test_toggle_endpoint_400_when_odoo_id_missing():
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app
    client = TestClient(app)
    r = client.post(
        "/api/settings/roster-filter/toggle",
        json={"excluded": True},
    )
    assert r.status_code == 400


def test_toggle_endpoint_400_when_odoo_id_not_int():
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app
    client = TestClient(app)
    r = client.post(
        "/api/settings/roster-filter/toggle",
        json={"odoo_id": "not-an-int", "excluded": True},
    )
    assert r.status_code == 400


def test_toggle_endpoint_400_when_excluded_not_bool():
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app
    client = TestClient(app)
    r = client.post(
        "/api/settings/roster-filter/toggle",
        json={"odoo_id": 123, "excluded": "yes"},
    )
    assert r.status_code == 400


def test_toggle_endpoint_writes_excluded_flag(monkeypatch):
    """Mock db.execute and assert it gets called with (excluded, odoo_id).
    Verifies the SQL shape without needing DATABASE_URL."""
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from zira_dashboard.app import app
    from zira_dashboard import db, staffing

    spy = MagicMock()
    monkeypatch.setattr(db, "execute", spy)
    monkeypatch.setattr(staffing, "_invalidate_roster_cache", MagicMock())

    client = TestClient(app)
    r = client.post(
        "/api/settings/roster-filter/toggle",
        json={"odoo_id": 1234, "excluded": True},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    spy.assert_called_once()
    args = spy.call_args.args
    assert "UPDATE people SET excluded" in args[0]
    assert args[1] == (True, 1234)
```

- [ ] **Step 2: Run the new tests**

```
.venv/Scripts/python.exe -m pytest tests/test_roster_filter.py -v -k "toggle_endpoint"
```

Expected: 4 PASS.

- [ ] **Step 3: Sync-preserves-excluded regression test**

`tests/test_odoo_sync.py` doesn't currently exist (verified by `Glob tests/test_odoo_*`); create it. Just one test, gated on DATABASE_URL:

```python
import os
import pytest

requires_db = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="DATABASE_URL not set; this test needs Postgres.",
)


@requires_db
def test_sync_upsert_does_not_clear_excluded_flag():
    """The Odoo sync's INSERT … ON CONFLICT (odoo_id) DO UPDATE clause
    only names (name, active, last_pulled_at) — local-only columns
    like reserve and excluded must survive across syncs.

    Validate by simulating a sync's UPSERT against a row that's
    already marked excluded, and checking the flag is preserved.
    """
    from datetime import datetime, timezone
    from zira_dashboard import db

    # Seed a row with excluded=TRUE.
    db.execute(
        "INSERT INTO people (odoo_id, name, active, excluded, last_pulled_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
        "  active = EXCLUDED.active, excluded = EXCLUDED.excluded, "
        "  last_pulled_at = EXCLUDED.last_pulled_at",
        (999995, "EXCLUDED Sync Test", True, True, datetime.now(timezone.utc)),
    )

    # Simulate the sync's UPSERT (matches odoo_sync.sync()'s SQL exactly).
    db.execute(
        "INSERT INTO people (odoo_id, name, active, last_pulled_at) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
        "active = EXCLUDED.active, last_pulled_at = EXCLUDED.last_pulled_at",
        (999995, "EXCLUDED Sync Test (renamed)", True, datetime.now(timezone.utc)),
    )

    rows = db.query(
        "SELECT excluded FROM people WHERE odoo_id = %s", (999995,)
    )
    assert rows[0]["excluded"] is True

    # Cleanup.
    db.execute("DELETE FROM people WHERE odoo_id = %s", (999995,))
```

- [ ] **Step 4: Run the regression test**

```
.venv/Scripts/python.exe -m pytest tests/test_odoo_sync.py -v
```

Without `DATABASE_URL`: skip. With it: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_roster_filter.py tests/test_odoo_sync.py
git commit -m "test: roster filter endpoint + sync-preserves-excluded

Four endpoint tests run without DATABASE_URL (mocked db.execute).
DB-backed tests for load_roster filtering and sync upsert
preservation skip when no DATABASE_URL is set, same pattern as the
rest of the suite.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Final test pass + CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the non-DB test suite**

```
.venv/Scripts/python.exe -m pytest tests/test_progress.py tests/test_deps_window_dates.py tests/test_share_route.py tests/test_results.py tests/test_zira_persist.py tests/test_slack_client.py tests/test_late_report.py tests/test_views_store.py tests/test_wc_attributions.py tests/test_leaderboards_avg.py tests/test_production_history.py tests/test_leaderboards_person_days.py tests/test_player_card.py tests/test_roster_filter.py -q
```

Expected: all PASS (DB-bound tests skip).

- [ ] **Step 2: Get the time**

```
date "+%I:%M %p"
```

- [ ] **Step 3: Add the CHANGELOG entry**

In `CHANGELOG.md`, insert at the top of today's `## 2026-05-07` section:

```markdown
### {time-from-step-2}

- **Roster Filter — exclude specific Odoo employees from current views** — new sub-tab in Settings (between Work Centers & Goals and Company Schedule). Renders one row per Odoo-synced person with a checkbox; uncheck to hide that person from the People Matrix, scheduler pickers, player-card picklist, and late/absence report. The exclusion flag is local-only — Odoo's hourly sync preserves it across runs the same way it preserves the `reserve` flag. Historical data (past schedules, leaderboards, attendance records) is unaffected — old assignment data still references excluded people, by design.
```

- [ ] **Step 4: Commit + push**

```bash
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
feat: Roster Filter — per-person exclusion from current views

Settings → Roster Filter sub-tab with one checkbox per Odoo-synced
person. The new excluded flag on people defaults to FALSE and is
preserved across Odoo syncs. staffing.load_roster() filters
WHERE NOT excluded, so every current-roster surface inherits the
filter automatically. Historical paths (past schedules,
leaderboards, attendance) bypass load_roster and remain
unaffected — old data still references excluded people.

Spec: docs/superpowers/specs/2026-05-07-roster-filter-design.md
Plan: docs/superpowers/plans/2026-05-07-roster-filter.md

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
git push origin main
```

---

## Self-review checklist

- [ ] Spec goal 1 (`excluded` column preserved across sync) — covered by Task 1 (DDL) + Task 5 step 3 (regression test). Sync code itself doesn't need touching because its UPSERT clause already only names sync-owned columns ✓
- [ ] Spec goal 2 (Settings sub-tab + per-row toggle) — Tasks 3, 4 ✓
- [ ] Spec goal 3 (every current-roster surface inherits the filter) — Task 2 (single funnel point) ✓
- [ ] Spec goal 4 (historical data unaffected) — implicit; historical paths don't go through `load_roster()`, so no task needed. Documented in Task 2 commit message ✓
- [ ] All testing items in spec — Task 2 (load_roster filter), Task 5 (endpoint + sync regression) ✓
- [ ] No placeholders, no TBDs ✓
- [ ] Type / column-name consistency: `excluded` (boolean), `odoo_id` (int), `name` (text). Used identically in DDL (Task 1), SQL (Task 2), endpoint body (Task 3), template data attrs (Task 4), tests (Tasks 2, 5) ✓
- [ ] Endpoint URL `/api/settings/roster-filter/toggle` matches between route definition (Task 3), template fetch (Task 4), and tests (Task 5) ✓
- [ ] Body shape `{odoo_id, excluded}` consistent across endpoint impl, template fetch, and tests ✓
