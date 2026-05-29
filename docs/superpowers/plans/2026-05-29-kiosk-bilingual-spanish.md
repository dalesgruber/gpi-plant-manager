# Bilingual English+Spanish Kiosk — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Employees who have an Odoo "Spanish" (Languages) skill at level ≥ 1 see Spanish stacked under English on every kiosk screen after they pick their name; everyone else sees today's English-only screens unchanged.

**Architecture:** A per-person `spanish_speaker` boolean is synced from Odoo into the `people` table. A single English→Spanish glossary (`kiosk_i18n.py`) plus a Jinja `t()` global render text bilingually only when the request's person is a Spanish speaker. Post-selection routes pass a `bilingual` flag into their template context; templates wrap user-facing strings with `{{ t("...") }}`.

**Tech Stack:** FastAPI, Jinja2 (`fastapi.templating.Jinja2Templates`), psycopg2/Postgres, Odoo XML-RPC, markupsafe.

**Environment note:** Local Python is 3.9 (project needs 3.11+), so the full suite can't run locally. Verify with `py_compile` and `ast`-extract/`exec` of pure helpers (the pattern used for prior fixes); route/template integration is verified in CI / on deploy. Deploy = push to `main` → Railway auto-build. Spanish levels populate on the next Odoo sync after deploy.

**Detection rule:** Spanish (Languages) skill `level_progress > 0` ⇒ `spanish_speaker = TRUE`. Unknown/unsynced ⇒ FALSE (English-only fallback; never hides English).

**Display format:** stacked — English on top, Spanish beneath, ~0.7em and muted.

---

## File Structure

- **Create** `src/zira_dashboard/kiosk_i18n.py` — the glossary + `t()` helper + `make_t`/registration entry point. One responsibility: kiosk translation.
- **Create** `tests/test_kiosk_i18n.py` — unit tests for `t()`.
- **Create** `tests/test_fetch_spanish_speakers.py` — unit tests for the Odoo fetch.
- **Modify** `src/zira_dashboard/db.py` — add `spanish_speaker` column (idempotent ALTER).
- **Modify** `src/zira_dashboard/odoo_client.py` — add `fetch_spanish_speaker_ids()`.
- **Modify** `src/zira_dashboard/odoo_sync.py` — write `spanish_speaker` during sync.
- **Modify** `src/zira_dashboard/routes/kiosk.py` — `_person_by_id` selects `spanish_speaker`; post-selection routes pass `bilingual`.
- **Modify** `src/zira_dashboard/routes/kiosk_time_off.py` — pass `bilingual` in its route contexts.
- **Modify** `src/zira_dashboard/deps.py` — register `t` as a Jinja global.
- **Modify** kiosk templates — wrap strings with `{{ t("...") }}`, add stacked CSS to `kiosk_base.html`.

---

## Task 1: Schema — `people.spanish_speaker` column

**Files:**
- Modify: `src/zira_dashboard/db.py` (the `people` ALTERs, alongside `wage_type` ~line 174)

- [ ] **Step 1: Add the column to bootstrap_schema**

Find the existing line:
```python
ALTER TABLE people ADD COLUMN IF NOT EXISTS wage_type TEXT;
```
Add immediately after it:
```python
ALTER TABLE people ADD COLUMN IF NOT EXISTS spanish_speaker BOOLEAN NOT NULL DEFAULT FALSE;
```

- [ ] **Step 2: Syntax check**

Run: `python3 -m py_compile src/zira_dashboard/db.py`
Expected: no output (success).

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/db.py
git commit -m "feat(kiosk): add people.spanish_speaker column"
```

---

## Task 2: Odoo client — `fetch_spanish_speaker_ids()`

**Files:**
- Modify: `src/zira_dashboard/odoo_client.py` (add after `fetch_skills_for`, ~line 220)
- Test: `tests/test_fetch_spanish_speakers.py`

- [ ] **Step 1: Write the failing test**

```python
"""fetch_spanish_speaker_ids returns Odoo employee ids with a non-zero
Spanish (Languages) skill level."""
from __future__ import annotations

from unittest import mock

from zira_dashboard import odoo_client


def _fake_execute(model, method, *args, **kwargs):
    if model == "hr.skill":
        # name ilike 'Spanish' -> one matching skill
        return [{"id": 7, "name": "Spanish"}]
    if model == "hr.employee.skill":
        # domain filtered to skill_id in [7] AND level_progress > 0
        return [
            {"employee_id": [11, "Ana"]},
            {"employee_id": [12, "Beto"]},
            {"employee_id": 13},  # already-unwrapped id form
        ]
    raise AssertionError(f"unexpected call {model}.{method}")


def test_returns_employee_ids_with_spanish():
    with mock.patch.object(odoo_client, "execute", side_effect=_fake_execute):
        assert odoo_client.fetch_spanish_speaker_ids() == {11, 12, 13}


def test_no_spanish_skill_returns_empty_set():
    def no_skill(model, method, *args, **kwargs):
        if model == "hr.skill":
            return []
        raise AssertionError("should not query employee skills when no Spanish skill")
    with mock.patch.object(odoo_client, "execute", side_effect=no_skill):
        assert odoo_client.fetch_spanish_speaker_ids() == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetch_spanish_speakers.py -v`
Expected: FAIL — `AttributeError: module 'zira_dashboard.odoo_client' has no attribute 'fetch_spanish_speaker_ids'`.

- [ ] **Step 3: Implement**

Add to `odoo_client.py` after `fetch_skills_for`:
```python
def fetch_spanish_speaker_ids() -> set[int]:
    """Odoo employee ids who have a 'Spanish' skill (Languages type) at a
    non-zero level — i.e. level 1-3 in Odoo's Languages rating.

    Matches the skill by name (ilike 'Spanish') so it works regardless of
    skill-type wiring, and filters on hr.employee.skill.level_progress > 0
    so a level-0 / unrated entry doesn't count. Used to flag bilingual
    kiosk users; deliberately separate from fetch_skills_for so it never
    adds Languages columns to the production skills matrix.
    """
    skills = execute(
        "hr.skill", "search_read",
        [("name", "ilike", "Spanish")],
        fields=["id", "name"],
    )
    skill_ids = [s["id"] for s in skills]
    if not skill_ids:
        return set()
    rows = execute(
        "hr.employee.skill", "search_read",
        [("skill_id", "in", skill_ids), ("level_progress", ">", 0)],
        fields=["employee_id"],
    )
    out: set[int] = set()
    for r in rows:
        eid = r["employee_id"]
        out.add(eid[0] if isinstance(eid, list) else eid)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetch_spanish_speakers.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_client.py tests/test_fetch_spanish_speakers.py
git commit -m "feat(kiosk): fetch Spanish-speaker employee ids from Odoo"
```

---

## Task 3: Sync the flag + expose it on the person lookup

**Files:**
- Modify: `src/zira_dashboard/odoo_sync.py` (fetch block ~line 76; employee upsert ~line 110-123)
- Modify: `src/zira_dashboard/routes/kiosk.py` (`_person_by_id` ~line 137-143)

- [ ] **Step 1: Fetch speaker ids in the sync try-block**

In `odoo_sync.py`, find:
```python
        employees = odoo_client.fetch_employees()
        emp_ids = [e["id"] for e in employees]
        emp_skills = odoo_client.fetch_skills_for(emp_ids)
```
Add after the `emp_skills` line:
```python
        spanish_ids = odoo_client.fetch_spanish_speaker_ids()
```

- [ ] **Step 2: Write spanish_speaker in the employee upsert**

In `odoo_sync.py`, find the employee upsert:
```python
            wage_type = emp.get("wage_type") or None
            cur.execute(
                "INSERT INTO people (odoo_id, name, active, wage_type, last_pulled_at) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
                "active = EXCLUDED.active, wage_type = EXCLUDED.wage_type, "
                "last_pulled_at = EXCLUDED.last_pulled_at",
                (emp["id"], _short_name(emp["name"]), bool(emp.get("active", True)), wage_type, pulled_at),
            )
```
Replace with:
```python
            wage_type = emp.get("wage_type") or None
            spanish_speaker = emp["id"] in spanish_ids
            cur.execute(
                "INSERT INTO people (odoo_id, name, active, wage_type, spanish_speaker, last_pulled_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
                "active = EXCLUDED.active, wage_type = EXCLUDED.wage_type, "
                "spanish_speaker = EXCLUDED.spanish_speaker, "
                "last_pulled_at = EXCLUDED.last_pulled_at",
                (emp["id"], _short_name(emp["name"]), bool(emp.get("active", True)),
                 wage_type, spanish_speaker, pulled_at),
            )
```

- [ ] **Step 3: Add spanish_speaker to `_person_by_id`**

In `kiosk.py`, find:
```python
        "SELECT id, name, odoo_id, wage_type FROM people "
        "WHERE id = %s AND active = TRUE",
```
Replace with:
```python
        "SELECT id, name, odoo_id, wage_type, spanish_speaker FROM people "
        "WHERE id = %s AND active = TRUE",
```

- [ ] **Step 4: Syntax check**

Run: `python3 -m py_compile src/zira_dashboard/odoo_sync.py src/zira_dashboard/routes/kiosk.py`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/odoo_sync.py src/zira_dashboard/routes/kiosk.py
git commit -m "feat(kiosk): sync spanish_speaker flag and expose it on person lookup"
```

---

## Task 4: i18n module — glossary + `t()` helper

**Files:**
- Create: `src/zira_dashboard/kiosk_i18n.py`
- Test: `tests/test_kiosk_i18n.py`

- [ ] **Step 1: Write the failing test**

```python
"""Kiosk t() helper: English-only passthrough, bilingual stacked markup,
format substitution, and graceful fallback for unknown strings."""
from __future__ import annotations

from markupsafe import Markup

from zira_dashboard import kiosk_i18n


class _Ctx(dict):
    """Stand-in for a Jinja context object (supports .get)."""


def _render(text, bilingual, **kw):
    return kiosk_i18n.t(_Ctx(bilingual=bilingual), text, **kw)


def test_english_only_passthrough():
    assert _render("Clock Out", False) == "Clock Out"


def test_bilingual_stacks_english_then_spanish():
    out = str(_render("Clock Out", True))
    assert '<span class="k-en">Clock Out</span>' in out
    assert '<span class="k-es">Marcar salida</span>' in out
    assert out.index("k-en") < out.index("k-es")


def test_unknown_string_falls_back_to_english():
    assert _render("Totally unknown label", True) == "Totally unknown label"


def test_format_substitution_both_languages():
    out = str(_render("Since {time}", True, time="2:30 PM"))
    assert "Since 2:30 PM" in out
    assert "Desde 2:30 PM" in out


def test_substituted_value_is_escaped():
    out = str(_render("Since {time}", True, time="<x>"))
    assert "<x>" not in out
    assert "&lt;x&gt;" in out


def test_every_translation_value_is_nonempty():
    assert all(v.strip() for v in kiosk_i18n.TRANSLATIONS.values())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_kiosk_i18n.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'zira_dashboard.kiosk_i18n'`.

- [ ] **Step 3: Implement the module**

Create `src/zira_dashboard/kiosk_i18n.py`:
```python
"""Kiosk English→Spanish translation for bilingual employees.

Employees with an Odoo Spanish (Languages) skill see Spanish stacked under
English on every screen after they pick their name. One glossary, one
helper. `t()` is registered as a Jinja global in deps.py; templates call
`{{ t("Clock Out") }}` (optionally with format kwargs, e.g.
`{{ t("Since {time}", time=check_in_display) }}`).

Register format: when the render context flag `bilingual` is False (the
default), `t()` returns plain English; when True, it returns stacked
`<span class="k-en">…</span><span class="k-es">…</span>` markup. An
unknown English string falls back to English (never blank), so a missing
glossary entry degrades gracefully.

Latin-American / Mexican shop-floor register. Edit a value here to fix any
wording — one line, one place.
"""
from __future__ import annotations

from jinja2 import pass_context
from markupsafe import Markup, escape

# English UI string -> Spanish. Keys must match the template literals exactly.
TRANSLATIONS: dict[str, str] = {
    # --- navigation ---
    "‹ Back": "‹ Atrás",
    "‹ Done": "‹ Listo",
    "Done": "Listo",
    # --- dashboard ---
    "You're clocked in": "Estás trabajando",
    "You're clocked in on": "Estás trabajando en",
    "Since {time}": "Desde {time}",
    "Clock Out": "Marcar salida",
    "Transfer": "Transferir",
    "Today you're scheduled on": "Hoy estás programado en",
    "Confirm — Clock In": "Confirmar — Marcar entrada",
    "I'm somewhere else": "Estoy en otro lugar",
    "You're not scheduled today": "No estás programado hoy",
    "Pick the work center you're on.": "Elige la estación donde estás trabajando.",
    "Pick Work Center": "Elegir estación",
    "Time Off Request": "Solicitar tiempo libre",
    # --- pick work center ---
    "Pick where you're working": "Elige dónde estás trabajando",
    "Transfer to…": "Transferir a…",
    # --- punch success ---
    "Returning home…": "Regresando al inicio…",
    # --- time off: landing ---
    "Time Off — {name}": "Tiempo libre — {name}",
    "Request Time Off": "Solicitar tiempo libre",
    "Full Day(s) Off": "Día(s) completo(s)",
    "Out for one or more whole days": "Ausente uno o más días completos",
    "Arriving Late": "Llegada tarde",
    "Tell us what time you'll arrive": "Dinos a qué hora llegarás",
    "Out for Part of the Day": "Ausente parte del día",
    "Leave + return on the same day": "Salir y regresar el mismo día",
    "Leaving Early": "Salida temprano",
    "Tell us what time you'll leave": "Dinos a qué hora te irás",
    "My Requests": "Mis solicitudes",
    "Who's Out": "Quién está ausente",
    # --- time off: request details ---
    "Mid-Day Gap": "Ausencia a media jornada",
    "Editing existing request": "Editando solicitud existente",
    "Type": "Tipo",
    "Type:": "Tipo:",
    "· Unpaid": "· Sin goce",
    "Available": "Disponible",
    "This request": "Esta solicitud",
    "Remaining after": "Restante después",
    "Start date": "Fecha de inicio",
    "End date": "Fecha de fin",
    "Date": "Fecha",
    "I'll arrive at": "Llegaré a las",
    "I'll leave at": "Saldré a las",
    "Gone from": "Ausente desde",
    "To": "Hasta",
    "Note (optional)": "Nota (opcional)",
    "Save Changes": "Guardar cambios",
    "Submit Request": "Enviar solicitud",
    # --- time off: calendar (Who's Out) ---
    "Who's Out — {heading}": "Quién está ausente — {heading}",
    "Mon": "Lun",
    "Tue": "Mar",
    "Wed": "Mié",
    "Thu": "Jue",
    "Fri": "Vie",
    "Sat": "Sáb",
    "Sun": "Dom",
    "more": "más",
    # --- time off: my requests + detail ---
    "My Requests — {name}": "Mis solicitudes — {name}",
    "Time Off": "Tiempo libre",
    "No requests yet.": "Aún no hay solicitudes.",
    "Request Details": "Detalles de la solicitud",
    "Dates": "Fechas",
    "Hours": "Horas",
    "Shape": "Modalidad",
    "Note": "Nota",
    "Sync error": "Error de sincronización",
    "Edit Request": "Editar solicitud",
    "Canceling an approved request will need approval again if you change your mind.":
        "Cancelar una solicitud aprobada requerirá aprobación nuevamente si cambias de opinión.",
    "Cancel This Request": "Cancelar esta solicitud",
    # --- time off: submitted ---
    "Request Submitted": "Solicitud enviada",
    "Your time-off request from {start} to {end} is pending approval.":
        "Tu solicitud de tiempo libre del {start} al {end} está pendiente de aprobación.",
}


def _fill(template: str, kwargs: dict) -> Markup:
    """Escape the template text and any substituted values, then format."""
    safe = escape(template)
    if not kwargs:
        return safe
    return safe.format(**{k: escape(v) for k, v in kwargs.items()})


@pass_context
def t(ctx, text: str, **kwargs) -> str | Markup:
    """Translate a UI string for the current render. English-only unless the
    context flag `bilingual` is True; then English + Spanish stacked. Unknown
    strings fall back to English."""
    english = _fill(text, kwargs)
    if not ctx.get("bilingual"):
        return english
    spanish_tmpl = TRANSLATIONS.get(text)
    if not spanish_tmpl:
        return english  # graceful fallback — never blank
    spanish = _fill(spanish_tmpl, kwargs)
    return Markup('<span class="k-en">{}</span><span class="k-es">{}</span>').format(
        english, spanish
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_kiosk_i18n.py -v`
Expected: PASS (all six tests).

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/kiosk_i18n.py tests/test_kiosk_i18n.py
git commit -m "feat(kiosk): English->Spanish glossary + t() Jinja helper"
```

---

## Task 5: Register `t` as a Jinja global

**Files:**
- Modify: `src/zira_dashboard/deps.py` (after `templates = Jinja2Templates(...)` ~line 34)

- [ ] **Step 1: Register the global**

After the line:
```python
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
```
Add:
```python
# Kiosk bilingual helper: templates call {{ t("...") }}; renders English-only
# unless the render context sets bilingual=True. See kiosk_i18n.
from . import kiosk_i18n  # noqa: E402
templates.env.globals["t"] = kiosk_i18n.t
```

- [ ] **Step 2: Syntax check**

Run: `python3 -m py_compile src/zira_dashboard/deps.py`
Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/deps.py
git commit -m "feat(kiosk): register t() translation helper as a Jinja global"
```

---

## Task 6: Pass `bilingual` into post-selection route contexts

`t()` reads `bilingual` from the render context, so every post-selection route must put it there. Default-false is safe; we set it from `_person_by_id`'s `spanish_speaker`.

**Files:**
- Modify: `src/zira_dashboard/routes/kiosk.py` (routes: `kiosk_dashboard`, `kiosk_pick_wc`, `kiosk_clock_in`, `kiosk_clock_out`, `kiosk_transfer`)
- Modify: `src/zira_dashboard/routes/kiosk_time_off.py` (every route that returns a `TemplateResponse`)

- [ ] **Step 1: kiosk.py — add `bilingual` to each TemplateResponse context**

In each of these `templates.TemplateResponse(...)` context dicts in `kiosk.py`, add a `"bilingual"` entry. For routes where the person dict is `p`, use `bool(p.get("spanish_speaker"))`.

`kiosk_dashboard` context — add:
```python
            "bilingual": bool(p.get("spanish_speaker")),
```
`kiosk_pick_wc` context — add the same line.
`kiosk_clock_in`, `kiosk_clock_out`, `kiosk_transfer` (each renders `kiosk_success.html` with `"person": p`) — add the same line to each context dict.

- [ ] **Step 2: kiosk_time_off.py — add `bilingual` to each TemplateResponse context**

Every route in `kiosk_time_off.py` already loads `p = _person_by_id(person_id)` and renders a template. Add to each `TemplateResponse` context dict:
```python
            "bilingual": bool(p.get("spanish_speaker")),
```
Routes to update (search for `TemplateResponse(` in the file): `time_off_landing`, `request_details` (the details GET), `time_off_calendar`, `time_off_mine`, `time_off_mine_detail`, the submit-success render, and any other `TemplateResponse` in the file. (The `_is_time_off_only` landing already has `p`; reuse it.)

- [ ] **Step 3: Syntax check**

Run: `python3 -m py_compile src/zira_dashboard/routes/kiosk.py src/zira_dashboard/routes/kiosk_time_off.py`
Expected: no output.

- [ ] **Step 4: Grep to confirm every kiosk TemplateResponse sets bilingual**

Run:
```bash
grep -n "TemplateResponse" src/zira_dashboard/routes/kiosk.py src/zira_dashboard/routes/kiosk_time_off.py
grep -n "\"bilingual\"" src/zira_dashboard/routes/kiosk.py src/zira_dashboard/routes/kiosk_time_off.py
```
Expected: every kiosk `TemplateResponse` for a post-selection screen has a matching `"bilingual"` entry. (The home screen `kiosk_home` does NOT get one.)

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/routes/kiosk.py src/zira_dashboard/routes/kiosk_time_off.py
git commit -m "feat(kiosk): pass bilingual flag into post-selection template contexts"
```

---

## Task 7: Stacked CSS in `kiosk_base.html`

**Files:**
- Modify: `src/zira_dashboard/templates/kiosk_base.html` (inside the existing `<style>` block)

- [ ] **Step 1: Add the stacked-text styles**

Inside the `<style>` block in `kiosk_base.html`, add:
```css
/* Bilingual stacked text: English on top, Spanish beneath (muted). */
.k-en { display: block; }
.k-es {
  display: block;
  font-size: 0.7em;
  font-weight: 400;
  font-style: italic;
  color: #64748b;
  line-height: 1.15;
}
```

- [ ] **Step 2: Syntax sanity (template renders)**

This is CSS only; verified visually after deploy. No code to compile.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/kiosk_base.html
git commit -m "feat(kiosk): stacked bilingual text styles"
```

---

## Task 8: Translate `kiosk_dashboard.html`

Wrap each user-facing string with `{{ t("...") }}`. Apply these exact replacements (left = current markup text, right = replacement). Proper nouns (`{{ current_wc }}`, `{{ scheduled_wc }}`, `{{ person.name }}`, times) stay untouched. The sync-warning banner (Odoo-config guidance) stays English — do not wrap it.

**Files:**
- Modify: `src/zira_dashboard/templates/kiosk_dashboard.html`

- [ ] **Step 1: Apply wraps**

- Line 5: `<a href="/kiosk" class="k-back">‹ Done</a>` → `<a href="/kiosk" class="k-back">{{ t("‹ Done") }}</a>`
- Line 28: `<h2>You're clocked in{% if current_wc %} on{% endif %}</h2>` →
  `<h2>{% if current_wc %}{{ t("You're clocked in on") }}{% else %}{{ t("You're clocked in") }}{% endif %}</h2>`
- Line 34: `Since {{ check_in_display }}` → `{{ t("Since {time}", time=check_in_display) }}`
- Line 39: `>Clock Out<` → `>{{ t("Clock Out") }}<`
- Line 43: `>Transfer<` (inside the transfer `<a>`) → `>{{ t("Transfer") }}<`
- Line 48: `<h2>Today you're scheduled on</h2>` → `<h2>{{ t("Today you're scheduled on") }}</h2>`
- Line 55: `Confirm — Clock In` → `{{ t("Confirm — Clock In") }}`
- Line 63: `I'm somewhere else` → `{{ t("I'm somewhere else") }}`
- Line 66: `<h2>You're not scheduled today</h2>` → `<h2>{{ t("You're not scheduled today") }}</h2>`
- Line 68: `Pick the work center you're on.` → `{{ t("Pick the work center you're on.") }}`
- Line 73: `Pick Work Center` → `{{ t("Pick Work Center") }}`
- Line 88: `<span>Time Off Request</span>` → `<span>{{ t("Time Off Request") }}</span>`

- [ ] **Step 2: Verify no stray un-wrapped literal remains (spot check)**

Run: `grep -nE "Clock Out|Transfer|scheduled|somewhere else|Time Off Request" src/zira_dashboard/templates/kiosk_dashboard.html`
Expected: each is now inside a `{{ t(...) }}` call.

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/kiosk_dashboard.html
git commit -m "feat(kiosk): bilingual strings on dashboard"
```

---

## Task 9: Translate `kiosk_pick_wc.html` + `kiosk_success.html`

**Files:**
- Modify: `src/zira_dashboard/templates/kiosk_pick_wc.html`
- Modify: `src/zira_dashboard/templates/kiosk_success.html`

- [ ] **Step 1: pick_wc wraps**

- Line 5: `‹ Back` → `{{ t("‹ Back") }}`
- Line 11 branch: `Pick where you're working` → `{{ t("Pick where you're working") }}`
- Line 12 branch: `Transfer to…` → `{{ t("Transfer to…") }}`
  (Keep the surrounding `{% if purpose == 'clock_in' %}…{% else %}…{% endif %}` structure; only the literal text becomes `{{ t(...) }}`.)

- [ ] **Step 2: success wraps**

- Line 15: `Returning home…` → `{{ t("Returning home…") }}`
- Leave the local-save warning (line 11, mentions Odoo) English — do not wrap.
- `{{ message }}`, `{{ time }}`, `{{ person.name }}` stay untouched (dynamic/backend text).

- [ ] **Step 3: Commit**

```bash
git add src/zira_dashboard/templates/kiosk_pick_wc.html src/zira_dashboard/templates/kiosk_success.html
git commit -m "feat(kiosk): bilingual strings on pick-WC and success screens"
```

---

## Task 10: Translate `kiosk_time_off_landing.html`

**Files:**
- Modify: `src/zira_dashboard/templates/kiosk_time_off_landing.html`

- [ ] **Step 1: Apply wraps**

- Line 6: `‹ Done` → `{{ t("‹ Done") }}`
- Line 8: `‹ Back` → `{{ t("‹ Back") }}`
- Line 10: `<span>Time Off — {{ person.name }}</span>` → `<span>{{ t("Time Off — {name}", name=person.name) }}</span>`
- Line 28-29: `Request Time Off` → `{{ t("Request Time Off") }}`
- Line 46: `Full Day(s) Off` → `{{ t("Full Day(s) Off") }}`
- Line 47-49: `Out for one or more whole days` → `{{ t("Out for one or more whole days") }}`
- Line 61: `Arriving Late` → `{{ t("Arriving Late") }}`
- Line 62-64: `Tell us what time you'll arrive` → `{{ t("Tell us what time you'll arrive") }}`
- Line 74: `Out for Part of the Day` → `{{ t("Out for Part of the Day") }}`
- Line 75-77: `Leave + return on the same day` → `{{ t("Leave + return on the same day") }}`
- Line 87: `Leaving Early` → `{{ t("Leaving Early") }}`
- Line 88-90: `Tell us what time you'll leave` → `{{ t("Tell us what time you'll leave") }}`
- Line 114: `<span>My Requests</span>` → `<span>{{ t("My Requests") }}</span>`
- Line 132: `<span>Who's Out</span>` → `<span>{{ t("Who's Out") }}</span>`
- Leave the sync-warning banner (lines 16-21, mentions Odoo) English — do not wrap.

- [ ] **Step 2: Commit**

```bash
git add src/zira_dashboard/templates/kiosk_time_off_landing.html
git commit -m "feat(kiosk): bilingual strings on time-off landing"
```

---

## Task 11: Translate `kiosk_time_off_request_details.html`

**Files:**
- Modify: `src/zira_dashboard/templates/kiosk_time_off_request_details.html`

- [ ] **Step 1: Apply wraps**

- Line 5: `‹ Back` → `{{ t("‹ Back") }}`
- Lines 7-10 header label branches: `Full Day(s) Off` / `Arriving Late` / `Leaving Early` / `Mid-Day Gap` → wrap each literal: `{{ t("Full Day(s) Off") }}`, `{{ t("Arriving Late") }}`, `{{ t("Leaving Early") }}`, `{{ t("Mid-Day Gap") }}` (keep the `{% if/elif/else %}` structure).
- Line 23-26: `Editing existing request` → `{{ t("Editing existing request") }}`
- Line 57-58: `Type` → `{{ t("Type") }}`
- Line 87: `Type:` → `{{ t("Type:") }}`
- Line 89: `&middot; Unpaid` → `&middot; {{ t("Unpaid") }}` **and** add `"Unpaid": "Sin goce"` to `TRANSLATIONS` (the inventory shows the visible text "· Unpaid"; wrapping just "Unpaid" keeps the `·` glyph outside). *(If you prefer to keep the `·` inside, wrap `"· Unpaid"` and use that key — but the glossary currently keys `"· Unpaid"`. Pick one; recommended: wrap `"· Unpaid"` whole → `{{ t("· Unpaid") }}` and drop the standalone "Unpaid" key. Use the whole-string key to match the glossary as written.)*
  → Final: `<span style="color: #64748b;">{{ t("· Unpaid") }}</span>`
- Line 105: `Available` → `{{ t("Available") }}`
- Line 109: `This request` → `{{ t("This request") }}`
- Line 113: `Remaining after` → `{{ t("Remaining after") }}`
- Line 121-123: `Start date` → `{{ t("Start date") }}`
- Line 134-135: `End date` → `{{ t("End date") }}`
- Line 148-150: `Date` → `{{ t("Date") }}`
- Line 165-167: `I'll arrive at` → `{{ t("I'll arrive at") }}`
- Line 178-180: `I'll leave at` → `{{ t("I'll leave at") }}`
- Line 191-193: `Gone from` → `{{ t("Gone from") }}`
- Line 202-203: `To` → `{{ t("To") }}`
- Line 216-218: `Note (optional)` → `{{ t("Note (optional)") }}`
- Line 238: `{% if edit_mode %}Save Changes{% else %}Submit Request{% endif %}` →
  `{% if edit_mode %}{{ t("Save Changes") }}{% else %}{{ t("Submit Request") }}{% endif %}`
- Leave the two "No … leave type … in Odoo …" config warnings (lines 34-43) English — do not wrap (Odoo-config jargon, manager-facing).

- [ ] **Step 2: Commit**

```bash
git add src/zira_dashboard/templates/kiosk_time_off_request_details.html
git commit -m "feat(kiosk): bilingual strings on time-off request details"
```

---

## Task 12: Translate calendar + mine + mine_detail + submitted

**Files:**
- Modify: `src/zira_dashboard/templates/kiosk_time_off_calendar.html`
- Modify: `src/zira_dashboard/templates/kiosk_time_off_mine.html`
- Modify: `src/zira_dashboard/templates/kiosk_time_off_mine_detail.html`
- Modify: `src/zira_dashboard/templates/kiosk_time_off_success.html`

- [ ] **Step 1: calendar wraps**

- Line 5: `‹ Back` → `{{ t("‹ Back") }}`
- Line 6: `<span>Who's Out — {{ heading }}</span>` → `<span>{{ t("Who's Out — {heading}", heading=heading) }}</span>`
- Lines 23-29 day headers: `Mon`/`Tue`/`Wed`/`Thu`/`Fri`/`Sat`/`Sun` → `{{ t("Mon") }}` … `{{ t("Sun") }}`
- Line 96: `+{{ d.names|length - 4 }} more` → `+{{ d.names|length - 4 }} {{ t("more") }}`

- [ ] **Step 2: mine wraps**

- Line 5: `‹ Back` → `{{ t("‹ Back") }}`
- Line 6: `<span>My Requests — {{ person.name }}</span>` → `<span>{{ t("My Requests — {name}", name=person.name) }}</span>`
- Line 40: `{{ r.type_name or "Time Off" }}` → `{{ r.type_name or t("Time Off") }}`
- Line 72: `No requests yet.` → `{{ t("No requests yet.") }}`
- Line 78: `Request Time Off` → `{{ t("Request Time Off") }}`

- [ ] **Step 3: mine_detail wraps**

- Line 5: `‹ Back` → `{{ t("‹ Back") }}`
- Line 6: `<span>Request Details</span>` → `<span>{{ t("Request Details") }}</span>`
- Line 44: `Dates` → `{{ t("Dates") }}`
- Line 50: `Hours` → `{{ t("Hours") }}`
- Line 56: `Shape` → `{{ t("Shape") }}`
- Line 60: `Note` → `{{ t("Note") }}`
- Line 65: `Sync error` → `{{ t("Sync error") }}`
- Line 81: `Edit Request` → `{{ t("Edit Request") }}`
- Line 88-89: `Canceling an approved request will need approval again if you change your mind.` → `{{ t("Canceling an approved request will need approval again if you change your mind.") }}`
- Line 94: `Cancel This Request` → `{{ t("Cancel This Request") }}`

- [ ] **Step 4: success (submitted) wraps**

- Line 5: `‹ Back` → `{{ t("‹ Back") }}`
- Line 6: `<span>Request Submitted</span>` → `<span>{{ t("Request Submitted") }}</span>`
- Line 12-14: `<h1 …>Request Submitted</h1>` → `<h1 …>{{ t("Request Submitted") }}</h1>`
- Line 16-22: replace the sentence (keep the `<p>`):
  `Your time-off request from <strong>{{ date_from }}</strong> to <strong>{{ date_to }}</strong> is pending approval.`
  →
  `{{ t("Your time-off request from {start} to {end} is pending approval.", start=date_from, end=date_to) }}`
  (Loses the bold on the dates — acceptable; the whole sentence renders bilingual.)
- Lines 23-30 (`You'll see it under <a>My Requests</a>.`): leave English — it embeds an inline link mid-sentence; not worth splitting. Do not wrap.
- Line 38: `Done` → `{{ t("Done") }}`
- Line 44: `My Requests` → `{{ t("My Requests") }}`

- [ ] **Step 5: Commit**

```bash
git add src/zira_dashboard/templates/kiosk_time_off_calendar.html src/zira_dashboard/templates/kiosk_time_off_mine.html src/zira_dashboard/templates/kiosk_time_off_mine_detail.html src/zira_dashboard/templates/kiosk_time_off_success.html
git commit -m "feat(kiosk): bilingual strings on calendar, my-requests, detail, submitted"
```

---

## Task 13: Render smoke test + final verification

**Files:**
- Test: `tests/test_kiosk_bilingual_render.py`

- [ ] **Step 1: Write a render smoke test (CI / 3.11+)**

This renders the dashboard template through a bare Jinja environment with `t` registered (mirrors `scripts/render_kiosk_preview.py`) and asserts Spanish appears only when `bilingual=True`.

```python
"""End-to-end-ish: the dashboard template renders Spanish under English
only for bilingual users."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from zira_dashboard import kiosk_i18n

TEMPLATES = Path("src/zira_dashboard/templates")


def _env():
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    env.globals["static_v"] = lambda _f: "test"
    env.globals["t"] = kiosk_i18n.t
    return env


def _ctx(bilingual):
    return {
        "person": {"name": "Maria Garcia"},
        "token": "t",
        "is_clocked_in": False,
        "scheduled_wc": None,
        "sync_warning": None,
        "time_off_enabled": True,
        "pending_time_off_count": 0,
        "bilingual": bilingual,
    }


def test_dashboard_english_only_when_not_bilingual():
    html = _env().get_template("kiosk_dashboard.html").render(**_ctx(False))
    assert "Pick Work Center" in html
    assert "Elegir estación" not in html


def test_dashboard_bilingual_shows_spanish():
    html = _env().get_template("kiosk_dashboard.html").render(**_ctx(True))
    assert "Pick Work Center" in html       # English still present
    assert "Elegir estación" in html        # Spanish added
    assert 'class="k-es"' in html
```

- [ ] **Step 2: Run (CI/3.11+)**

Run: `pytest tests/test_kiosk_bilingual_render.py tests/test_kiosk_i18n.py tests/test_fetch_spanish_speakers.py -v`
Expected: PASS. (Locally on 3.9 this can't run; verify the helper logic via the `ast`-extract/`exec` pattern and `py_compile` all changed modules.)

- [ ] **Step 3: Local fallback verification**

Run:
```bash
python3 -m py_compile src/zira_dashboard/kiosk_i18n.py src/zira_dashboard/deps.py \
  src/zira_dashboard/db.py src/zira_dashboard/odoo_client.py \
  src/zira_dashboard/odoo_sync.py src/zira_dashboard/routes/kiosk.py \
  src/zira_dashboard/routes/kiosk_time_off.py
```
Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add tests/test_kiosk_bilingual_render.py
git commit -m "test(kiosk): bilingual dashboard render smoke test"
```

- [ ] **Step 5: Push (deploys via Railway) and trigger a sync**

```bash
git push origin main
```
After deploy, run an Odoo sync (Settings → manual refresh, or wait for the hourly tick) so `spanish_speaker` populates. Verify on the kiosk: a Spanish-skilled employee (e.g. someone with Spanish level 3) sees stacked Spanish after picking their name; an English-only employee sees no change.

---

## Self-Review

**Spec coverage:**
- Detection (Spanish Languages skill ≥1) → Tasks 1-3. ✓
- Sync change for Languages type without polluting matrix → Task 2 (`fetch_spanish_speaker_ids`, name-matched, separate from `fetch_skills_for`). ✓
- Glossary + `t()` helper, English fallback → Task 4. ✓
- Stacked display format → Tasks 4 (markup) + 7 (CSS). ✓
- Scope = all post-selection screens; home stays English → Tasks 6, 8-12 (home untouched). ✓
- Proper nouns untranslated → noted per task (WC names, names, times left alone). ✓
- Translation review → glossary is one file (Task 4) for Dale to review. ✓
- Testing (flag, helper, fallback, render) → Tasks 2, 4, 13. ✓

**Placeholder scan:** Glossary values all non-empty (asserted by a test). No "TBD"/"handle later". The one judgment note (· Unpaid wrapping) resolves to a single explicit choice (wrap `"· Unpaid"` whole). ✓

**Type/key consistency:** Template `{{ t("X") }}` keys match `TRANSLATIONS` keys exactly, including punctuation/casing and `{name}`/`{time}`/`{start}`/`{end}`/`{heading}` format fields. Mixed-string keys (`"Since {time}"`, `"Time Off — {name}"`, `"Who's Out — {heading}"`, `"My Requests — {name}"`, `"Your time-off request from {start} to {end} is pending approval."`) are passed the matching kwargs in their template calls. ✓

**Decisions deliberately leaving text English (documented, not gaps):** sync-warning banners and Odoo-config error messages (Odoo jargon, manager-facing); the "You'll see it under My Requests." line (inline link mid-sentence); browser `<title>` tags (not visible on the fullscreen kiosk).
