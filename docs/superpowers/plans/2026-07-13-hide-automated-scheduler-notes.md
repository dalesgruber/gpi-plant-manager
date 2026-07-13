# Hide Automated Scheduler Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all automated per-person scheduler notes from the Staffing UI while preserving human notes, schedule-level warnings, and internal scheduler reason data.

**Architecture:** Keep `rotation_reasons` in the scheduling engine and route context for diagnostics, but remove every browser presentation path: initial Jinja rendering, client-side summary injection, client bootstrap state, rebuild-state assignment, and unused CSS. Amend the pending global minimum-coverage solver documents so future work keeps reasons internal and renders only actionable schedule-level issues.

**Tech Stack:** Python 3.12, Jinja2, vanilla JavaScript/CSS, pytest.

## Global Constraints

- Follow `docs/superpowers/specs/2026-07-13-hide-automated-scheduler-notes-design.md`.
- Do not remove `rotation_reasons`, reason codes, or reason text from Python scheduling results or route context.
- Do not render automated per-person explanations as text, badges, icons, tooltips, or hidden accessibility content.
- Keep `#rotation-warnings` and its structured unresolved-coverage “Why?” details visible and functional.
- Keep supervisor-authored `notes` and `wc_note__*` fields unchanged.
- Preserve unrelated worktree changes, especially the concurrent skills automation work and existing untracked plans.

## File Map

| File | Action | Responsibility |
|---|---|---|
| `tests/test_staffing_rotations.py` | Modify | Static regression contract for no automated per-person notes and preserved warnings/human notes |
| `src/zira_dashboard/templates/staffing.html` | Modify | Stop server rendering and bootstrapping per-assignment reason text |
| `src/zira_dashboard/static/staffing.js` | Modify | Stop injecting reasons during live summary rebuilds |
| `src/zira_dashboard/static/staffing.css` | Modify | Remove the unused automated-reason badge style |
| `docs/superpowers/specs/2026-07-13-global-minimum-coverage-solver-design.md` | Modify | Clarify that assignment reasons are diagnostic-only and never appear beside names |
| `docs/superpowers/plans/2026-07-13-global-minimum-coverage-solver.md` | Modify | Bind future solver implementation to the no-per-person-notes presentation rule |

---

### Task 1: Remove automated per-person notes and prevent their return

**Files:**

- Modify: `tests/test_staffing_rotations.py:1097-1126`
- Modify: `src/zira_dashboard/templates/staffing.html:314,451-453`
- Modify: `src/zira_dashboard/static/staffing.js:461-495,1645-1653`
- Modify: `src/zira_dashboard/static/staffing.css:719-731`
- Modify: `docs/superpowers/specs/2026-07-13-global-minimum-coverage-solver-design.md:220-252`
- Modify: `docs/superpowers/plans/2026-07-13-global-minimum-coverage-solver.md:13-28,1395-1430`

**Interfaces:**

- Consumes: existing Python `rotation_reasons: dict[str, dict[str, str]]` context and rebuild `reasons` payload; neither is changed.
- Produces: a Staffing UI with no per-person scheduler annotations; `#rotation-warnings`, `textarea[name="notes"]`, and `textarea[name^="wc_note__"]` retain their existing contracts.

- [ ] **Step 1: Replace the existing static contract with a failing no-notes regression**

In `tests/test_staffing_rotations.py`, rename
`test_staffing_has_rotation_mode_controls_and_reason_data` to
`test_staffing_has_rotation_mode_controls_without_automated_person_notes`.
Keep its existing mode, Auto-checkbox, endpoint, and rebuild-button assertions.
Replace `assert "rotation_reasons" in html` and add the complete presentation
contract below before the endpoint assertions:

```python
    css = (ROOT / "src/zira_dashboard/static/staffing.css").read_text()
    assert "rotation_reasons" not in html
    assert "ROTATION_REASONS" not in html
    assert "ROTATION_REASONS" not in js
    assert "appendReasonBadge" not in js
    assert "rotation-reason" not in html
    assert "rotation-reason" not in js
    assert "rotation-reason" not in css
    assert 'id="rotation-warnings"' in html
    assert 'name="notes"' in html
    assert 'name="wc_note__{{ row.loc.name }}"' in html
```

This test intentionally checks only browser presentation. Leave route-context
tests that assert `ctx["rotation_reasons"]` unchanged because internal reason
data remains supported.

- [ ] **Step 2: Run the regression and verify that it fails for the current badges**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes -q
```

Expected: FAIL because the template, JavaScript, and CSS still contain
`rotation_reasons`, `ROTATION_REASONS`, `appendReasonBadge`, and
`.rotation-reason`.

- [ ] **Step 3: Remove the server-rendered and bootstrapped reason UI**

In `src/zira_dashboard/templates/staffing.html`, change the assigned-person
loop so the content after the partial-hours button closes directly with the
person span. Delete this fragment in full:

```jinja2
{% set _reason = rotation_reasons.get(row.loc.name, {}).get(a.name) if rotation_reasons else none %}{% if _reason %}<span class="rotation-reason" title="{{ _reason }}">{{ _reason }}</span>{% endif %}
```

At the bottom bootstrap script, change the comment to:

```javascript
  // Rotation controls (mode buttons + schedule-level warnings).
```

Delete this line and leave `ROTATION_WARNINGS` and all other bootstrap globals
unchanged:

```javascript
  window.ROTATION_REASONS = {{ rotation_reasons|tojson }};
```

- [ ] **Step 4: Remove client-side reason injection and rebuild state**

In `src/zira_dashboard/static/staffing.js`, delete the entire
`appendReasonBadge(parentEl, loc, name)` function and its preceding comment.
Change the summary comment to:

```javascript
    // Rebuild via DOM so we can attach certification badges to each name span.
```

In `updateDdSummary(dd)`, delete:

```javascript
      appendReasonBadge(span, dd.dataset.loc, name);
```

In `applyRebuild(data)`, delete:

```javascript
      window.ROTATION_REASONS = data.reasons || {};
```

Do not alter the returned `data.reasons` API field; the browser simply ignores
it.

- [ ] **Step 5: Remove the now-unused badge style**

In `src/zira_dashboard/static/staffing.css`, delete the comment beginning
`/* "Why" annotation shown next to a generated Recycled pill.` and the complete
`.rotation-reason { ... }` rule immediately following it. Do not change
`.coverage-why` or schedule-level warning styles.

- [ ] **Step 6: Align the pending global solver design**

In `docs/superpowers/specs/2026-07-13-global-minimum-coverage-solver-design.md`,
add this paragraph immediately after the assignment-reason code table:

```markdown
Assignment reasons are diagnostic data only. The Staffing page must not render
them beside employee names as badges, notes, icons, tooltips, or hidden text.
Only actionable schedule-level warnings and unresolved-center issues appear in
the UI; their “Why?” disclosure may show candidate rejection details.
```

Keep the stable reason codes and display text because solver diagnostics and
tests still consume them.

- [ ] **Step 7: Bind the pending global solver implementation plan to the rule**

In `docs/superpowers/plans/2026-07-13-global-minimum-coverage-solver.md`, add
this item under `## Global Constraints`:

```markdown
- Follow `docs/superpowers/specs/2026-07-13-hide-automated-scheduler-notes-design.md`: keep assignment reasons internal and never render per-person automated notes, badges, icons, tooltips, or hidden text.
```

In Task 5 Step 1, extend
`test_rotation_warning_supports_structured_coverage_issues()` with:

```python
    css = STAFFING_CSS.read_text()
    assert "rotation-reason" not in html
    assert "rotation-reason" not in js
    assert "rotation-reason" not in css
    assert "ROTATION_REASONS" not in html
    assert "ROTATION_REASONS" not in js
```

This preserves structured schedule-level `coverage-why` diagnostics while
preventing the global solver work from reintroducing the removed name badges.

- [ ] **Step 8: Run the focused regression and verify it passes**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py::test_staffing_has_rotation_mode_controls_without_automated_person_notes -q
```

Expected: `1 passed`.

- [ ] **Step 9: Run the relevant Staffing suites**

Run:

```bash
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_rotations.py tests/test_staffing_static.py -q
```

Expected: all selected tests pass with no failures or warnings introduced by
this change.

- [ ] **Step 10: Verify the diff and commit only this feature**

Run:

```bash
git diff --check
git diff -- src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_rotations.py docs/superpowers/specs/2026-07-13-global-minimum-coverage-solver-design.md docs/superpowers/plans/2026-07-13-global-minimum-coverage-solver.md
git status --short
```

Confirm that unrelated changes are not staged, then commit:

```bash
git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_rotations.py docs/superpowers/specs/2026-07-13-global-minimum-coverage-solver-design.md docs/superpowers/plans/2026-07-13-global-minimum-coverage-solver.md
git commit -m "fix: hide automated scheduler notes"
```
