# GOAT Badges Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a 🐐 icon next to every employee name in the dashboard for each group they currently hold the all-time GOAT record in, with a hover tooltip showing the group.

**Architecture:** Mirrors the existing `_cert_badges.html` pattern: a TTL-cached helper `awards.goat_holders_map()` returns `{name: [group, ...]}`; a Jinja macro `goat_badges(name, holders)` renders one 🐐 per group; the helper is registered as a Jinja global so templates can call `goat_holders()` without per-route plumbing. Daily-update guarantee falls out structurally — `goat()` reads `production_daily`, which the existing nightly job and 45 s live warmer keep fresh.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, pytest. No new dependencies, no schema changes, no new endpoints.

**Spec:** `docs/superpowers/specs/2026-05-11-goat-badges-design.md`

---

## File Structure

**New files:**
- `src/zira_dashboard/templates/_goat_badges.html` — the macro file (mirror of `_cert_badges.html`)
- `tests/test_goat_holders_map.py` — unit tests for the helper
- `tests/test_goat_badges_macro.py` — macro rendering tests

**Modified files:**
- `src/zira_dashboard/awards.py` — append `goat_holders_map()` with TTL cache
- `src/zira_dashboard/app.py` — one-line Jinja global registration
- `src/zira_dashboard/templates/leaderboards.html` — 12 callsites + macro import + CSS
- `src/zira_dashboard/templates/staffing.html` — 5 callsites + macro import + CSS
- `src/zira_dashboard/templates/skills.html` — 1 callsite + macro import + CSS
- `src/zira_dashboard/templates/past_schedules.html` — 1 callsite + macro import + CSS
- `src/zira_dashboard/templates/player_card.html` — 1 new callsite (headline) + macro import + CSS
- `CHANGELOG.md` — one deploy entry at the end

**Responsibility split:** `awards.py` owns the data computation and caching. `_goat_badges.html` owns the visual. `app.py` owns the wiring. Each template owns the placement next to its existing name-rendering code.

---

## Conventions

- Tests that don't need a DB run unconditionally. The helper test stubs `awards.goat` via `monkeypatch.setattr` so it doesn't need DATABASE_URL.
- Commit messages follow repo convention: `feat(scope):`, `test(scope):`, `docs:`.
- Every step shows the exact code or command.
- Python interpreter on Dale's Windows machine: `.venv/Scripts/python.exe`. Use that, NOT plain `python`.

---

## Task 1: `awards.goat_holders_map()` helper

**Files:**
- Modify: `src/zira_dashboard/awards.py` (append at end of file)
- Test: `tests/test_goat_holders_map.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_goat_holders_map.py`:

```python
"""Tests for awards.goat_holders_map().

The helper inverts per-group goat() calls into {name: [groups...]}.
Tests stub goat() + registered_groups() + apply_overrides_single
via monkeypatch so they run without a DB.
"""
from __future__ import annotations

import time


def _stub(monkeypatch, *, groups, goat_by_group, overrides=None):
    """groups: list of group names; goat_by_group: {group: {name, ...} | None};
    overrides: optional {group: replacement_slot | None}.

    overrides=None means apply_overrides_single is a no-op passthrough.
    """
    from zira_dashboard import awards, work_centers_store

    monkeypatch.setattr(
        work_centers_store, "registered_groups", lambda: list(groups)
    )

    def _fake_goat(g):
        return goat_by_group.get(g)

    monkeypatch.setattr(awards, "goat", _fake_goat)

    def _fake_apply(slot, *, scope, group_name=None, **kw):
        if overrides is None:
            return slot
        if group_name in overrides:
            return overrides[group_name]
        return slot

    monkeypatch.setattr(awards, "apply_overrides_single", _fake_apply)
    # Bust the in-process TTL cache between tests.
    awards._GOAT_HOLDERS_CACHE.clear()


def test_empty_groups_returns_empty_map(monkeypatch):
    from zira_dashboard import awards
    _stub(monkeypatch, groups=[], goat_by_group={})
    assert awards.goat_holders_map() == {}


def test_single_group_single_goat(monkeypatch):
    from zira_dashboard import awards
    _stub(
        monkeypatch,
        groups=["Repairs"],
        goat_by_group={"Repairs": {"name": "Alice", "units": 100}},
    )
    assert awards.goat_holders_map() == {"Alice": ["Repairs"]}


def test_multi_group_same_person(monkeypatch):
    """Alice holds GOAT in both Repairs and Juniors — two badges."""
    from zira_dashboard import awards
    _stub(
        monkeypatch,
        groups=["Repairs", "Juniors"],
        goat_by_group={
            "Repairs": {"name": "Alice", "units": 100},
            "Juniors": {"name": "Alice", "units": 60},
        },
    )
    out = awards.goat_holders_map()
    assert out == {"Alice": ["Repairs", "Juniors"]}


def test_group_with_no_goat_contributes_nothing(monkeypatch):
    """goat() returns None for a group with no qualifying data."""
    from zira_dashboard import awards
    _stub(
        monkeypatch,
        groups=["Repairs", "Empty"],
        goat_by_group={
            "Repairs": {"name": "Alice", "units": 100},
            "Empty": None,
        },
    )
    assert awards.goat_holders_map() == {"Alice": ["Repairs"]}


def test_override_replaces_name(monkeypatch):
    """Manual override re-points the GOAT slot to a different person."""
    from zira_dashboard import awards
    _stub(
        monkeypatch,
        groups=["Repairs"],
        goat_by_group={"Repairs": {"name": "Alice", "units": 100}},
        overrides={"Repairs": {"name": "Bob"}},
    )
    assert awards.goat_holders_map() == {"Bob": ["Repairs"]}


def test_override_deletes_slot(monkeypatch):
    """Override action='delete' → apply_overrides_single returns None → no entry."""
    from zira_dashboard import awards
    _stub(
        monkeypatch,
        groups=["Repairs"],
        goat_by_group={"Repairs": {"name": "Alice", "units": 100}},
        overrides={"Repairs": None},
    )
    assert awards.goat_holders_map() == {}


def test_broken_group_does_not_poison_map(monkeypatch):
    """A goat() call that raises must not break the rest of the map."""
    from zira_dashboard import awards, work_centers_store

    awards._GOAT_HOLDERS_CACHE.clear()
    monkeypatch.setattr(
        work_centers_store, "registered_groups", lambda: ["Repairs", "Broken"]
    )

    def _fake_goat(g):
        if g == "Broken":
            raise RuntimeError("boom")
        return {"name": "Alice", "units": 100}

    monkeypatch.setattr(awards, "goat", _fake_goat)
    monkeypatch.setattr(
        awards, "apply_overrides_single", lambda slot, **kw: slot
    )
    out = awards.goat_holders_map()
    assert out == {"Alice": ["Repairs"]}


def test_ttl_cache_returns_same_object(monkeypatch):
    """Two calls within TTL → goat() invoked only once."""
    from zira_dashboard import awards
    calls = {"n": 0}

    def _counting_goat(g):
        calls["n"] += 1
        return {"name": "Alice", "units": 100}

    awards._GOAT_HOLDERS_CACHE.clear()
    from zira_dashboard import work_centers_store
    monkeypatch.setattr(
        work_centers_store, "registered_groups", lambda: ["Repairs"]
    )
    monkeypatch.setattr(awards, "goat", _counting_goat)
    monkeypatch.setattr(
        awards, "apply_overrides_single", lambda slot, **kw: slot
    )

    awards.goat_holders_map()
    awards.goat_holders_map()
    assert calls["n"] == 1  # second call hit the cache


def test_ttl_cache_recomputes_after_expiry(monkeypatch):
    """After TTL elapses, the cache rebuilds — goat() invoked twice."""
    from zira_dashboard import awards
    calls = {"n": 0}

    def _counting_goat(g):
        calls["n"] += 1
        return {"name": "Alice", "units": 100}

    awards._GOAT_HOLDERS_CACHE.clear()
    from zira_dashboard import work_centers_store
    monkeypatch.setattr(
        work_centers_store, "registered_groups", lambda: ["Repairs"]
    )
    monkeypatch.setattr(awards, "goat", _counting_goat)
    monkeypatch.setattr(
        awards, "apply_overrides_single", lambda slot, **kw: slot
    )

    # First call populates cache with a far-past expiry.
    awards.goat_holders_map()
    # Force expire: rewrite the cached entry to be already-expired.
    awards._GOAT_HOLDERS_CACHE["value"] = (awards._GOAT_HOLDERS_CACHE["value"][0], 0.0)

    awards.goat_holders_map()
    assert calls["n"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_goat_holders_map.py -v`
Expected: FAILs — `goat_holders_map` not defined.

- [ ] **Step 3: Implement `goat_holders_map` in `awards.py`**

Append to `src/zira_dashboard/awards.py`:

```python
# ---- GOAT holders lookup (used by the _goat_badges macro) ----------------

import time as _time

_GOAT_HOLDERS_TTL_SECONDS = 300  # 5 minutes
_GOAT_HOLDERS_CACHE: dict = {}   # {"value": (map, expires_at)}


def goat_holders_map() -> dict[str, list[str]]:
    """Return {operator_name: [group_name, ...]} for every current GOAT.

    Iterates registered groups, calls goat(group), applies overrides
    (so manual reassignments / deletes flow through), and inverts.
    Groups where the GOAT slot is empty or override-deleted contribute
    nothing.

    Cached in-process for 5 minutes. The data updates structurally:
    goat() reads production_daily, which the nightly job + 45 s live
    warmer keep fresh; a new GOAT shows up across the system within
    ~5 min of the cache expiring.

    A broken group (goat() raises) is logged and skipped — it must not
    poison the rest of the map.
    """
    from . import work_centers_store

    now = _time.time()
    cached = _GOAT_HOLDERS_CACHE.get("value")
    if cached is not None and now < cached[1]:
        return cached[0]

    out: dict[str, list[str]] = {}
    for g in work_centers_store.registered_groups():
        try:
            live = goat(g)
        except Exception:
            continue
        final = apply_overrides_single(live, scope="award_goat", group_name=g)
        if final is None:
            continue
        name = final.get("name")
        if not name:
            continue
        out.setdefault(name, []).append(g)

    _GOAT_HOLDERS_CACHE["value"] = (out, now + _GOAT_HOLDERS_TTL_SECONDS)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_goat_holders_map.py -v`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/awards.py tests/test_goat_holders_map.py
git commit -m "feat(awards): goat_holders_map() — inverted lookup for badge rendering"
```

---

## Task 2: `_goat_badges.html` macro

**Files:**
- Create: `src/zira_dashboard/templates/_goat_badges.html` (new)
- Test: `tests/test_goat_badges_macro.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/test_goat_badges_macro.py`:

```python
"""Render-tests for the _goat_badges.html Jinja macro.

We build a tiny Jinja Environment pointed at the templates directory,
import the macro, and render it with representative inputs. No FastAPI
app, no DB.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader


@pytest.fixture
def env():
    templates_dir = Path(__file__).resolve().parent.parent / "src" / "zira_dashboard" / "templates"
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )


def _render_macro(env, name, holders):
    tmpl = env.from_string(
        '{% from "_goat_badges.html" import goat_badges %}'
        '{{ goat_badges(name, holders) }}'
    )
    return tmpl.render(name=name, holders=holders).strip()


def test_no_goat_holdings_emits_nothing(env):
    out = _render_macro(env, "Alice", {})
    assert out == ""


def test_name_not_in_map_emits_nothing(env):
    out = _render_macro(env, "Alice", {"Bob": ["Repairs"]})
    assert out == ""


def test_none_holders_emits_nothing(env):
    out = _render_macro(env, "Alice", None)
    assert out == ""


def test_single_group_emits_one_badge(env):
    out = _render_macro(env, "Alice", {"Alice": ["Repairs"]})
    assert '<span class="goat-badges">' in out
    assert out.count('class="goat-badge"') == 1
    assert 'title="GOAT — Repairs"' in out
    assert "🐐" in out


def test_multi_group_emits_multiple_badges(env):
    out = _render_macro(env, "Alice", {"Alice": ["Repairs", "Juniors"]})
    assert out.count('class="goat-badge"') == 2
    assert 'title="GOAT — Repairs"' in out
    assert 'title="GOAT — Juniors"' in out
    assert out.count("🐐") == 2


def test_group_name_with_quotes_is_escaped(env):
    """Defensive: a group name with a `"` in it must not break the HTML."""
    out = _render_macro(env, "Alice", {"Alice": ['Re"pairs']})
    # Rendered title attribute must escape the quote.
    assert 'title="GOAT — Re&#34;pairs"' in out or 'title="GOAT — Re&quot;pairs"' in out


def test_css_macro_emits_class_rules(env):
    tmpl = env.from_string(
        '{% from "_goat_badges.html" import goat_badges_css %}'
        '{{ goat_badges_css() }}'
    )
    css = tmpl.render()
    assert ".goat-badges" in css
    assert ".goat-badge" in css
    assert "display: inline-flex" in css
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_goat_badges_macro.py -v`
Expected: FAILs — template not found.

- [ ] **Step 3: Create the macro file**

Create `src/zira_dashboard/templates/_goat_badges.html`:

```jinja
{# Reusable GOAT badges. One 🐐 per group an operator currently holds
   the all-time GOAT record in. Hover reveals the group.

   Usage:
     {% from "_goat_badges.html" import goat_badges, goat_badges_css %}
     ...
     <head><style>... {{ goat_badges_css() }} ...</style></head>
     ...
     <span class="name">{{ name }}</span>{{ goat_badges(name, goat_holders()) }}

   `goat_holders` is a Jinja global (registered in app.py) that returns
   the cached {name: [group, ...]} map. Templates invoke it explicitly.
#}

{% macro goat_badges_css() -%}
.goat-badges {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: 4px;
  vertical-align: middle;
  line-height: 1;
}
.goat-badge {
  font-size: 0.95em;
  cursor: help;
  user-select: none;
}
{%- endmacro %}

{% macro goat_badges(name, goat_holders) -%}
{%- set groups = (goat_holders or {}).get(name, []) -%}
{%- if groups -%}
<span class="goat-badges">
{%- for g in groups -%}
<span class="goat-badge" title="GOAT — {{ g }}">🐐</span>
{%- endfor -%}
</span>
{%- endif -%}
{%- endmacro %}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_goat_badges_macro.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/_goat_badges.html tests/test_goat_badges_macro.py
git commit -m "feat(templates): _goat_badges.html macro"
```

---

## Task 3: Register the Jinja global

**Files:**
- Modify: `src/zira_dashboard/app.py` (one line, around the existing `templates.env.globals` block)

- [ ] **Step 1: Find the existing Jinja-global block**

In `src/zira_dashboard/app.py`, locate the lines that register `cert_icon_svg`, `cert_icon_slug`, etc. on `templates.env.globals` (around line 191). They look like:

```python
templates.env.globals["cert_icon_svg"] = cert_icons.icon_for
templates.env.globals["cert_icon_slug"] = cert_icons.slug_for
templates.env.globals["cert_icon_data"] = cert_icons.all_data
```

- [ ] **Step 2: Add the goat_holders global**

After the last `cert_icon_*` line, add:

```python
from . import awards
templates.env.globals["goat_holders"] = awards.goat_holders_map
```

The import is at function-scope-equivalent (module-top, but kept next to its only consumer for clarity). `awards.goat_holders_map` is the function — Jinja evaluates `goat_holders()` per render and benefits from the TTL cache inside.

- [ ] **Step 3: Verify imports cleanly**

Run: `.venv/Scripts/python.exe -c "from zira_dashboard import app; print(callable(app.templates.env.globals['goat_holders']))"`
Expected: `True`.

- [ ] **Step 4: Commit**

```bash
git add src/zira_dashboard/app.py
git commit -m "feat(app): register goat_holders Jinja global"
```

---

## Task 4: Wire badges into leaderboards.html

**Files:**
- Modify: `src/zira_dashboard/templates/leaderboards.html`

- [ ] **Step 1: Add the macro import**

In `src/zira_dashboard/templates/leaderboards.html`, find line 2:

```jinja
{% from "_cert_badges.html" import cert_badges, cert_badges_css %}
```

Add a sibling line immediately after:

```jinja
{% from "_goat_badges.html" import goat_badges, goat_badges_css %}
```

- [ ] **Step 2: Add the CSS call**

In the same file, find the `{% block styles %}` block (around line 5):

```jinja
{% block styles %}
  {{ cert_badges_css() }}
{% endblock %}
```

Add a sibling line:

```jinja
{% block styles %}
  {{ cert_badges_css() }}
  {{ goat_badges_css() }}
{% endblock %}
```

- [ ] **Step 3: Wire 12 callsites**

In the same file, find every line that matches the pattern `{{ cert_badges(r.name, person_certs) }}` (there are 12). After each occurrence, insert `{{ goat_badges(r.name, goat_holders()) }}`. Concretely, each line transforms from:

```jinja
<td class="op">{{ r.name }}{{ cert_badges(r.name, person_certs) }} <span class="lb-name-count">({{ r.name_count }})</span></td>
```

to:

```jinja
<td class="op">{{ r.name }}{{ cert_badges(r.name, person_certs) }}{{ goat_badges(r.name, goat_holders()) }} <span class="lb-name-count">({{ r.name_count }})</span></td>
```

Use a multi-line Edit or `replace_all` so all 12 happen consistently. Note: SOME callsites wrap the name in a `<button onclick="openLbPopup(this)">{{ r.name }}</button>` — for those, the substitution is:

```jinja
onclick="openLbPopup(this)">{{ r.name }}</button>{{ cert_badges(r.name, person_certs) }}{{ goat_badges(r.name, goat_holders()) }} <span class="lb-name-count">({{ r.name_count }})</span></td>
```

The exact pattern that's unique enough to safely `replace_all`: `{{ cert_badges(r.name, person_certs) }} <span class="lb-name-count">` → `{{ cert_badges(r.name, person_certs) }}{{ goat_badges(r.name, goat_holders()) }} <span class="lb-name-count">`.

- [ ] **Step 4: Verify**

Run: `grep -c "goat_badges(r.name, goat_holders())" src/zira_dashboard/templates/leaderboards.html`
Expected: `12`.

Run: `.venv/Scripts/python.exe -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/leaderboards.html
git commit -m "feat(leaderboards): GOAT badges next to operator names"
```

---

## Task 5: Wire badges into staffing.html

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html`

- [ ] **Step 1: Add the macro import**

At line 1 of `src/zira_dashboard/templates/staffing.html`, find:

```jinja
{% from "_cert_badges.html" import cert_badges, cert_badges_css %}
```

Add a sibling line below it:

```jinja
{% from "_goat_badges.html" import goat_badges, goat_badges_css %}
```

- [ ] **Step 2: Add the CSS call**

In the same file, inside the `<style>` block (starts at line 10), find the line `{{ cert_badges_css() }}` (line 11). Add right after:

```jinja
  {{ goat_badges_css() }}
```

- [ ] **Step 3: Wire the 5 cert_badges callsites**

Find each of these distinct callsites in `staffing.html` and add the sibling `goat_badges` call immediately after the `cert_badges` call:

**Callsite A — Unassigned list (line ~121):**

Find:
```jinja
{% for n in unassigned %}<li data-name="{{ n }}"><a href="/staffing/people/{{ n|urlencode }}" class="person-card-link">{{ n }}</a>{{ cert_badges(n, person_certs) }}</li>{% endfor %}
```

Replace with:
```jinja
{% for n in unassigned %}<li data-name="{{ n }}"><a href="/staffing/people/{{ n|urlencode }}" class="person-card-link">{{ n }}</a>{{ cert_badges(n, person_certs) }}{{ goat_badges(n, goat_holders()) }}</li>{% endfor %}
```

**Callsite B — Time Off entry name (line ~139):**

Find:
```jinja
<span class="name"><a href="/staffing/people/{{ e.name|urlencode }}" class="person-card-link" onclick="event.stopPropagation()">{{ e.name }}</a>{{ cert_badges(e.name, person_certs) }}{% if is_partial %} <span class="partial-tag">partial</span>{% endif %}</span>
```

Replace with:
```jinja
<span class="name"><a href="/staffing/people/{{ e.name|urlencode }}" class="person-card-link" onclick="event.stopPropagation()">{{ e.name }}</a>{{ cert_badges(e.name, person_certs) }}{{ goat_badges(e.name, goat_holders()) }}{% if is_partial %} <span class="partial-tag">partial</span>{% endif %}</span>
```

**Callsite C — Reserves list (line ~167):**

Find:
```jinja
{% for n in reserves %}<li data-name="{{ n }}"><a href="/staffing/people/{{ n|urlencode }}" class="person-card-link">{{ n }}</a>{{ cert_badges(n, person_certs) }}</li>{% endfor %}
```

Replace with:
```jinja
{% for n in reserves %}<li data-name="{{ n }}"><a href="/staffing/people/{{ n|urlencode }}" class="person-card-link">{{ n }}</a>{{ cert_badges(n, person_certs) }}{{ goat_badges(n, goat_holders()) }}</li>{% endfor %}
```

**Callsites D + E — Dropdown items in the scheduler row (lines ~329 and ~340):**

For each of these two lines, find:
```jinja
<span class="dd-item-text">{{ p.name }}{{ cert_badges(p.name, person_certs) }}</span>
```

Replace with:
```jinja
<span class="dd-item-text">{{ p.name }}{{ cert_badges(p.name, person_certs) }}{{ goat_badges(p.name, goat_holders()) }}</span>
```

(Both occurrences. Safe to use `replace_all` on this exact 96-char string since the surrounding context is identical.)

**Callsites at lines ~312 and ~315** — these were truncated from the grep output. Read those lines and apply the same pattern: any `{{ cert_badges(<name_var>, person_certs) }}` becomes `{{ cert_badges(<name_var>, person_certs) }}{{ goat_badges(<name_var>, goat_holders()) }}`. Use the same `<name_var>` (it's whatever variable the cert_badges call passed).

- [ ] **Step 4: Verify**

Run: `grep -c "goat_badges(" src/zira_dashboard/templates/staffing.html`
Expected: `5` or more (5 wiring sites + any near-duplicates depending on how the truncated lines wired). If the count is 5-7 it's fine; if 0 or >10 something went wrong.

Run: `.venv/Scripts/python.exe -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/staffing.html
git commit -m "feat(staffing): GOAT badges in unassigned / time-off / reserves / dropdowns"
```

---

## Task 6: Wire badges into skills.html

**Files:**
- Modify: `src/zira_dashboard/templates/skills.html`

- [ ] **Step 1: Add the macro import**

At line 2 of `src/zira_dashboard/templates/skills.html`, find:

```jinja
{% from "_cert_badges.html" import cert_badges, cert_badges_css %}
```

Add a sibling line below:

```jinja
{% from "_goat_badges.html" import goat_badges, goat_badges_css %}
```

- [ ] **Step 2: Add the CSS call**

In the same file, find the `{{ cert_badges_css() }}` call inside the `<style>` block (it follows the same pattern as the other templates). Add `{{ goat_badges_css() }}` on the next line.

- [ ] **Step 3: Wire the one callsite (line ~67)**

Find:
```jinja
<a class="name-link" href="/staffing/people/{{ p.name | urlencode }}">{{ p.name }}{{ cert_badges(p.name, person_certs) }}</a>
```

Replace with:
```jinja
<a class="name-link" href="/staffing/people/{{ p.name | urlencode }}">{{ p.name }}{{ cert_badges(p.name, person_certs) }}{{ goat_badges(p.name, goat_holders()) }}</a>
```

- [ ] **Step 4: Verify**

Run: `grep -c "goat_badges(" src/zira_dashboard/templates/skills.html`
Expected: `1`.

Run: `.venv/Scripts/python.exe -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/skills.html
git commit -m "feat(skills): GOAT badges next to operator names in matrix"
```

---

## Task 7: Wire badges into past_schedules.html

**Files:**
- Modify: `src/zira_dashboard/templates/past_schedules.html`

- [ ] **Step 1: Add the macro import**

At line 2 of `src/zira_dashboard/templates/past_schedules.html`, find:

```jinja
{% from "_cert_badges.html" import cert_badges, cert_badges_css %}
```

Add a sibling line below:

```jinja
{% from "_goat_badges.html" import goat_badges, goat_badges_css %}
```

- [ ] **Step 2: Add the CSS call**

In the same file, find the `{{ cert_badges_css() }}` call inside the `<style>` block. Add `{{ goat_badges_css() }}` on the next line.

- [ ] **Step 3: Wire the one callsite (line ~247)**

Find:
```jinja
{% for p in people %}{{ p }}{{ cert_badges(p, person_certs) }}{% if not loop.last %}, {% endif %}{% endfor %}
```

Replace with:
```jinja
{% for p in people %}{{ p }}{{ cert_badges(p, person_certs) }}{{ goat_badges(p, goat_holders()) }}{% if not loop.last %}, {% endif %}{% endfor %}
```

- [ ] **Step 4: Verify**

Run: `grep -c "goat_badges(" src/zira_dashboard/templates/past_schedules.html`
Expected: `1`.

Run: `.venv/Scripts/python.exe -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/past_schedules.html
git commit -m "feat(past_schedules): GOAT badges next to per-WC operator names"
```

---

## Task 8: Wire badges into player_card.html headline

This template does NOT currently import `_cert_badges.html`, so we add both the import and the CSS for the first time.

**Files:**
- Modify: `src/zira_dashboard/templates/player_card.html`

- [ ] **Step 1: Add the macro import**

At line 1 (after `{% extends "_staffing_base.html" %}`), add:

```jinja
{% from "_goat_badges.html" import goat_badges, goat_badges_css %}
```

So the top of the file reads:

```jinja
{% extends "_staffing_base.html" %}
{% from "_goat_badges.html" import goat_badges, goat_badges_css %}
{% block title %}{{ name }}{% endblock %}
```

- [ ] **Step 2: Add the CSS call**

Inside the `{% block styles %}` block (starts at line 4), at the end (just before `{% endblock %}` on line 34), add:

```jinja
  {{ goat_badges_css() }}
```

- [ ] **Step 3: Wire the headline callsite**

Find the closing `</h2>` of the headline (the `<h2 style="margin:0">` block that wraps the picker, ends at line 49). Insert the badge call AFTER the `<script>` block (the picker's onchange handler ends at line 62, the script tag closes there). Specifically, the current structure is:

```jinja
  </h2>
  <script>
  (function () {
    ...
  })();
  </script>
  <span class="pc-skills">
```

Insert between `</script>` and `<span class="pc-skills">`:

```jinja
  </script>
  {{ goat_badges(name, goat_holders()) }}
  <span class="pc-skills">
```

This puts the 🐐 emoji to the right of the name dropdown, before the skills list.

- [ ] **Step 4: Verify**

Run: `grep -c "goat_badges(" src/zira_dashboard/templates/player_card.html`
Expected: `1`.

Run: `.venv/Scripts/python.exe -c "from zira_dashboard import app; print('OK')"`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/player_card.html
git commit -m "feat(player_card): GOAT badges next to headline name"
```

---

## Task 9: Smoke test + CHANGELOG + push

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the full test suite**

Run: `.venv/Scripts/python.exe -m pytest 2>&1 | tail -10`

Expected: all new tests pass (9 + 7 = 16 new). 11 pre-existing failures in `test_shift_config_for.py` and `test_dashboards_polish.py` remain (DATABASE_URL-related, unrelated to this work). Net: no new failures.

- [ ] **Step 2: Boot the app locally and smoke-test one page**

Run (in a separate terminal, or background):
```bash
.venv/Scripts/python.exe -m zira_dashboard
```

Then in another terminal:
```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://localhost:8000/staffing/leaderboards
```

Expected: `HTTP 200`. (If you can hit production after deploy, `https://gpiplantmanager.com/staffing/leaderboards` should also work and show the badges if any GOAT data exists.)

- [ ] **Step 3: Get current time + add CHANGELOG entry**

Run: `powershell.exe -Command "Get-Date -Format 'h:mm tt'"` to get the current time.

Open `CHANGELOG.md`. The top section is `## 2026-05-11`. Insert a new `### <HH:MM TT>` block ABOVE the most recent existing time block for today (so the new entry sits at the top, newest first):

```markdown
### <HH:MM TT>

- **🐐 GOAT badges next to every employee name** — anywhere an operator's name appears (scheduler, leaderboards, skills matrix, past schedules, player-card headline), a 🐐 emoji now sits next to it for each group they currently hold the all-time GOAT record in. Hover over the icon to see which group ("GOAT — Repairs", etc.). People who hold GOAT in multiple groups get one icon per group, stacked. Updates within ~5 min of any change (cache TTL) and recomputes from `production_daily` each render — so if anyone takes the GOAT record away on a given day, the badge moves overnight after the nightly precompute.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): GOAT badges system-wide"
```

- [ ] **Step 5: Push**

```bash
git push origin main
```

Expected: pushes ~10 commits (Tasks 1-9). Railway picks up the push and redeploys automatically. The badges appear on the next page render after the redeploy.

---

## Done

Every spec requirement maps to a task above:
- "GOAT icon next to that employee's name everywhere" → Tasks 4, 5, 6, 7, 8.
- "Show which thing it is for on hover" → `title="GOAT — {{ g }}"` in Task 2.
- "Multiple icons if GOAT in multiple areas" → loop in the macro (Task 2) + `setdefault().append(g)` in the helper (Task 1).
- "Update daily" → goat() reads production_daily (already kept fresh by the nightly job + warmer); 5-min TTL cache means changes propagate within minutes (Task 1).

No new schema, no new endpoints, no per-route code changes beyond the one Jinja global in Task 3.
