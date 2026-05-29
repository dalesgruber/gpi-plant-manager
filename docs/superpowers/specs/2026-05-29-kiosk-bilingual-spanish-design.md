# Bilingual (English + Spanish) kiosk for Spanish-speaking employees

- **Date:** 2026-05-29
- **Status:** Approved (design) — pending implementation plan
- **Author:** Dale + Claude

## Goal

On the timeclock kiosk, employees who have a **Spanish** language skill in Odoo
should see Spanish text alongside English on every screen **after they pick
their name**. English-only employees see the screens exactly as they are today.

## Detection — who is a "Spanish speaker"

Source of truth is Odoo skills. In Odoo, **Spanish** is a skill under the
**Languages** skill type, rated on a 1–3 scale (1 = some, 3 = functional+).

- A person is a Spanish speaker when their **Spanish** level is **≥ 1**
  (i.e. they have it at all). No Languages skill, or Spanish at level 0 →
  not flagged.

### Sync change (required)

`odoo_client.SKILL_TYPE_NAMES` currently pulls only *Production Skills*,
*Supervisor Skills*, and *Certifications*. The **Languages** type is **not**
synced today, so Spanish levels never reach the local DB.

- Add a **targeted** pull of each active employee's Spanish (Languages) level,
  scoped so it does **not** add English/Spanish columns to the production
  skills matrix (`skills.html`). Likely a dedicated query in `odoo_client` +
  a per-person flag, separate from the `person_skills` / matrix-column path.
- Store the result as a per-person boolean: **`people.spanish_speaker`**
  (idempotent `ALTER TABLE ... ADD COLUMN`), written on every Odoo sync next
  to the existing `wage_type` write.
- Treat unknown/unsynced as **not** a Spanish speaker (English-only fallback —
  the safe default; never hides English).

## Rendering architecture

A single **English → Spanish glossary** plus a small Jinja **`t()` helper**.

- **Glossary:** one module (e.g. `kiosk_i18n.py`) mapping each English UI
  string to its Spanish translation. Single source of truth.
- **Helper:** `t("Clock Out")` registered as a Jinja global. It renders:
  - plain English when the current person is **not** a Spanish speaker, or
  - **stacked bilingual** when they are (see Display format).
- **Flag in context:** each post-selection route passes a `bilingual` flag
  (from `people.spanish_speaker`) so `t()` knows which mode to render. The
  flag is derived once per request from the same person lookup the routes
  already do (`_person_by_id`).
- **Untranslated content:** proper nouns pass through unchanged — work-center
  names ("Repair 1"), people's names, and clock times are **not** translated.
  Only UI chrome (labels, headings, buttons, instructions) goes through `t()`.

**Rejected alternatives:** duplicate Spanish templates (drift, double
maintenance); full gettext/Babel i18n (build step + tooling overkill for one
language pair on a kiosk).

## Display format — stacked

English primary, Spanish on a second line, slightly smaller and muted.

```
   Clock Out
   Salir          ← smaller, muted (e.g. ~0.7em, #64748b)
```

Implemented as a small inline markup wrapper from `t()` (English span +
Spanish span) plus a CSS class in `kiosk_base.html`. Works for headings,
instructions, and big touch buttons alike.

## Scope — screens after name selection

Bilingual rendering applies to every screen reached **after** a name is
tapped:

- `kiosk_dashboard.html` (clock in/out/transfer + time-off tile)
- `kiosk_pick_wc.html` (work-center picker)
- `kiosk_success.html` (clock-in/out/transfer confirmation)
- `kiosk_time_off_landing.html`
- `kiosk_time_off_request_details.html`
- `kiosk_time_off_calendar.html`
- `kiosk_time_off_mine.html`
- `kiosk_time_off_mine_detail.html`
- `kiosk_time_off_success.html`
- shared chrome in `kiosk_base.html` as needed

**Out of scope:** the home / name-pick screen (`kiosk_home.html`) stays
English — we don't know who the user is until they pick a name.

## Translation content

Spanish strings are authored as part of implementation — **Latin-American /
Mexican register, plain shop-floor wording** — and collected in the glossary
for review. Dale (or a bilingual employee) reviews the glossary before it goes
live. Translations live in one file so corrections are one-line edits.

## Testing

Local dev here is Python 3.9 while the app targets 3.11+ (suite can't run
locally; see memory). Verify with:

- **Unit (for CI):** the Spanish-speaker flag derivation, and `t()` rendering —
  English-only mode returns the input unchanged; bilingual mode returns
  English + Spanish stacked markup; an unknown key falls back to English (never
  blank).
- **Local:** `py_compile` + `ast`-extract/`exec` of the pure helpers (the
  pattern already used for the timezone and time-off-only fixes).
- Confirm a missing glossary entry degrades gracefully to English-only (no
  crash, no empty label).

## Rollout / dependencies

- Requires an Odoo sync after deploy so `spanish_speaker` populates (hourly
  tick or a manual refresh). Until then, everyone is treated English-only.
- No feature flag proposed (low blast radius — purely additive text for a
  subset of users). Add one if desired.

## Future (not now)

Additional languages, machine translation, or translating dynamic content
(work-center names). The glossary + `t()` design leaves room for more language
pairs later without re-architecting.
