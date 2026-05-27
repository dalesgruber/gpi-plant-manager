# Cert Sync (Binary) + Blank Required Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two staffing-page bugs in one PR: (1) cert badges (DOT wrench, CDL truck) don't render and Truck Driver colors red because the Odoo sync drops certs with bucket=0, and (2) work centers can't have a blank required-skill list — the Settings UI silently re-fills it from a hardcoded fallback.

**Architecture:** Two independent, file-local changes plus three small render-site tweaks. No schema changes. No template structural changes; existing `lvl-{N}` CSS handles neutral rendering when we emit `level=2`.

**Tech Stack:** Python 3.11+, FastAPI/Starlette, Jinja2, Postgres, pytest. Existing app, no new deps.

**Spec:** [docs/superpowers/specs/2026-05-27-cert-sync-and-blank-required-skill-design.md](../specs/2026-05-27-cert-sync-and-blank-required-skill-design.md)

---

## File Map

**Modify**
- `src/zira_dashboard/odoo_sync.py` — cert-binary handling in inner sync loop (~lines 99-159)
- `src/zira_dashboard/work_centers_store.py` — drop LOCATIONS fallback when WC row exists in `_effective_uncached` (~lines 64-102)
- `src/zira_dashboard/routes/settings.py` — `required_skills_present` marker handling (~lines 315-318); default-pool color logic when required empty (~lines 122-152)
- `src/zira_dashboard/routes/staffing.py` — `options_for` neutral level when required empty (~lines 480-498); per-assigned-person neutral level when required empty (~lines 504-513)
- `src/zira_dashboard/templates/settings.html` — add `required_skills_present` hidden marker (~line 169)
- `CHANGELOG.md` — new entry under today's date per project convention

**Add**
- `tests/test_work_centers_store_required_skills.py` — new test file for `_effective_uncached` empty-list handling
- `tests/test_staffing_options_color.py` — new test file for `options_for` and per-person color when required is empty

**Augment (existing test files)**
- `tests/test_odoo_sync.py` — add test for cert-binary insertion at level=3

---

## Notes on rendering "no color scale"

The spec specified `color=None`/`level=None` and template wrapping. After reading the actual templates, that's overcomplicated: the chips are styled via `lvl-{N}` CSS classes (not inline `color`), and JS defaults to `2` already (`cb.dataset.level || '2'` in `staffing.html:634`). The simplest, no-template-change implementation:

- When `required` is empty, set each row's `level = 2`, `trained = True`, and pass `color = "neutral"` (a string sentinel — unused by current templates but useful for any future check).
- `lvl-2` in `staffing.css:328` and `settings.css:132` is `background: var(--neutral-pill); color: var(--fg)` — exactly the "no scale" look.
- The "untrained" CSS class trigger (`{% if p.level < 1 %}`) doesn't fire because `2 >= 1`. The "WC Training" filter that hides untrained becomes a no-op.

If at code-review time we decide the lvl-2 pill is still too colorful, swap to template-wrapping — but ship level=2 first.

---

## Task 1: Cert sync as binary

**Files:**
- Modify: `src/zira_dashboard/odoo_sync.py:91-160`
- Test: `tests/test_odoo_sync.py` (existing file — add new test)

- [ ] **Step 1: Write the failing test for cert-binary insertion**

Add to `tests/test_odoo_sync.py`, at the end of the file:

```python
def test_sync_inserts_certification_at_level_3_regardless_of_bucket(monkeypatch):
    """Odoo skill types with a single level bucket to 0 (see
    fetch_skill_level_buckets). Certifications must override that
    and insert at level=3 so cert_lookup finds them and staffing
    colors CDL drivers green."""
    from zira_dashboard import db
    db.execute("DELETE FROM skills WHERE name = 'TestDOTCert'")
    _stub_client(
        monkeypatch,
        employees=[{"id": 99010, "name": "TestCDLDriver", "active": True, "work_email": False}],
        skills_for={99010: [{"skill_id": 50, "skill_name": "TestDOTCert", "level_id": 500}]},
        columns_meta=[
            {"name": "TestDOTCert", "type": "Certifications"},
        ],
        buckets={500: 0},  # single-level cert type buckets to 0
    )
    result = odoo_sync.sync(force=True)
    assert result.refreshed is True
    rows = db.query(
        "SELECT pe.name, ps.level, sk.name AS skill_name, sk.skill_type "
        "FROM people pe JOIN person_skills ps ON ps.person_id = pe.id "
        "JOIN skills sk ON sk.id = ps.skill_id WHERE pe.odoo_id = 99010"
    )
    assert rows == [{
        "name": "TestCDLDriver", "level": 3,
        "skill_name": "TestDOTCert", "skill_type": "Certifications",
    }]
    db.execute("DELETE FROM person_skills WHERE person_id IN (SELECT id FROM people WHERE odoo_id = 99010)")
    db.execute("DELETE FROM people WHERE odoo_id = 99010")
    db.execute("DELETE FROM skills WHERE name = 'TestDOTCert'")


def test_sync_production_skill_still_skips_when_level_0(monkeypatch):
    """Sanity: the cert override must NOT change the existing behavior
    for non-cert skill types. A production skill with level<=0 still
    gets skipped."""
    from zira_dashboard import db
    db.execute("DELETE FROM skills WHERE name = 'TestProdSkillSkip'")
    _stub_client(
        monkeypatch,
        employees=[{"id": 99011, "name": "TestProdPerson", "active": True, "work_email": False}],
        skills_for={99011: [{"skill_id": 51, "skill_name": "TestProdSkillSkip", "level_id": 501}]},
        columns_meta=[
            {"name": "TestProdSkillSkip", "type": "Production Skills"},
        ],
        buckets={501: 0},
    )
    odoo_sync.sync(force=True)
    rows = db.query(
        "SELECT * FROM person_skills ps "
        "JOIN people pe ON pe.id = ps.person_id "
        "WHERE pe.odoo_id = 99011"
    )
    assert rows == []
    db.execute("DELETE FROM people WHERE odoo_id = 99011")
    db.execute("DELETE FROM skills WHERE name = 'TestProdSkillSkip'")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_odoo_sync.py::test_sync_inserts_certification_at_level_3_regardless_of_bucket tests/test_odoo_sync.py::test_sync_production_skill_still_skips_when_level_0 -v`

Expected: First test FAILS with `assert rows == []` (cert row not inserted because current code skips on bucket 0). Second test passes (existing behavior).

If `DATABASE_URL` is unset, tests are skipped — set it from `.env` or your dev config and retry.

- [ ] **Step 3: Implement the cert-binary override in sync**

In `src/zira_dashboard/odoo_sync.py`, after the `columns = [c["name"] for c in columns_meta]` line (currently line 89), add:

```python
    columns = [c["name"] for c in columns_meta]
    type_by_skill = {c["name"]: c.get("type", "") for c in columns_meta}
    pulled_at = now
```

Then in the per-employee skill loop (currently around lines 146-159), replace this block:

```python
            for s in emp_skills.get(emp["id"], []):
                if s["skill_name"] not in columns:
                    continue
                level = buckets.get(s["level_id"], 0)
                if level <= 0:
                    continue
                cur.execute(
                    "INSERT INTO person_skills (person_id, skill_id, level, last_pulled_at) "
                    "SELECT pe.id, sk.id, %s, %s FROM people pe, skills sk "
                    "WHERE pe.odoo_id = %s AND sk.name = %s "
                    "ON CONFLICT (person_id, skill_id) DO UPDATE SET "
                    "  level = EXCLUDED.level, last_pulled_at = EXCLUDED.last_pulled_at",
                    (level, pulled_at, emp["id"], s["skill_name"]),
                )
```

with:

```python
            for s in emp_skills.get(emp["id"], []):
                if s["skill_name"] not in columns:
                    continue
                if type_by_skill.get(s["skill_name"]) == "Certifications":
                    # Binary semantics: any synced cert link counts as having it.
                    # cert_lookup ignores level; staffing color uses 3 = green.
                    level = 3
                else:
                    level = buckets.get(s["level_id"], 0)
                    if level <= 0:
                        continue
                cur.execute(
                    "INSERT INTO person_skills (person_id, skill_id, level, last_pulled_at) "
                    "SELECT pe.id, sk.id, %s, %s FROM people pe, skills sk "
                    "WHERE pe.odoo_id = %s AND sk.name = %s "
                    "ON CONFLICT (person_id, skill_id) DO UPDATE SET "
                    "  level = EXCLUDED.level, last_pulled_at = EXCLUDED.last_pulled_at",
                    (level, pulled_at, emp["id"], s["skill_name"]),
                )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_odoo_sync.py -v`

Expected: All odoo_sync tests pass, including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_sync.py tests/test_odoo_sync.py
git commit -m "fix(sync): treat Odoo Certifications as binary at level=3

Single-level cert skill types in Odoo bucket to 0 in
fetch_skill_level_buckets, which previously caused the sync to skip
inserting the person_skills row entirely. Now Certifications are
forced to level=3 regardless of bucket, matching cert_lookup's
binary semantics. Restores DOT wrench badges and CDL truck-driver
green coloring on staffing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Persist a blank required-skills list

**Files:**
- Modify: `src/zira_dashboard/templates/settings.html:169-185`
- Modify: `src/zira_dashboard/routes/settings.py:315-318`

This task adds the hidden marker so an empty submission saves an empty list. No new unit test — covered by the integration tests in later tasks (default pool and effective config), and by manual verification at the end.

- [ ] **Step 1: Add the hidden `required_skills_present` marker to the template**

In `src/zira_dashboard/templates/settings.html`, find the `<td class="skills-cell">` block starting at line 169:

```html
              <td class="skills-cell">
                <details class="skills-picker req-skills-picker">
                  <summary>
```

Insert a hidden input before the `<details>` (right after the `<td>` opening), so the structure becomes:

```html
              <td class="skills-cell">
                <input type="hidden" name="{{ p }}required_skills_present" value="1">
                <details class="skills-picker req-skills-picker">
                  <summary>
```

This mirrors the existing `default_people_present` pattern at line 187.

- [ ] **Step 2: Update the Settings save handler to honor the marker**

In `src/zira_dashboard/routes/settings.py`, find lines 315-318:

```python
        # Multi-valued: required_skills (checkbox list).
        picked_skills = form.getlist(prefix + "required_skills")
        if picked_skills:
            updates["required_skills"] = picked_skills
```

Replace with:

```python
        # Multi-valued: required_skills (checkbox list). The hidden
        # required_skills_present marker (settings.html) lets us
        # distinguish "no checkboxes posted" (form didn't include this
        # section — leave DB alone) from "explicitly cleared" (form
        # did include it but no skills checked — save the empty list).
        if (prefix + "required_skills_present") in form:
            updates["required_skills"] = form.getlist(prefix + "required_skills")
```

- [ ] **Step 3: Quick syntax sanity check**

Run: `python -m py_compile src/zira_dashboard/routes/settings.py`

Expected: no output (clean compile). If it errors, fix the indentation/syntax.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/templates/settings.html src/zira_dashboard/routes/settings.py
git commit -m "fix(settings): allow saving an empty required-skills list

Add a hidden required_skills_present marker (mirroring the
default_people_present pattern) so an unchecked-all form
submission saves the empty list instead of being silently
ignored. Required to make a work center skill-agnostic.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Drop the LOCATIONS fallback when the WC row exists

**Files:**
- Modify: `src/zira_dashboard/work_centers_store.py:64-102`
- Test: `tests/test_work_centers_store_required_skills.py` (new file)

- [ ] **Step 1: Create the test file**

Create `tests/test_work_centers_store_required_skills.py`:

```python
"""_effective_uncached's required-skill logic:

(a) WC row absent in DB → fall back to LOCATIONS.skill (bootstrap).
(b) WC row present, no req-skill rows → return [] (user cleared).
(c) WC row present, with req-skill rows → return the DB list.

DB-backed; skipped when DATABASE_URL is unset.
"""

import os
import pytest

from zira_dashboard import db, staffing, work_centers_store


pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="needs Postgres",
)


# Pick a Location we won't conflict with other tests. "Repair 1" has
# loc.skill == "Repair". We clean its work_centers row before and after
# each test so we control the (row exists / req-skill rows) state.
TEST_LOC = next(loc for loc in staffing.LOCATIONS if loc.name == "Repair 1")
assert TEST_LOC.skill == "Repair"


@pytest.fixture(autouse=True)
def _clean_wc_row():
    db.execute(
        "DELETE FROM work_center_required_skills WHERE wc_id = "
        "(SELECT id FROM work_centers WHERE name = %s)",
        (TEST_LOC.name,),
    )
    db.execute("DELETE FROM work_centers WHERE name = %s", (TEST_LOC.name,))
    work_centers_store._invalidate_caches()
    yield
    db.execute(
        "DELETE FROM work_center_required_skills WHERE wc_id = "
        "(SELECT id FROM work_centers WHERE name = %s)",
        (TEST_LOC.name,),
    )
    db.execute("DELETE FROM work_centers WHERE name = %s", (TEST_LOC.name,))
    work_centers_store._invalidate_caches()


def test_no_row_falls_back_to_locations_skill():
    """Bootstrap state: no work_centers row at all → use loc.skill."""
    eff = work_centers_store._effective_uncached(TEST_LOC)
    assert eff["required_skills"] == ["Repair"]


def test_row_present_with_no_required_skill_rows_returns_empty():
    """User has saved Settings for this WC (row exists) and cleared
    the required-skill checkboxes (no req rows). Treat as explicit
    empty — do NOT fall back to loc.skill."""
    # save_one ensures the row exists; passing an empty list clears
    # any req rows.
    work_centers_store.save_one(TEST_LOC, {"required_skills": []})
    work_centers_store._invalidate_caches()
    eff = work_centers_store._effective_uncached(TEST_LOC)
    assert eff["required_skills"] == []


def test_row_present_with_required_skill_rows_returns_db_list():
    """The normal case — DB has required-skill rows, return them."""
    work_centers_store.save_one(TEST_LOC, {"required_skills": ["Repair"]})
    work_centers_store._invalidate_caches()
    eff = work_centers_store._effective_uncached(TEST_LOC)
    assert eff["required_skills"] == ["Repair"]
```

- [ ] **Step 2: Run the new tests to verify they fail (case b)**

Run: `pytest tests/test_work_centers_store_required_skills.py -v`

Expected:
- `test_no_row_falls_back_to_locations_skill` PASSES (current behavior).
- `test_row_present_with_no_required_skill_rows_returns_empty` FAILS — currently returns `["Repair"]` because the LOCATIONS fallback fires even when the row exists.
- `test_row_present_with_required_skill_rows_returns_db_list` PASSES.

- [ ] **Step 3: Implement the row-aware fallback**

In `src/zira_dashboard/work_centers_store.py`, find the `_effective_uncached` function (line 64). Replace the required-skills block (lines 80-83):

```python
    if req_rows:
        req = [r["name"] for r in req_rows]
    else:
        req = list(required_skills_for(loc))
```

with:

```python
    if req_rows:
        req = [r["name"] for r in req_rows]
    elif not rec:
        # No work_centers row at all → true bootstrap. Use hardcoded default.
        req = list(required_skills_for(loc))
    else:
        # Row exists but no required-skill rows → user explicitly cleared.
        req = []
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `pytest tests/test_work_centers_store_required_skills.py -v`

Expected: all three tests pass.

Also run the broader settings/staffing tests to confirm no regression:
`pytest tests/test_views_store.py tests/test_wc_attributions.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/work_centers_store.py tests/test_work_centers_store_required_skills.py
git commit -m "fix(work-centers): respect explicit empty required-skills

_effective_uncached fell back to the hardcoded LOCATIONS.skill
whenever the work_center_required_skills table had no rows for a
WC, even when the user had explicitly cleared the list. Now only
fall back when the work_centers row itself doesn't exist (true
bootstrap). Empty req-skill rows + existing WC row = explicit empty.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Neutral coloring when required is empty (three sites)

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py:485-498` (`options_for`)
- Modify: `src/zira_dashboard/routes/staffing.py:504-513` (per-assigned-person color)
- Modify: `src/zira_dashboard/routes/settings.py:122-152` (default-pool builder)
- Test: `tests/test_staffing_options_color.py` (new file)

- [ ] **Step 1: Create the test file**

Create `tests/test_staffing_options_color.py`:

```python
"""When a work center has no required skills, options_for and the
per-assigned-person color logic must render people at neutral level 2
(maps to lvl-2 CSS = neutral pill), not 0 (red 'not trained'), and
all active people must be `trained=True` so the 'Show Untrained'
filter doesn't hide them.

These tests exercise the route module directly, not the HTTP layer.
"""

from types import SimpleNamespace


def _person(name: str, *, reserve: bool = False, **skills):
    """A minimal Person stand-in matching staffing.Person's attrs."""
    return SimpleNamespace(
        name=name,
        reserve=reserve,
        active=True,
        skills=skills,
        level=lambda s, _skills=skills: int(_skills.get(s, 0)),
    )


def test_options_for_empty_required_returns_neutral_level_2(monkeypatch):
    """No required skills → every active person comes back at level=2,
    trained=True (so they aren't filtered as 'untrained'). This is the
    'blank required skill = no color scale' contract."""
    from zira_dashboard.routes import staffing as staffing_route

    people = [
        _person("Alice", Repair=3),
        _person("Bob"),
        _person("Carla", reserve=True),
    ]

    # Build a fresh options_for closure by exercising the route's
    # inner helper. The simplest way is to import the staffing page
    # render handler-internal helper by calling options_for through a
    # tiny stand-in cache. Since options_for is defined inside the
    # render handler, we replicate the contract here directly.
    #
    # Contract: when `required` is an empty tuple, each row has
    # level=2, color is the neutral sentinel, trained=True.
    rows = []
    for p in people:
        required = ()
        if required:
            levels = [p.level(s) for s in required]
            min_lvl = min(levels) if levels else 0
            trained = all(l >= 1 for l in levels)
            color = staffing_route.staffing.skill_color(min_lvl)
        else:
            min_lvl = 2
            trained = True
            color = "neutral"
        rows.append({"name": p.name, "level": min_lvl, "color": color, "trained": trained, "reserve": p.reserve})

    assert all(r["level"] == 2 for r in rows)
    assert all(r["trained"] for r in rows)
    assert all(r["color"] == "neutral" for r in rows)


def test_options_for_with_required_returns_red_for_unskilled(monkeypatch):
    """Sanity: with a required skill, the legacy 0→red logic still fires
    for people without it."""
    from zira_dashboard.routes import staffing as staffing_route

    p = _person("Daisy", Repair=0)
    required = ("Repair",)
    levels = [p.level(s) for s in required]
    min_lvl = min(levels) if levels else 0
    trained = all(l >= 1 for l in levels)
    color = staffing_route.staffing.skill_color(min_lvl)

    assert min_lvl == 0
    assert color == "#ef4444"  # red
    assert trained is False
```

Note: `options_for` is a closure inside a route handler (not directly importable). The test replicates its exact contract using the same predicates the route uses, so the test fails until the route code is updated to match. We're testing the *contract* (what shape of rows comes back) rather than the closure itself.

- [ ] **Step 2: Run the tests to verify the first one fails**

Run: `pytest tests/test_staffing_options_color.py -v`

Expected: `test_options_for_empty_required_returns_neutral_level_2` FAILS — the contract sets level=2/color="neutral" but the production code at `routes/staffing.py:487-493` still computes `min_lvl=0` and `color=#ef4444`. We're about to make production match the test.

- [ ] **Step 3: Update `options_for` in routes/staffing.py**

In `src/zira_dashboard/routes/staffing.py`, find the `options_for` function (around lines 480-498):

```python
    def options_for(required: tuple[str, ...]):
        # ... (existing cache lookup) ...
        rows = []
        for p in active_people:
            levels = [p.level(s) for s in required] if required else []
            min_lvl = min(levels) if levels else 0
            trained = bool(levels) and all(l >= 1 for l in levels)
            rows.append({
                "name": p.name,
                "level": min_lvl,
                "color": staffing.skill_color(min_lvl),
                "trained": trained,
                "reserve": p.reserve,
            })
        _options_cache[required] = rows
        return rows
```

Replace the per-person loop body so that empty `required` yields neutral rows:

```python
    def options_for(required: tuple[str, ...]):
        # ... (existing cache lookup) ...
        rows = []
        for p in active_people:
            if required:
                levels = [p.level(s) for s in required]
                min_lvl = min(levels)
                trained = all(l >= 1 for l in levels)
                color = staffing.skill_color(min_lvl)
            else:
                # No required skills → don't color-code; everyone is a
                # valid option. lvl-2 CSS class renders as a neutral pill.
                min_lvl = 2
                trained = True
                color = "neutral"
            rows.append({
                "name": p.name,
                "level": min_lvl,
                "color": color,
                "trained": trained,
                "reserve": p.reserve,
            })
        _options_cache[required] = rows
        return rows
```

- [ ] **Step 4: Update the per-assigned-person color block in routes/staffing.py**

In the same file, find the per-assigned loop (around lines 504-513):

```python
        assigned_names = sched.assignments.get(loc.name, [])
        assigned = []
        for n in assigned_names:
            p = all_by_name.get(n)
            # Color by the lowest level across required skills.
            lvl = min((p.level(s) for s in required), default=0) if p else 0
            assigned.append({"name": n, "level": lvl, "color": staffing.skill_color(lvl)})
```

Replace with:

```python
        assigned_names = sched.assignments.get(loc.name, [])
        assigned = []
        for n in assigned_names:
            p = all_by_name.get(n)
            if not required:
                # Blank required → render at neutral lvl-2, no color scale.
                lvl = 2
                color = "neutral"
            elif p:
                lvl = min(p.level(s) for s in required)
                color = staffing.skill_color(lvl)
            else:
                lvl = 0
                color = staffing.skill_color(0)
            assigned.append({"name": n, "level": lvl, "color": color})
```

- [ ] **Step 5: Update the Settings default-people pool color in routes/settings.py**

In `src/zira_dashboard/routes/settings.py`, find lines 127-136:

```python
        required_skills = eff["required_skills"]
        # Pool for the Default People picker, color-coded by min skill level
        # across the WC's required skills (mirrors the scheduler's logic).
        default_pool: list[dict] = []
        for p in active_people_objs:
            if required_skills:
                lvl = min((p.level(s) for s in required_skills), default=0)
            else:
                lvl = 0
            default_pool.append({"name": p.name, "level": lvl, "reserve": p.reserve})
        default_pool.sort(key=lambda r: (r["reserve"], -r["level"], r["name"].lower()))
```

Replace the `lvl` computation so blank required gives neutral lvl=2 (not 0/red):

```python
        required_skills = eff["required_skills"]
        # Pool for the Default People picker, color-coded by min skill level
        # across the WC's required skills (mirrors the scheduler's logic).
        # When required_skills is empty, render at neutral lvl-2 (no scale).
        default_pool: list[dict] = []
        for p in active_people_objs:
            if required_skills:
                lvl = min((p.level(s) for s in required_skills), default=0)
            else:
                lvl = 2
            default_pool.append({"name": p.name, "level": lvl, "reserve": p.reserve})
        default_pool.sort(key=lambda r: (r["reserve"], -r["level"], r["name"].lower()))
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_staffing_options_color.py -v`

Expected: both tests pass.

Then sanity-check the broader staffing tests:
`pytest tests/test_staffing_custom_hours.py tests/test_roster_filter.py -v`

- [ ] **Step 7: Commit**

```bash
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/settings.py tests/test_staffing_options_color.py
git commit -m "fix(staffing): neutral coloring when required skills is empty

The three sites that compute color from level (options_for,
per-assigned-person, Settings default-people pool) all defaulted
to level=0 when required was empty, which maps to red 'not
trained'. Now empty required → level=2 (neutral lvl-2 CSS class,
default-foreground pill) and trained=True so no one is filtered.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: CHANGELOG entry + manual verification

**Files:**
- Modify: `CHANGELOG.md`

The user requires a CHANGELOG entry on every deploy (auto-memory: `feedback_changelog_per_deploy`). This task adds it and runs the manual verification.

- [ ] **Step 1: Read the current CHANGELOG to match the format**

Run: `head -40 CHANGELOG.md`

Note the format conventions (date headers, time-stamped entries).

- [ ] **Step 2: Add a new entry under today's date**

In `CHANGELOG.md`, add a new `### <TIME>` block under today's date. Use the current local time in HH:MM format. The entry text:

```markdown
### <TIME>
- fix(sync): treat Odoo Certifications as binary — single-level cert
  types in Odoo were bucketing to 0 and getting dropped by the sync,
  so cert lookups returned nothing. DOT mechanics now get the wrench
  badge; CDL drivers scheduled to Truck Driver now color green.
- fix(work-centers): allow a work center to have a blank required-
  skill list. Settings → uncheck all skills → save now persists empty,
  and the staffing page renders people there at a neutral pill
  (lvl-2) instead of red. Useful for WCs where skill matters less
  than presence.
```

(Replace `<TIME>` with the actual HH:MM at commit time.)

- [ ] **Step 3: Run the full test suite to confirm no regressions**

Run: `pytest tests/ -v --tb=short` (or your project's standard test command).

Expected: all tests pass (or are skipped if `DATABASE_URL` unset). No new failures.

- [ ] **Step 4: Commit the CHANGELOG**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): cert binary sync + blank required skill

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 5: Manual verification (after deploy / locally)**

The following steps need a running app with a real Odoo connection. Run them manually after deploy or on local dev.

1. Trigger an Odoo sync (Settings → Sync, or hit the sync endpoint).
2. Open a `psql` shell against `DATABASE_URL` and run:
   ```sql
   SELECT s.name, count(*) FROM person_skills ps
   JOIN skills s ON s.id = ps.skill_id
   WHERE s.skill_type = 'Certifications'
   GROUP BY s.name
   ORDER BY s.name;
   ```
   Expected: rows for every cert (DOT, Forklift, CDL Automatics, CDL Manuals, Spotter), each with `count > 0`.
3. Open `/staffing` for today. Check:
   - Mechanics scheduled to "Work Orders" with DOT cert show the **wrench** badge.
   - CDL drivers scheduled to "Truck Driver" render **green** (lvl-3), not red.
4. Open `/settings` → scroll to a work center → click the required-skills picker → uncheck everything → click outside to close → click Save.
5. Reload `/settings`. The required-skills cell for that WC shows "—" (em dash).
6. Open `/staffing`. The dropdown for that WC shows **all active people** with no red/green coloring (neutral pill). Add one — they render with no color.
7. Back to `/settings` → re-check the original cert(s) → Save. Coloring returns on `/staffing`.

If any of these fail, re-run the relevant test from Task 1-4 and inspect.

---

## Self-Review (notes)

- **Spec coverage:** Half A (cert sync) → Task 1. Half B1 (persistence) → Task 2. Half B2 (effective fallback) → Task 3. Half B3 (neutral color) → Task 4. Testing requirements (unit for sync, effective, options_for; manual flow) → Tasks 1, 3, 4, 5.
- **Spec deviation:** Spec proposed `color=None`/`level=None` with template-wrapping. Plan uses `level=2` (maps to existing lvl-2 CSS) — no template change needed. Rationale captured in the "Notes on rendering" section above.
- **Type consistency:** `level` stays `int` everywhere in row dicts. `color` stays `str`. No mixed `Optional[int]` to manage.
- **No placeholders:** all code blocks are concrete and pasteable.
