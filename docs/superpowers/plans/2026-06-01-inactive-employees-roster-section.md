# Inactive Employees in the Settings Roster Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Settings → Roster Filter into an Active and an Inactive section (Inactive sourced from Odoo's `active = false` flag, read-only), and make every name link to its employee card.

**Architecture:** Surface the existing `people.active` flag in the Settings UI. The route gains a tiny pure split helper plus a loader that adds `active` to its query; the roster-filter panel body is extracted into a `_roster_filter.html` partial that renders the two sections and the card links. No schema change, no Odoo-sync change — every live picker already filters to `active`, and leaderboards/trophies already read history independent of the roster.

**Tech Stack:** FastAPI routes (`src/zira_dashboard/routes/settings.py`), Jinja2 templates (`src/zira_dashboard/templates/`), Postgres via `zira_dashboard.db`, pytest.

**Spec:** `docs/superpowers/specs/2026-06-01-inactive-employees-roster-section-design.md`

---

## Environment note (read before running anything)

- **Local interpreter is Python 3.9; the project requires 3.11.** `fastapi` is **not** importable locally, so any pytest that touches `zira_dashboard.app` or imports `zira_dashboard.routes.settings` runs **only in the real 3.11+ environment** (or on the deployed app). There is no CI; production is Railway auto-deploy on push to `main`.
- **`jinja2` *is* importable locally (3.1.6).** The template partial test and the local verification snippets below are designed to run on local 3.9 with no app import.
- For each task: the `pytest` command is the **canonical** check (real env). The **Local (3.9)** block is what you run on this machine before pushing.

## File structure

- **Modify** `src/zira_dashboard/routes/settings.py`
  - Add pure helper `_split_roster_rows(rows)` and loader `_roster_filter_lists()`.
  - Swap the inline roster-filter query + `roster_filter_rows` context var for `roster_filter_active` / `roster_filter_inactive`.
- **Create** `src/zira_dashboard/templates/_roster_filter.html`
  - Self-contained partial (no `extends`) rendering the intro copy, Active section (with hide checkboxes + card links), and Inactive section (read-only + card links). Depends only on `roster_filter_active` and `roster_filter_inactive`.
- **Modify** `src/zira_dashboard/templates/settings.html`
  - Replace the roster-filter panel **body** (intro `<p>` + the list block) with `{% include "_roster_filter.html" %}`. Keep the `<section>` wrapper, the `<h2>`, and the page-level toggle `<script>` unchanged.
- **Modify** `tests/test_roster_filter.py`
  - Add unit tests for the helper + loader and Jinja render tests for the partial.

---

### Task 1: Pure split helper `_split_roster_rows`

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py` (add helper just after `_loc_by_key`, ~line 50)
- Test: `tests/test_roster_filter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_roster_filter.py`:

```python
def test_split_roster_rows_separates_active_and_inactive():
    """Active rows (active truthy) and inactive rows are split, order preserved."""
    from zira_dashboard.routes.settings import _split_roster_rows

    rows = [
        {"odoo_id": 1, "name": "Ana", "excluded": False, "active": True},
        {"odoo_id": 2, "name": "Zed", "excluded": False, "active": False},
        {"odoo_id": 3, "name": "Cara", "excluded": True, "active": True},
    ]
    active, inactive = _split_roster_rows(rows)
    assert [r["name"] for r in active] == ["Ana", "Cara"]
    assert [r["name"] for r in inactive] == ["Zed"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run (real env): `pytest tests/test_roster_filter.py::test_split_roster_rows_separates_active_and_inactive -v`
Expected: FAIL — `ImportError: cannot import name '_split_roster_rows'`.

Local (3.9) — confirm the function does not yet exist:
```bash
python3 - <<'PY'
import ast
tree = ast.parse(open("src/zira_dashboard/routes/settings.py").read())
names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
print("present" if "_split_roster_rows" in names else "absent (expected before impl)")
PY
```
Expected: `absent (expected before impl)`.

- [ ] **Step 3: Write the minimal implementation**

In `src/zira_dashboard/routes/settings.py`, immediately after the `_loc_by_key` function (~line 50), add:

```python
def _split_roster_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split roster-filter rows into (active, inactive) by the `active`
    flag. Input order is preserved within each list (the query already
    sorts by name)."""
    active = [r for r in rows if r.get("active")]
    inactive = [r for r in rows if not r.get("active")]
    return active, inactive
```

(`settings.py` already has `from __future__ import annotations`, so the `tuple[...]` / `list[dict]` annotations are deferred strings — safe on 3.11.)

- [ ] **Step 4: Run the test to verify it passes**

Run (real env): `pytest tests/test_roster_filter.py::test_split_roster_rows_separates_active_and_inactive -v`
Expected: PASS.

Local (3.9) — exec the function in isolation via `ast` (no app import):
```bash
python3 - <<'PY'
import ast
tree = ast.parse(open("src/zira_dashboard/routes/settings.py").read())
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "_split_roster_rows")
ns = {}
exec("from __future__ import annotations\n" + ast.unparse(fn), ns)
split = ns["_split_roster_rows"]
rows = [
    {"odoo_id": 1, "name": "Ana", "excluded": False, "active": True},
    {"odoo_id": 2, "name": "Zed", "excluded": False, "active": False},
    {"odoo_id": 3, "name": "Cara", "excluded": True, "active": True},
]
active, inactive = split(rows)
assert [r["name"] for r in active] == ["Ana", "Cara"], active
assert [r["name"] for r in inactive] == ["Zed"], inactive
print("OK: _split_roster_rows splits active/inactive correctly")
PY
```
Expected: `OK: _split_roster_rows splits active/inactive correctly`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/settings.py tests/test_roster_filter.py
git commit -m "feat(settings): add _split_roster_rows helper for roster active/inactive split"
```

---

### Task 2: Loader `_roster_filter_lists` + wire into the settings route

**Files:**
- Modify: `src/zira_dashboard/routes/settings.py` (add loader after `_split_roster_rows`; change the `section == "roster_filter"` block at ~line 61-69; change the context dict line `"roster_filter_rows": roster_filter_rows,` at ~line 288)
- Test: `tests/test_roster_filter.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_roster_filter.py`:

```python
def test_roster_filter_lists_queries_active_and_splits(monkeypatch):
    """_roster_filter_lists() selects the `active` column and returns the
    rows split into (active, inactive)."""
    from zira_dashboard import db
    from zira_dashboard.routes import settings as settings_route

    captured = {}

    def fake_query(sql, *args):
        captured["sql"] = sql
        return [
            {"odoo_id": 1, "name": "Ana", "excluded": False, "active": True},
            {"odoo_id": 2, "name": "Zed", "excluded": False, "active": False},
        ]

    monkeypatch.setattr(db, "query", fake_query)
    active, inactive = settings_route._roster_filter_lists()

    assert "active" in captured["sql"].lower()
    assert "where odoo_id is not null" in captured["sql"].lower()
    assert [r["name"] for r in active] == ["Ana"]
    assert [r["name"] for r in inactive] == ["Zed"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run (real env): `pytest tests/test_roster_filter.py::test_roster_filter_lists_queries_active_and_splits -v`
Expected: FAIL — `AttributeError: module 'zira_dashboard.routes.settings' has no attribute '_roster_filter_lists'`.

(Local 3.9 cannot import `settings` — skip; covered by Step 4's local check.)

- [ ] **Step 3: Add the loader**

In `src/zira_dashboard/routes/settings.py`, directly after `_split_roster_rows`, add:

```python
def _roster_filter_lists() -> tuple[list[dict], list[dict]]:
    """Load Odoo-synced people for the Settings roster filter, split into
    (active, inactive). Active and inactive are each alphabetical by name."""
    from .. import db
    rows = db.query(
        "SELECT odoo_id, name, excluded, active "
        "FROM people "
        "WHERE odoo_id IS NOT NULL "
        "ORDER BY lower(name)"
    )
    return _split_roster_rows(rows)
```

- [ ] **Step 4: Wire it into the route + context**

In `settings_page`, replace this block (currently ~lines 61-69):

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

with:

```python
    roster_filter_active: list[dict] = []
    roster_filter_inactive: list[dict] = []
    if section == "roster_filter":
        roster_filter_active, roster_filter_inactive = _roster_filter_lists()
```

Then in the `templates.TemplateResponse(...)` context dict, replace the line:

```python
            "roster_filter_rows": roster_filter_rows,
```

with:

```python
            "roster_filter_active": roster_filter_active,
            "roster_filter_inactive": roster_filter_inactive,
```

- [ ] **Step 5: Run the test to verify it passes**

Run (real env): `pytest tests/test_roster_filter.py::test_roster_filter_lists_queries_active_and_splits -v`
Expected: PASS.

Local (3.9) — syntax check + confirm `roster_filter_rows` is fully removed and the new wiring is present:
```bash
python3 -m py_compile src/zira_dashboard/routes/settings.py && echo "py_compile OK"
grep -n "roster_filter_rows" src/zira_dashboard/routes/settings.py || echo "roster_filter_rows fully removed (expected)"
grep -n "roster_filter_active\|roster_filter_inactive\|_roster_filter_lists" src/zira_dashboard/routes/settings.py
```
Expected: `py_compile OK`; `roster_filter_rows fully removed (expected)`; and the grep shows the loader call plus both context keys.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/routes/settings.py tests/test_roster_filter.py
git commit -m "feat(settings): load roster filter as active/inactive lists from Odoo flag"
```

---

### Task 3: `_roster_filter.html` partial + include + links + copy + styling

**Files:**
- Create: `src/zira_dashboard/templates/_roster_filter.html`
- Modify: `src/zira_dashboard/templates/settings.html` (replace the panel body ~lines 250-273 with an include)
- Test: `tests/test_roster_filter.py`

- [ ] **Step 1: Write the failing render tests**

Add to `tests/test_roster_filter.py` (uses only `jinja2`, so it runs locally on 3.9 too):

```python
def _render_roster_filter(active, inactive):
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    env = Environment(
        loader=FileSystemLoader("src/zira_dashboard/templates"),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("_roster_filter.html").render(
        roster_filter_active=active,
        roster_filter_inactive=inactive,
    )


def test_roster_filter_partial_active_checkbox_inactive_readonly_and_links():
    html = _render_roster_filter(
        [
            {"odoo_id": 1, "name": "Ana", "excluded": False, "active": True},
            {"odoo_id": 3, "name": "Cara", "excluded": True, "active": True},
        ],
        [{"odoo_id": 2, "name": "Zed", "excluded": False, "active": False}],
    )
    # one toggle per ACTIVE row only — inactive rows are read-only (no checkbox)
    assert html.count("roster-filter-toggle") == 2
    # only the not-excluded active row is pre-checked
    assert html.count("checked") == 1
    # every name links to its employee card
    assert 'href="/staffing/people/Ana"' in html
    assert 'href="/staffing/people/Cara"' in html
    assert 'href="/staffing/people/Zed"' in html
    # both section headings present, with counts
    assert 'id="roster-filter-active-heading"' in html
    assert 'id="roster-filter-inactive-heading"' in html


def test_roster_filter_partial_hides_inactive_heading_when_none():
    html = _render_roster_filter(
        [{"odoo_id": 1, "name": "Ana", "excluded": False, "active": True}],
        [],
    )
    assert 'id="roster-filter-inactive-heading"' not in html
    assert html.count("roster-filter-toggle") == 1


def test_roster_filter_partial_empty_state():
    html = _render_roster_filter([], [])
    assert "No Odoo-synced people yet" in html
    assert 'id="roster-filter-active-heading"' not in html
    assert 'id="roster-filter-inactive-heading"' not in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (works locally on 3.9 — jinja2 only): `python3 -m pytest tests/test_roster_filter.py -k roster_filter_partial -v` *(if pytest is unavailable locally, run the standalone snippet in Step 4 instead).*
Expected: FAIL — `jinja2.exceptions.TemplateNotFound: _roster_filter.html`.

- [ ] **Step 3: Create the partial**

Create `src/zira_dashboard/templates/_roster_filter.html` with exactly:

```html
<p class="note" style="margin-bottom:0.8rem">
  <strong>Active</strong> employees can be unchecked to hide them from live roster
  views (People Matrix, scheduler dropdowns, late/absence report).
  <strong>Inactive</strong> employees are archived in Odoo — already hidden from
  scheduling and pickers, and shown here read-only. Click any name to open their
  card. Historical data (past schedules, leaderboards, trophies) stays intact for
  everyone.
</p>

{% if roster_filter_active or roster_filter_inactive %}
  <h3 id="roster-filter-active-heading" style="margin:0.6rem 0 0.4rem;font-size:0.95rem">
    Active <span style="color:var(--muted);font-weight:normal">({{ roster_filter_active | length }})</span>
  </h3>
  {% if roster_filter_active %}
  <ul class="roster-filter-list" style="list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill, minmax(20rem, 1fr));gap:0.4rem 1rem">
    {% for p in roster_filter_active %}
    <li class="roster-filter-row" data-odoo-id="{{ p.odoo_id }}"
        style="display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0.5rem;border-radius:6px">
      <label style="display:inline-flex;align-items:center;cursor:pointer;margin:0">
        <input type="checkbox" class="roster-filter-toggle"
               {% if not p.excluded %}checked{% endif %}>
      </label>
      <a class="roster-filter-name" href="/staffing/people/{{ p.name | urlencode }}"
         style="flex:1;text-decoration:none;color:inherit">{{ p.name }}</a>
      <span class="roster-filter-meta" style="color:var(--muted);font-size:0.78rem">Odoo #{{ p.odoo_id }}</span>
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p style="color:var(--muted);font-style:italic;margin:0.2rem 0 0">No active employees.</p>
  {% endif %}

  {% if roster_filter_inactive %}
  <h3 id="roster-filter-inactive-heading" style="margin:1rem 0 0.4rem;font-size:0.95rem">
    Inactive <span style="color:var(--muted);font-weight:normal">({{ roster_filter_inactive | length }})</span>
  </h3>
  <ul class="roster-filter-list" style="list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill, minmax(20rem, 1fr));gap:0.4rem 1rem">
    {% for p in roster_filter_inactive %}
    <li class="roster-filter-row roster-filter-inactive"
        style="display:flex;align-items:center;gap:0.5rem;padding:0.3rem 0.5rem;border-radius:6px;opacity:0.6">
      <a class="roster-filter-name" href="/staffing/people/{{ p.name | urlencode }}"
         style="flex:1;text-decoration:none;color:inherit">{{ p.name }}</a>
      <span class="roster-filter-meta" style="color:var(--muted);font-size:0.78rem">Odoo #{{ p.odoo_id }}</span>
    </li>
    {% endfor %}
  </ul>
  {% endif %}
{% else %}
  <p style="color:var(--muted);font-style:italic">No Odoo-synced people yet. Run an Odoo sync first.</p>
{% endif %}
```

- [ ] **Step 4: Include the partial in `settings.html`**

In `src/zira_dashboard/templates/settings.html`, the roster-filter panel currently reads (≈lines 247-274):

```html
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

Replace the intro `<p class="note">…</p>` and everything from `{% if roster_filter_rows %}` through its matching `{% endif %}` with a single include, leaving the `<section>` and `<h2>` intact:

```html
  <section class="panel" id="roster-filter-panel"
           {% if active_section != 'roster_filter' %}style="display:none"{% endif %}>
    <h2>Roster Filter</h2>
    {% include "_roster_filter.html" %}
  </section>
```

Leave the page-level `<script>` that binds `.roster-filter-toggle` (≈line 1077) **unchanged** — inactive rows emit no `.roster-filter-toggle`, so it naturally skips them.

- [ ] **Step 5: Run the render tests to verify they pass**

Run (real env or local): `python3 -m pytest tests/test_roster_filter.py -k roster_filter_partial -v`
Expected: PASS (3 tests).

Local (3.9) standalone fallback if pytest isn't installed — runs the same assertions with jinja2 only:
```bash
python3 - <<'PY'
from jinja2 import Environment, FileSystemLoader, select_autoescape
env = Environment(loader=FileSystemLoader("src/zira_dashboard/templates"),
                  autoescape=select_autoescape(["html"]))
def render(active, inactive):
    return env.get_template("_roster_filter.html").render(
        roster_filter_active=active, roster_filter_inactive=inactive)

html = render(
    [{"odoo_id": 1, "name": "Ana", "excluded": False, "active": True},
     {"odoo_id": 3, "name": "Cara", "excluded": True, "active": True}],
    [{"odoo_id": 2, "name": "Zed", "excluded": False, "active": False}])
assert html.count("roster-filter-toggle") == 2, html.count("roster-filter-toggle")
assert html.count("checked") == 1, html.count("checked")
assert 'href="/staffing/people/Ana"' in html
assert 'href="/staffing/people/Zed"' in html
assert 'id="roster-filter-active-heading"' in html
assert 'id="roster-filter-inactive-heading"' in html

html2 = render([{"odoo_id": 1, "name": "Ana", "excluded": False, "active": True}], [])
assert 'id="roster-filter-inactive-heading"' not in html2
assert html2.count("roster-filter-toggle") == 1

html3 = render([], [])
assert "No Odoo-synced people yet" in html3
print("OK: _roster_filter.html renders active/inactive sections correctly")
PY
```
Expected: `OK: _roster_filter.html renders active/inactive sections correctly`.

- [ ] **Step 6: Commit**

```bash
git add src/zira_dashboard/templates/_roster_filter.html src/zira_dashboard/templates/settings.html tests/test_roster_filter.py
git commit -m "feat(settings): split roster filter into Active/Inactive sections with card links"
```

---

## Manual verification (deployed app / 3.11 env)

The full page render and the toggle round-trip can't run on local 3.9. After pushing to `main` and letting Railway deploy, on the live app:

- [ ] Open **Settings → Roster Filter**. Confirm an **Active (N)** section with checkboxes and an **Inactive (M)** section that is read-only (no checkboxes).
- [ ] Click an **active** name → lands on that employee's card (`/staffing/people/<name>`). Click an **inactive** name → lands on their card too, showing their historical stats/trophies.
- [ ] Toggle an active person's checkbox off → confirm the row flashes and the change persists on reload (the existing `/api/settings/roster-filter/toggle` still works), and that they drop out of the scheduler dropdown.
- [ ] Confirm an inactive person does **not** appear in the scheduler assignment dropdown but **does** still appear in the trophy case / leaderboards.
- [ ] (If you have an archived-in-Odoo employee) confirm they show under **Inactive**, not Active.

## Run the full roster-filter suite (real env)

```bash
pytest tests/test_roster_filter.py -v
```
Expected: all tests pass (existing `load_roster` / toggle tests + the new helper, loader, and partial tests). The DB-marked tests skip automatically when `DATABASE_URL` is unset.

---

## Self-review (plan author ran this)

**1. Spec coverage**
- Two sections (Active/Inactive) → Tasks 2 (data) + 3 (UI). ✓
- Inactive sourced from Odoo `active=false`, read-only → loader queries `active`; partial omits checkbox for inactive. ✓
- Names link to employee card → partial links both sections to `/staffing/people/{name}`. ✓
- Manual hide checkbox stays on active employees → preserved in the Active row markup; toggle JS untouched. ✓
- "Nowhere else" / history visible → no changes to scheduler/pickers/leaderboards (already correct); confirmed by the manual checklist. ✓
- Edge cases (excluded+active, inactive+excluded, empty inactive, empty all) → partial conditionals + render tests cover empty-inactive and empty-all; loader split covers excluded+active and inactive+excluded ordering. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**3. Type/name consistency:** `_split_roster_rows` → `_roster_filter_lists` → context keys `roster_filter_active` / `roster_filter_inactive` → partial variables of the same names → test helper kwargs of the same names. Class/id hooks (`roster-filter-toggle`, `roster-filter-row`, `roster-filter-active-heading`, `roster-filter-inactive-heading`) match between the partial and the render-test assertions, and `roster-filter-toggle` matches the untouched page script. ✓
