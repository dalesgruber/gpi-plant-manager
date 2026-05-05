# Settings Panels Always Open — Design

**Date:** 2026-05-05
**Status:** Approved (brainstorming → implementation planning)

## Context

The `/settings` page has a sidebar (Work Centers & Goals · Company Schedule
· Integrations) plus a content area. The sidebar already drives section
selection: clicking a sidebar link sets `?section=...` in the URL and
the template hides non-matching sections via `style="display:none"` on
the section's wrapper element.

Two of the three sections (`Company Schedule`, `Work Centers & Goals`)
also wrap their content in a `<details class="panel collapsible">`
element with a chevron and a preview line in the `<summary>`. So the
user has to click both the sidebar link AND the panel summary to see
the actual settings — even though the sidebar already implies "you
chose this section, show me the contents."

The third section (`Integrations`) is already a plain `<section class="panel">`
with an `<h2>` heading — that's the target shape.

## Goals

1. The Schedule and Work Centers settings panels are always visible
   when their sidebar link is active. No click-to-expand step.
2. Each panel's heading is a plain `<h2>` — no chevron, no preview line.
3. The sidebar continues to be the only mechanism for switching
   between sections.

## Non-goals

- Multi-page settings (one URL per section). The single-page-with-section-param
  pattern stays.
- Changes to nested `<details>` elements inside the Work Centers table
  (skill pickers, required-skills picker, default-people picker,
  dp-reserves). Those are row-level controls that need to stay
  collapsible to keep the table readable.
- The Integrations section. Already in the target shape.
- Any changes to form `data-section` attributes, save flow, or
  fetch-based form submission. The existing JS in the page footer
  works on `<form data-section="…">`, not on the panel element.
- The auto-close-other-details JS at the bottom of the file. It still
  runs against nested pickers, which remain `<details>` elements. No
  change needed.

## Design

### Template change (`templates/settings.html`)

Convert both top-level panels from `<details class="panel collapsible">`
to `<section class="panel">` with a plain `<h2>` heading.

**Before (Schedule, around line 549):**

```jinja
<details class="panel collapsible">
  <summary>
    <h2 style="display:inline;margin:0">Company Schedule</h2>
    <span class="collapsible-sub">{{ schedule.shift_start }}–{{ schedule.shift_end }} · {{ schedule.work_weekdays | length }} days · {{ schedule.breaks | length }} breaks</span>
    <span class="collapsible-chevron" aria-hidden="true">▾</span>
  </summary>

  <div class="sched-grid">
    ...content...
  </div>

</details>
```

**After:**

```jinja
<section class="panel">
  <h2>Company Schedule</h2>

  <div class="sched-grid">
    ...content...
  </div>

</section>
```

Same shape change for the Work Centers panel (around line 594) — drop
the `<details>` wrapper, drop the `<summary>` block (chevron + preview
line + h2-with-inline-style), keep the inner content, replace the
closing `</details>` with `</section>`. Leave the surrounding `<form>`
element untouched — it's the section-toggle target.

### CSS cleanup (`templates/settings.html` `<style>` block)

Delete the now-unused `.collapsible` rules. Specifically:

- Lines ~110–141: the `details.collapsible*` block (chevron rotation,
  summary border, summary padding, hidden default marker, etc.).
- Lines ~334–336: the content-padding overrides
  (`details.collapsible > :not(summary)`, `> .actions`,
  `.sched-grid` inside collapsible).

The standard `.panel { padding: 0.6rem 0.8rem }` rule on line 39
already applies to plain panels, so inner content will pick up the
panel's padding directly. If the `.actions` row or `.sched-grid` look
visually off after the change (e.g., too much vertical space at the
bottom, or the table edges crowd the panel border), tighten with
new minimal rules — but expect that the default `.panel` padding
will be close enough.

### Out of scope (verified)

- Nested `<details>` inside the Work Centers table (`details.skills-picker`,
  `details.single-picker`, `details.req-skills-picker`,
  `details.default-people-picker`, `details.dp-reserves`) — keep
  collapsing. They're per-row pickers; expanding them all would make
  the table unusable.
- The `Integrations` section — already a plain `<section class="panel">`.
- The auto-close-other-details JS — still runs against nested pickers
  (which are still `<details>`). The behavior of "open one picker,
  close the others" is preserved within the Work Centers table.

## Testing

No automated tests for this template. Verify visually on Railway:

1. Each sidebar link still toggles between sections.
2. Schedule and Work Centers panels are open by default with no
   chevron/click-to-expand affordance.
3. Nested pickers in the Work Centers table still expand and collapse
   when clicked.
4. Save/save-flash flow on Schedule and Work Centers still works
   (form submit, inline save status).
5. No layout regressions — content sits inside the panel border with
   reasonable padding, no overflow, headings have appropriate margin.

If item 5 fails, add minimal `.panel > .sched-grid` or
`.panel > .actions` padding rules to taste.

## Files touched

- `src/zira_dashboard/templates/settings.html` — replace two `<details>`
  wrappers with `<section>`, drop `.collapsible` CSS rules.
- `CHANGELOG.md` — entry for the deploy.
