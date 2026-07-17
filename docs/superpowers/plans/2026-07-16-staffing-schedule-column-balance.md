# Staffing Schedule Column Balance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the staffing scheduler table denser around each work-center's crew label, give the reclaimed width to Scheduled and Notes at an 80/20 split, and show assigned operators as first name plus last initial.

**Architecture:** Keep assignment data, checkbox values, and picker labels as full names. Add a template-local macro used only by the Scheduled summary to form the compact display label, and adjust the existing table column CSS so the station column no longer consumes flexible blank space while the two receiving columns get explicit relative widths.

**Tech Stack:** FastAPI, Jinja2 templates, CSS, pytest static-template regression tests.

## Global Constraints

- This is presentation-only: do not change assignment, autosave, or scheduling behavior.
- Full employee names remain the submitted `<input>` values and the picker labels.
- Scheduled summary labels are `First L.`; a single-word name stays unchanged.
- Recover station-column space and distribute it 80% to Scheduled and 20% to Notes.

---

## File structure

- Modify `src/zira_dashboard/templates/staffing.html`: define the display-label macro and use it exclusively in the Scheduled summary.
- Modify `src/zira_dashboard/static/staffing.css`: reduce the station column's fixed minimum and assign the agreed Scheduled/Notes width ratio.
- Modify `tests/test_staffing_static.py`: lock down the template macro, full-name form values, and CSS layout contract.

### Task 1: Lock down the presentation contract

**Files:**
- Modify: `tests/test_staffing_static.py`

**Interfaces:**
- Consumes: `staffing.html` and `staffing.css` source text through the existing `_template()` and `_style()` helpers.
- Produces: static regression coverage for compact Scheduled labels and the 80/20 recovered-width allocation.

- [ ] **Step 1: Write the failing test**

```python
def test_staffing_schedule_uses_compact_assigned_labels_and_balanced_columns():
    html = _template()
    css = _style()

    assert "{% macro scheduled_operator_name(name) %}" in html
    assert "{{ scheduled_operator_name(a.name) }}" in html
    assert 'value="{{ p.name }}"' in html
    assert "table.sched tbody td.station { min-width: 13rem; }" in css
    assert "table.sched thead th.sched-col    { width: 40%; }" in css
    assert "table.sched thead th.wc-note-col  { width: 23%; }" in css
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_staffing_static.py::test_staffing_schedule_uses_compact_assigned_labels_and_balanced_columns -v`

Expected: FAIL because the macro and updated CSS widths do not yet exist.

- [ ] **Step 3: Implement the minimal template and CSS changes**

Add the following macro immediately before the schedule table in `src/zira_dashboard/templates/staffing.html`:

```jinja2
{% macro scheduled_operator_name(name) -%}
  {%- set parts = name.split() -%}
  {{- parts[0] if parts|length == 1 else parts[0] ~ ' ' ~ parts[-1][0] ~ '.' -}}
{%- endmacro %}
```

In the Scheduled summary, replace only the displayed `{{ a.name }}` with:

```jinja2
{{ scheduled_operator_name(a.name) }}
```

Keep every `a.name` used for badges, titles, ARIA labels, data values, and input values unchanged. In `src/zira_dashboard/static/staffing.css`, change the table sizing rules to:

```css
table.sched tbody td.station { min-width: 13rem; }
table.sched thead th.sched-col    { width: 40%; }
table.sched thead th.wc-note-col  { width: 23%; }
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `pytest tests/test_staffing_static.py::test_staffing_schedule_uses_compact_assigned_labels_and_balanced_columns -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.css tests/test_staffing_static.py
git commit -m "feat: compact staffing schedule labels"
```

### Task 2: Verify existing staffing behavior remains intact

**Files:**
- Test: `tests/test_staffing_static.py`

**Interfaces:**
- Consumes: the compact-label macro and updated table CSS from Task 1.
- Produces: a verified, non-mutating UI-only change.

- [ ] **Step 1: Run the staffing static regression suite**

Run: `pytest tests/test_staffing_static.py -v`

Expected: PASS, including existing accessibility, autosave, and work-center state checks.

- [ ] **Step 2: Inspect the final diff for accidental data-flow changes**

Run: `git diff --check HEAD~1..HEAD && git show --stat --oneline HEAD`

Expected: no whitespace errors and changes limited to the template, stylesheet, and static regression test.

- [ ] **Step 3: Verify the rendered-label rule manually**

Confirm by rendering or inspecting the template that `Jose Ochoa` displays as `Jose O.`, `Carlos Jimenez` displays as `Carlos J.`, and a one-word name remains unchanged, while the corresponding checkbox retains `value="{{ p.name }}"`.
