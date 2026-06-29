# Top-nav Inbox count: server-render so the menu is solid

**Date:** 2026-06-29
**Status:** Approved (design)

## Problem

When the user clicks a top-menu item, the Inbox count badge disappears for
~0.7s and then reappears. The user wants the top menu to "feel solid and
unchanging" — the Inbox notification should stay visible across navigation,
not flash/reload.

## Root cause

The dashboard is a **multi-page app**: every menu click is a full page reload,
so the entire top bar (`_topnav.html`) is re-created from scratch each time.

The Inbox count is **not in the server HTML**. It is injected client-side by
`static/footer.js` *after* the page loads:

- On `/exceptions` only, the page renders a bootstrap blob
  (`<script id="gpi-inbox-summary-bootstrap">`), so `startInboxSummary()`
  adopts the value instantly.
- On **every other page** there is no bootstrap, so `startInboxSummary()`
  takes the `else` branch: `setTimeout(refreshInboxSummary, 650)` — a 650ms
  delay plus a network fetch of `/api/exceptions/summary` before the count
  appears.

Sequence the user sees: navigate → new page paints with a bare "Inbox" link
(no count) → ~700ms+ later the summary fetch returns → count reappears. That
is the flash.

`build_summary()` (in `exception_inbox.py`) is cheap — it deliberately avoids
fresh Odoo calls and reads from in-process cache / the local Postgres mirror —
so it is safe to compute at render time.

## Goal

The Inbox count badge is part of the server HTML on every page, drawn the
instant the page paints (like the menu links and the active highlight already
are). No vanish-then-reappear. `footer.js` keeps it fresh in place; the only
motion is a smooth in-place number change if the count actually changed since
render — never a hide→show.

Out of scope: converting navigation to client-side/SPA (considered and
rejected as too large for this fix).

## Approach: server-render the badge into the top nav

### 1. Expose the summary to every page — `src/zira_dashboard/deps.py`

Register a Jinja global callable, mirroring how `static_v` is registered in
`app.py` (`templates.env.globals["static_v"] = _static_v`):

```python
templates.env.globals["nav_inbox_summary"] = exception_inbox.build_summary
```

Because `_topnav.html` is included on every page that has the top menu, this
makes the count available everywhere with **zero route changes**. The global
is a callable, so it is only invoked by templates that actually call it — the
kiosk/TV pages that don't include `_topnav.html` pay nothing.

Import note: `deps.py` must not create an import cycle. `exception_inbox`
imports `plant_day`, `schedule_store`, `staffing`, `inbox_keys` (leaf modules)
and lazily imports `routes.staffing` *inside* `build_summary()`, not at module
top level — so importing `exception_inbox` from `deps` is safe. Verify no cycle
at import time before finalizing.

### 2. Render the badge in `_topnav.html`

At the top of the template, compute the summary once:

```jinja
{% set nav_inbox = nav_inbox_summary() %}
```

Derive the same values `updateInboxSummaryLink()` computes in `footer.js`:

- `total = nav_inbox.total | int`
- `urgent = nav_inbox.urgent_total | int`
- `degraded = nav_inbox.source_errors is truthy (non-empty list)`

Render the Inbox link as label + count spans, mirroring the JS exactly:

- link classes: `inbox-nav-link`, plus `active` when `active_nav == 'inbox'`,
  `has-open` when `total > 0`, `has-urgent` when `urgent > 0`,
  `is-degraded` when `degraded and total <= 0`
- `<span class="inbox-nav-label">Inbox</span>`
- `<span class="inbox-nav-count">` — add the `hidden` attribute when
  `total <= 0 and not degraded`; text is `!` when `degraded and total <= 0`,
  else `99+` when `total > 99`, else `total`
- `title` attribute matching the JS: `Exception Inbox: N open[, M urgent]`
  / `Exception Inbox: all clear`, with ` (some checks could not load)`
  appended when degraded.

This is a server-side mirror of `updateInboxSummaryLink()`. Keep the two in
sync (add a short comment in each pointing at the other).

### 3. Emit the bootstrap blob on every page — `_topnav.html`

Move the bootstrap script out of `exceptions.html` and into `_topnav.html`,
fed by the same `nav_inbox` (only `total`, `urgent_total`, `source_errors` are
read by the JS):

```jinja
<script id="gpi-inbox-summary-bootstrap" type="application/json">
{{ {"total": total, "urgent_total": urgent, "source_errors": nav_inbox.source_errors} | tojson }}
</script>
```

With the bootstrap present everywhere, `startInboxSummary()` adopts the server
value instantly on load and **never** hits the 650ms-delay + initial-fetch
branch. The 60s background refresh (and visibility-change refresh) stays.

Then remove the now-duplicate bootstrap (`exceptions.html` line ~176) and the
redundant `initial_nav_summary` from both `exceptions.html` and its route
(`routes/exceptions.py`), since `_topnav.html` (included by `exceptions.html`)
now provides it. Confirm `exceptions.html` includes `_topnav.html` so the
bootstrap is still present on that page after the move.

### 4. `footer.js` — no behavior change required

`ensureInboxLink` / `ensureInboxLabel` / `ensureInboxCount` already **adopt**
existing DOM rather than recreate it, so the server-rendered spans are reused.
`readInboxSummaryBootstrap()` now finds a blob on every page, so the delayed
fetch branch is dead on normal navigation. No edit needed; verify by reading.

## Net effect

Every page arrives with the correct count already drawn. The menu is identical
and stable on every navigation. The only motion is an in-place number change
if the count changed since the page was rendered — no flash.

## Trade-offs / risks

- `build_summary()` now runs once per full-page render of any page with the
  top nav. It is cheap (local DB / in-process cache, no Odoo). If we later want
  to bound this under load, a small shared TTL cache (~10s) on `build_summary`
  would also cut the 60s-poll cost — **left out of this change** unless
  requested.
- DB connection pool: `build_summary()` runs its source queries sequentially
  within one request (not fan-out), so it adds at most one in-use connection at
  a time. Low marginal pressure, but worth a sanity check given prior pool
  exhaustion history.
- Server/JS drift: the badge logic now lives in two places (`_topnav.html` and
  `updateInboxSummaryLink` in `footer.js`). Cross-reference comments mitigate.

## Testing

- Unit/route test: a non-Inbox page (e.g. `/recycling` or `/staffing`) renders
  with the `inbox-nav-count` span and the `gpi-inbox-summary-bootstrap` blob
  present in the HTML, with classes matching a known summary (monkeypatch
  `build_summary` to a fixed dict: e.g. total=3/urgent=1 → `has-open`,
  `has-urgent`, count text `3`, not hidden; total=0 → count hidden, no
  `has-open`; degraded+0 → `is-degraded`, count text `!`).
- Regression: `/exceptions` still renders exactly one bootstrap blob (no
  duplicate id) after moving it into `_topnav.html`.
- Manual: navigate between Dashboards / Trophy Case / Inbox / Staffing /
  Settings and confirm the count never disappears.
