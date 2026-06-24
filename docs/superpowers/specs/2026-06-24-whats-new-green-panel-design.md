# What's New — green card panel, top-right trigger, feedback capture

**Date:** 2026-06-24
**Status:** Approved design, pre-implementation
**Branch:** `whats-new-green-panel`

## Goal

Port the richer "What's new" experience (a card-based panel with per-entry read
state and a Send-feedback action) into GPI Plant Manager, themed entirely in the
site green (`--accent: #16a34a`) — never the gold `--warn` tone. Move its trigger
from the footer text link to a green icon button in the **top-right of the
header**, and apply a small dashboard layout fix so chart tooltips stop
overlapping the date-range controls.

This is the "full port" (option C): card panel + top-right trigger + per-entry
Mark read / Mark all read + working Send feedback + structured Features/Fixes
changelog going forward.

## Current state

- **`templates/_footer.html`** — a footer with `#changelog-open` ("What's new ↗")
  text link and a `#changelog-modal` whose body is filled by fetching `/changelog`
  HTML. Already green-accented. Included on 9 pages (see Reach below).
- **`static/footer.js`** — fetches `/changelog/latest`, sets a `has-new` dot when
  the latest entry is newer than `localStorage.changelog_seen` (a single global
  timestamp), opens the modal, loads `/changelog`, flashes deploy sections newer
  than seen, and writes `changelog_seen` on open. Also injects the Inbox/Handoff
  nav links and the four alert badges/modals (shared `makeBadgeModal` factory).
- **`static/footer.css`** — `.changelog-*` styles, already using `--accent`
  (green). Gold (`--warn`/`#a16207`) is used only for the warning badges.
- **`routes/changelog.py`** — renders `CHANGELOG.md` via a tiny markdown subset:
  `## YYYY-MM-DD` (date), `### TIME` (a deploy → `<section class="changelog-deploy"
  data-when="YYYY-MM-DDTHH:MM">`), `- bullets`, `**bold**`, `*italic*`, `` `code` ``.
  Also `/changelog/latest` (most recent date or date+time) and `/changelog.md` (raw).
- **`CHANGELOG.md`** — hand-authored. One or more bold-led prose bullets per
  `### TIME` deploy. No `feat:`/`fix:` structure, no per-entry type or title.
- **Headers** — every page hand-rolls its own `<header>` (no shared partial). Flex,
  `justify-content: space-between`. Shapes vary: most pages have `[left group][right
  group]` (2 children); `settings.html` is flat (`logo`, `nav`, `.page-actions`);
  `wc_dashboard.html` has only a left group (1 child). `footer.js` locates the nav
  via the `a[href="/settings"]` link.
- **Theme tokens** — `--accent: #16a34a`, `--accent-dim: #dcfce7` (green);
  `--warn: #a16207` / `#d97706` (gold, warnings only). Defined per-page in static CSS.
- **Date-range row** — dashboards render a "Range:" chip row (recycling
  `.rc-toolbar`/`.rc-chip`, leaderboards `.lb-...`) directly under the header. The
  per-person progress chart's hover tooltip (e.g. "Jose Ochoa — 274 / 222 expected
  (123.2%)") can overlap this row.

## Decisions (locked with user)

1. **Features/Fixes split** → *structured going forward*. New entries are authored
   with explicit Features/Fixes sections; existing prose history renders as a single
   "Highlights" block. No git-commit automation, no keyword heuristic.
2. **Send feedback** → *database table + admin page*, text-only for v1. No email.
3. **Trigger button** → *far right of the header bar*, injected once via the shared
   `footer.js` so it is identical on every footer page.
4. **Date range** → *add spacing* so chart tooltips no longer overlap the controls.

## Design

### A. Changelog data model & rendering (`routes/changelog.py`, `CHANGELOG.md`)

A **card = one deploy** (`### TIME` under a `## DATE`). The `/changelog` renderer is
extended to emit a semantic card per deploy instead of a flat blob:

```html
<article class="cl-entry" data-key="2026-06-19T07:35" data-feature="1">
  <header class="cl-entry-head">
    <span class="cl-entry-title">Shift Handoff log</span>   <!-- optional -->
    <span class="cl-entry-date">2026-06-19</span>
    <span class="cl-badge">New feature</span>               <!-- when data-feature -->
  </header>
  <div class="cl-group"><h4 class="cl-group-title">Features</h4><ul>…</ul></div>
  <div class="cl-group"><h4 class="cl-group-title">Fixes</h4><ul>…</ul></div>
  <button class="cl-markread">Mark read</button>
</article>
```

**Authoring convention (new, optional, backward-compatible):**

- `### 7:35 AM — Shift Handoff log` — text after an em dash / ` - ` becomes the
  card title. Omit it and the card simply shows the date (how all history renders).
- `#### Features` / `#### Fixes` subheadings group the bullets that follow.
- Bullets with no preceding `####` (i.e. the entire existing 245 KB history) render
  under a single **Highlights** group. No rewrite of history.
- The green **"New feature"** badge (`data-feature="1"`) appears when, and only when,
  the entry has a non-empty Features group. It is a *type* signal, independent of
  read state.

**Stable entry key** (for per-entry read state), emitted as `data-key`:

- Prefer `YYYY-MM-DDTHH:MM` (date + parsed deploy time).
- If a deploy has no parseable time, fall back to `YYYY-MM-DD#<n>` where `n` is the
  deploy's 0-based index within that date, so multiple untimed entries stay distinct.

`/changelog/latest` and `/changelog.md` are unchanged.

### B. Read state — Mark read / Mark all read (`footer.js`, per-browser)

Read state stays in `localStorage` (matches today's behavior; no server-side
per-user state in v1).

- New key **`changelog_read`** = JSON array of entry keys.
- **Migration on load**, when `changelog_read` is absent:
  - If legacy `changelog_seen` exists → seed `changelog_read` with every entry key
    whose `when` is `<=` `changelog_seen` (preserves "only newer-than-seen is new").
  - Else (brand-new browser) → seed `changelog_read` with **all** current keys, so a
    first-time visitor isn't told everything is new; only future entries light up.
- **Unread** = an entry key not in `changelog_read`.
- **Mark read** (per card) → add key, restyle the card muted, hide its button,
  recompute the trigger dot.
- **Mark all read** → add all rendered keys; update every card + the dot.
- The trigger's unread **dot** shows whenever unread count > 0.

### C. Trigger button — top-right of the header (`footer.js`, `_footer.html`, `footer.css`)

Remove the `#changelog-open` text link from `_footer.html`. `footer.js` injects a
green icon button (megaphone/announcement glyph as inline SVG) into the app header:

```
button.whatsnew-btn  (aria-label="What's new", aria-haspopup="dialog")
  └─ inline SVG icon + span.whatsnew-dot (unread indicator, hidden by default)
```

**Robust placement rule** (header = `document.querySelector('header')`; skip if none):

1. Header has **≥2** element children → append the button to `header.lastElementChild`
   (the right-side group / `.page-actions`). Far-right, beside existing right controls.
2. Header has **exactly 1** element child (`wc_dashboard`) → create a
   `<div class="whatsnew-slot">` (`margin-left:auto`), append to the header, button inside.

This yields a consistent far-right placement on every footer page while preserving
each page's existing layout (it does not disturb `space-between`). Clicking opens the
panel (same open/close/Esc/backdrop logic as today's modal).

### D. The panel (`_footer.html`, `footer.css`)

Restructure `#changelog-modal` into the card panel shown in the approved mockup, in
green:

- **Head:** title "What's new", subtitle "Recent Plant Manager changes", a green
  outlined **Send feedback** button, and **Close**.
- **Mark all read** button, right-aligned, below the head.
- **Body:** the `/changelog` cards (Section A), newest first.
- **Inline feedback form** (Section E), hidden until Send feedback is clicked.

Green only: `--accent`/`--accent-dim` fills, dark-green (`#166534`) text on light-green
pills. No gold anywhere in the panel or trigger.

### E. Send feedback (`routes/feedback.py` (new), `_schema.py`, `app.py`, admin link)

- **Schema** — new table `feedback` (added to `_schema.py`, `CREATE TABLE IF NOT
  EXISTS`):
  | column | type | notes |
  |---|---|---|
  | `id` | serial PK | |
  | `created_at` | timestamptz NOT NULL DEFAULT now() | |
  | `submitter` | text NULL | UPN/email from the auth session |
  | `page_url` | text NULL | page the panel was opened from |
  | `category` | text NULL | optional: Bug / Idea / Other |
  | `message` | text NOT NULL | the feedback body |
- **Submit** — `POST /feedback` (login-required, like all routes). JSON
  `{message, category?, page_url?}`. Trim + reject empty message (400
  `{ok:false,error}`). Capture `submitter` from the session, insert, return
  `{ok:true, id}`.
- **Admin view** — `GET /admin/feedback` (login-required), a simple table newest-first
  (`created_at`, `submitter`, `page_url`, `category`, `message`). Linked from Settings.
- **UI** — the panel's Send-feedback button toggles the inline form (category select +
  textarea + Submit/Cancel); success shows a toast and clears. `page_url` =
  `window.location.href` at open.

### F. Dashboard date-range spacing (dashboard CSS)

Add vertical breathing room above the "Range:" chip row on the dashboards that show it
(recycling, leaderboards, and any sibling using the same toolbar — verify
player_card / trophy_case / cumulative-chart pages) so chart hover tooltips clear the
controls. CSS-only (margin), no markup or layout restructure. Verify against the
per-person progress tooltip that prompted this.

## Theming

All new UI uses the green tokens exclusively: `--accent: #16a34a`, `--accent-dim:
#dcfce7`, dark-green `#166534` text on light pills. Reuse/extend the already-green
`.changelog-*` styles. New classes: `.whatsnew-btn`, `.whatsnew-dot`, `.whatsnew-slot`,
`.cl-entry`, `.cl-entry-head/-title/-date`, `.cl-badge`, `.cl-group`, `.cl-group-title`,
`.cl-markread`, `.cl-markall`, `.cl-feedback-*`.

## Reach (which pages get the trigger)

The trigger appears wherever the shared footer is included — today that's 9 pages
(`index`, `staffing`, `recycling`, `handoff`, `handoff_detail`, `exceptions`,
`settings`, `new_dept`, and `_staffing_base` consumers). It does **not** appear on
`leaderboards`, `wc_dashboard`, the kiosk/timeclock pages, or any page in **TV mode**
(`recycling`/`new_dept` already guard the footer with `{% if not tv_mode %}`).
Broadening the trigger to non-footer pages is a deliberate follow-up, not v1. The
date-range fix (Section F) applies to its dashboards regardless of footer presence.

## Files touched (anticipated)

- `templates/_footer.html` — drop the text link; rebuild the modal into the card panel + feedback form.
- `static/footer.css` — panel cards, badges, trigger button + dot, feedback form; retire `.app-footer` link styling.
- `static/footer.js` — header injection of the trigger; per-entry read state + migration; mark read / mark all; open/close; feedback submit; unread dot.
- `routes/changelog.py` — card-structured HTML per deploy: `data-key`, optional title, `#### Features`/`#### Fixes` grouping, `data-feature` flag.
- `CHANGELOG.md` — add a short format note at the top; author this change's entry in the new structured format as the first real example.
- `routes/feedback.py` (new) + `app.py` (include router) — `POST /feedback`, `GET /admin/feedback`.
- `_schema.py` — `feedback` table.
- `settings.html` — link to `/admin/feedback`.
- dashboard CSS (`recycling.css`, `leaderboards.css`, others as verified) — range-row spacing.
- `tests/` — changelog parsing/structure, feedback endpoints + validation + auth, schema presence.

## Testing strategy

- **pytest**
  - *changelog*: structured md → cards with Features/Fixes groups + `data-feature`
    badge flag; legacy prose → single Highlights group, no badge; stable `data-key`,
    including the `YYYY-MM-DD#<n>` fallback for untimed deploys.
  - *feedback*: `POST /feedback` inserts a row; empty/whitespace message → 400; route
    requires auth; `/admin/feedback` renders rows newest-first.
- **Manual (run the app)**: trigger sits top-right on `index`, `staffing`,
  `recycling`, `handoff`, `exceptions`, `settings`; panel is green; Mark read +
  Mark all read persist across reload; feedback submit appears in `/admin/feedback`;
  date-range tooltip clears the controls; `recycling?tv=1` shows no trigger.

## Out of scope (v1)

- Feedback attachments (image paste / file upload), a feedback resolve/triage
  workflow, and email delivery.
- Server-side, per-user read state (stays per-browser via `localStorage`).
- Git-commit-driven changelog generation / deploy hook.
- Refactoring the per-template headers into a shared partial.
- Broadening the trigger to non-footer pages (leaderboards, wc_dashboard, kiosk).

## Risks / notes

- **Header variety** — covered by the 2-case injection rule; verify on all 9 footer
  pages during implementation.
- **Migration flash** — seed `changelog_read` from `changelog_seen` so existing users
  don't see every entry marked unread on first load after deploy.
- **Changelog size** — the panel renders the full history (245 KB), same as today's
  modal, so no regression; client-side "show older" pagination is a possible future
  optimization, not v1.
