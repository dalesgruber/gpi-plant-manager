# Nonstandard Hours Badge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the compact blue Custom hours badge for every schedule outside the normal Monday–Friday default.

**Architecture:** Reuse the route's existing `nonstandard_schedule` context boolean. The hours-pill template condition changes from the narrower `hours_source == 'custom'` check to that shared boolean, keeping one definition of nonstandard scheduling across the badge and work-center rows.

**Tech Stack:** FastAPI, Jinja, CSS, pytest.

## Global Constraints

- Saturdays, Sundays, and special-hours days use the compact blue `CUSTOM` hours badge.
- Only standard Monday–Friday schedules use the longer default Hours badge.
- Preserve the existing hours-editor behavior and hours value formatting.
- Add a short, plain-language What’s New entry for the completed change.

---

### Task 1: Share the nonstandard schedule rule with the hours badge

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html:133-144`
- Test: `tests/test_staffing_static.py:145-151`

**Interfaces:**
- Consumes: Existing Jinja boolean `nonstandard_schedule` and `eff_custom_hours_label`.
- Produces: A compact custom-style `#hours-pill` for all nonstandard schedules.

- [ ] **Step 1: Write the failing static test**

```python
def test_nonstandard_hours_badge_uses_compact_custom_copy():
    html = _template()

    assert "{% if nonstandard_schedule %}" in html
    assert '<span class="label">Custom</span>' in html
    assert '<span>{{ eff_custom_hours_label }}</span>' in html
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv/bin/pytest -q tests/test_staffing_static.py -k nonstandard_hours_badge`

Expected: FAIL because the pill uses `hours_source == 'custom'`.

- [ ] **Step 3: Implement the shared condition**

```jinja2
<button class="hours-pill {% if nonstandard_schedule %}custom{% elif hours_source == 'saturday_default' %}saturday-default{% endif %}">
  {% if nonstandard_schedule %}
    <span class="label">Custom</span><span>{{ eff_custom_hours_label }}</span>
  {% else %}
    <span class="label">Hours</span><span>{{ eff_hours_label }}</span>
  {% endif %}
</button>
```

Keep the existing break and Saturday-default detail only in the normal branch.

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest -q tests/test_staffing_static.py tests/test_staffing_rotations.py -k staffing`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/staffing.html tests/test_staffing_static.py
git commit -m "feat: compact nonstandard hours badge"
```

### Task 2: Record the user-facing update

**Files:**
- Modify: `CHANGELOG.md:1`

**Interfaces:**
- Consumes: Completed compact nonstandard-hours badge.
- Produces: A short 2026-07-23 Features entry for people using the app.

- [ ] **Step 1: Replace the planning note with the completed feature note**

```markdown
- **Weekend and special-hour time bubbles are shorter.** Saturdays, Sundays, and days with special hours now show a small blue Custom bubble. Normal Monday–Friday schedules keep the full Hours bubble.
```

- [ ] **Step 2: Verify the note**

Run: `rg -n "Weekend and special-hour time bubbles" CHANGELOG.md`

Expected: one matching entry near the top of the file.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: note compact nonstandard hours badge"
```

## Self-review

- Spec coverage: Task 1 applies the approved shared rule and preserves normal-day display. Task 2 adds the required simple What's New copy.
- Placeholder scan: no placeholders or deferred work.
- Type consistency: both template branches use existing route context values.
