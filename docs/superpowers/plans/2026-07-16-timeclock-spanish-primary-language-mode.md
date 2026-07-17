# Timeclock Spanish-Primary Language Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make exact Odoo Spanish skill level 3 render Spanish first with smaller English throughout personalized timeclock screens, while every other employee sees English only.

**Architecture:** Sync the Spanish employee-skill relation through the same 0–3 Odoo level buckets already used by production skills, persist the exact bucket on `people`, and centralize template context selection in `timeclock_i18n`. Keep the existing `spanish_speaker` boolean unchanged for object-API compatibility; only the timeclock reads `spanish_level`.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, Postgres/psycopg2, Odoo XML-RPC facade, pytest.

## Global Constraints

- Add no new runtime dependency.
- `spanish_level == 3` means Spanish-first with smaller English underneath.
- Spanish levels 0, 1, 2, missing, or invalid mean English only.
- The unidentified shared `/timeclock` home screen remains intentionally bilingual.
- Preserve the existing `spanish_speaker` field and object-API meaning: any non-zero Spanish level is true.
- Unknown translation keys must render safe English, never blank content.

---

## File Structure

- Modify `src/zira_dashboard/_schema.py` — add the exact local Spanish bucket.
- Modify `src/zira_dashboard/_odoo_skills.py` — fetch employee Spanish `skill_level_id` values.
- Modify `src/zira_dashboard/odoo_client.py` — expose the stable client facade.
- Modify `src/zira_dashboard/odoo_sync.py` — map Spanish level ids through existing 0–3 buckets and persist both fields.
- Modify `src/zira_dashboard/timeclock_i18n.py` — own language-mode selection and primary/secondary markup.
- Modify `src/zira_dashboard/templates/timeclock_base.html` — style Spanish-primary stacks.
- Modify `src/zira_dashboard/routes/timeclock.py` — select `spanish_level` and pass centralized context.
- Modify `src/zira_dashboard/routes/timeclock_time_off.py` — use the same context on every personalized time-off screen.
- Modify `tests/test_fetch_spanish_speakers.py` — cover the new Odoo helper contract.
- Modify `tests/test_odoo_sync.py` — verify persisted exact level and backward-compatible boolean.
- Create `tests/test_schema_spanish_level.py` — verify idempotent schema creation.
- Modify `tests/test_timeclock_i18n.py` — test mode selection, order, escaping, and fallback.
- Modify `tests/test_timeclock_bilingual_render.py` — assert Spanish-first and English-only template output.
- Modify `tests/test_timeclock_notifications_routes.py` — verify route-provided language mode.

---

### Task 1: Sync and persist the exact Spanish skill bucket

**Files:**
- Modify: `src/zira_dashboard/_schema.py`
- Modify: `src/zira_dashboard/_odoo_skills.py`
- Modify: `src/zira_dashboard/odoo_client.py`
- Modify: `src/zira_dashboard/odoo_sync.py`
- Modify: `tests/test_fetch_spanish_speakers.py`
- Modify: `tests/test_odoo_sync.py`
- Create: `tests/test_schema_spanish_level.py`

**Interfaces:**
- Produces: `odoo_client.fetch_spanish_skill_level_ids() -> dict[int, int]`, mapping employee Odoo id to Odoo `hr.skill.level` id.
- Produces: `people.spanish_level SMALLINT NOT NULL DEFAULT 0 CHECK (spanish_level BETWEEN 0 AND 3)`.
- Preserves: `people.spanish_speaker == (spanish_level > 0)` after every successful sync.

- [ ] **Step 1: Replace the old helper tests with failing exact-level tests**

```python
def _fake_execute(model, method, *args, **kwargs):
    if model == "hr.skill":
        return [{"id": 7, "name": "Spanish"}]
    if model == "hr.employee.skill":
        return [
            {"employee_id": [11, "Ana"], "skill_level_id": [101, "Basic"]},
            {"employee_id": [12, "Beto"], "skill_level_id": [103, "Fluent"]},
            {"employee_id": 13, "skill_level_id": False},
        ]
    raise AssertionError(f"unexpected call {model}.{method}")


def test_returns_employee_to_spanish_skill_level_id():
    with mock.patch.object(odoo_client, "execute", side_effect=_fake_execute):
        assert odoo_client.fetch_spanish_skill_level_ids() == {11: 101, 12: 103}


def test_no_spanish_skill_returns_empty_mapping():
    def no_skill(model, method, *args, **kwargs):
        if model == "hr.skill":
            return []
        raise AssertionError("employee skills must not be queried")
    with mock.patch.object(odoo_client, "execute", side_effect=no_skill):
        assert odoo_client.fetch_spanish_skill_level_ids() == {}
```

- [ ] **Step 2: Run the helper tests and verify the new API is missing**

Run: `pytest tests/test_fetch_spanish_speakers.py -v`

Expected: FAIL because `fetch_spanish_skill_level_ids` is not defined.

- [ ] **Step 3: Implement the focused Odoo helper and facade**

```python
# src/zira_dashboard/_odoo_skills.py
def fetch_spanish_skill_level_ids(execute_fn, unwrap_m2o_fn) -> dict[int, int]:
    skills = execute_fn(
        "hr.skill", "search_read", [("name", "ilike", "Spanish")],
        fields=["id", "name"],
    )
    skill_ids = [int(row["id"]) for row in skills]
    if not skill_ids:
        return {}
    rows = execute_fn(
        "hr.employee.skill", "search_read",
        [("skill_id", "in", skill_ids)],
        fields=["employee_id", "skill_level_id"],
    )
    out: dict[int, int] = {}
    for row in rows:
        employee_id = unwrap_m2o_fn(row.get("employee_id"))
        level_id = unwrap_m2o_fn(row.get("skill_level_id"))
        if employee_id and level_id:
            out[int(employee_id)] = int(level_id)
    return out


# src/zira_dashboard/odoo_client.py
def fetch_spanish_skill_level_ids() -> dict[int, int]:
    return _odoo_skills.fetch_spanish_skill_level_ids(execute, unwrap_m2o)
```

Delete `fetch_spanish_speaker_ids` from both modules after all call sites move;
do not retain two competing Spanish data sources.

- [ ] **Step 4: Run the helper tests and verify they pass**

Run: `pytest tests/test_fetch_spanish_speakers.py -v`

Expected: PASS.

- [ ] **Step 5: Add failing schema and sync assertions**

```python
# tests/test_schema_spanish_level.py
import os
import pytest
from zira_dashboard import db

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="needs Postgres"
)


def test_people_has_exact_spanish_level_bucket():
    db.bootstrap_schema()
    rows = db.query(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'people'"
    )
    assert "spanish_level" in {row["column_name"] for row in rows}
```

Extend the existing Odoo sync stub so it returns `spanish_level_ids={99002:
103}` and `buckets={103: 3}`, then assert:

```python
rows = db.query(
    "SELECT spanish_level, spanish_speaker FROM people WHERE odoo_id = 99002"
)
assert rows == [{"spanish_level": 3, "spanish_speaker": True}]
```

Add a second employee whose level id maps to bucket 2 and assert
`spanish_level == 2` while `spanish_speaker is True`. Add an employee without
a Spanish row and assert `0` and `False`.

- [ ] **Step 6: Run the sync tests and verify they fail on the missing column and call**

Run: `pytest tests/test_fetch_spanish_speakers.py tests/test_odoo_sync.py -v`

Expected: the helper tests pass; sync tests FAIL because `odoo_sync.sync()`
still calls `fetch_spanish_speaker_ids` and does not persist `spanish_level`.
The schema test may SKIP when `DATABASE_URL` is absent.

- [ ] **Step 7: Add the schema migration and persist the bucket**

```sql
ALTER TABLE people
  ADD COLUMN IF NOT EXISTS spanish_level SMALLINT NOT NULL DEFAULT 0;

ALTER TABLE people
  DROP CONSTRAINT IF EXISTS people_spanish_level_check;
ALTER TABLE people
  ADD CONSTRAINT people_spanish_level_check
  CHECK (spanish_level BETWEEN 0 AND 3);
```

Update the sync read and upsert:

```python
spanish_level_ids = odoo_client.fetch_spanish_skill_level_ids()

# inside the employee loop, after buckets has loaded
spanish_level = int(buckets.get(spanish_level_ids.get(emp["id"]), 0))
spanish_speaker = spanish_level > 0
```

Replace the existing people upsert with the same locally-owned-field-safe SQL,
adding only the synchronized level column:

```python
cur.execute(
    "INSERT INTO people (odoo_id, name, active, wage_type, spanish_speaker, "
    "spanish_level, resource_calendar_id, is_flexible, last_pulled_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (odoo_id) DO UPDATE SET name = EXCLUDED.name, "
    "active = EXCLUDED.active, wage_type = EXCLUDED.wage_type, "
    "spanish_speaker = EXCLUDED.spanish_speaker, "
    "spanish_level = EXCLUDED.spanish_level, "
    "resource_calendar_id = EXCLUDED.resource_calendar_id, "
    "is_flexible = EXCLUDED.is_flexible, "
    "last_pulled_at = EXCLUDED.last_pulled_at",
    (
        emp["id"], _short_name(emp["name"]), bool(emp.get("active", True)),
        wage_type, spanish_speaker, spanish_level,
        _m2o_id(emp.get("resource_calendar_id")), is_flex, pulled_at,
    ),
)
```

Do not update `reserve`, `excluded`, or any other locally-owned field.

- [ ] **Step 8: Run the focused sync and schema tests**

Run: `pytest tests/test_fetch_spanish_speakers.py tests/test_odoo_sync_unit.py tests/test_odoo_sync.py tests/test_schema_spanish_level.py -v`

Expected: PASS, with Postgres-gated tests SKIP when no `DATABASE_URL` is set.

- [ ] **Step 9: Commit the exact-level sync**

```bash
git add src/zira_dashboard/_schema.py src/zira_dashboard/_odoo_skills.py src/zira_dashboard/odoo_client.py src/zira_dashboard/odoo_sync.py tests/test_fetch_spanish_speakers.py tests/test_odoo_sync.py tests/test_schema_spanish_level.py
git commit -m "feat: sync exact Spanish skill level"
```

---

### Task 2: Render Spanish-primary or English-only from one helper

**Files:**
- Modify: `src/zira_dashboard/timeclock_i18n.py`
- Modify: `src/zira_dashboard/templates/timeclock_base.html`
- Modify: `tests/test_timeclock_i18n.py`
- Modify: `tests/test_timeclock_bilingual_render.py`

**Interfaces:**
- Consumes: `person["spanish_level"]` from Task 1.
- Produces: `timeclock_i18n.language_mode_for_person(person) -> Literal["en", "es_primary"]`.
- Produces: `timeclock_i18n.context_for_person(person) -> dict[str, str]` with key `timeclock_language`.
- Changes: `t()` reads `timeclock_language`; `bilingual` is retired from personalized screens.

- [ ] **Step 1: Rewrite the i18n unit tests around the approved modes**

```python
def _render(text, mode="en", **kwargs):
    return timeclock_i18n.t(
        _Ctx(timeclock_language=mode), text, **kwargs
    )


def test_level_three_selects_spanish_primary():
    assert timeclock_i18n.language_mode_for_person({"spanish_level": 3}) == "es_primary"


@pytest.mark.parametrize("value", [None, 0, 1, 2, 4, "3"])
def test_every_other_value_selects_english(value):
    assert timeclock_i18n.language_mode_for_person({"spanish_level": value}) == "en"


def test_spanish_primary_stacks_spanish_then_small_english():
    out = str(_render("Clock Out", "es_primary"))
    assert '<span class="k-es k-primary">Marcar salida</span>' in out
    assert '<span class="k-en k-secondary">Clock Out</span>' in out
    assert out.index("k-es k-primary") < out.index("k-en k-secondary")


def test_english_mode_is_plain_english():
    assert _render("Clock Out", "en") == "Clock Out"


def test_unknown_spanish_key_falls_back_to_english_only():
    assert _render("Unknown label", "es_primary") == "Unknown label"
```

Keep the existing format-substitution, HTML escaping, and non-empty glossary
tests; update them to use `es_primary`.

- [ ] **Step 2: Run the i18n tests and verify they fail**

Run: `pytest tests/test_timeclock_i18n.py tests/test_timeclock_bilingual_render.py -v`

Expected: FAIL because the helper and `timeclock_language` contract do not exist.

- [ ] **Step 3: Implement the centralized mode and markup**

```python
from typing import Literal

LanguageMode = Literal["en", "es_primary"]


def language_mode_for_person(person: dict | None) -> LanguageMode:
    if person and person.get("spanish_level") == 3:
        return "es_primary"
    return "en"


def context_for_person(person: dict | None) -> dict[str, LanguageMode]:
    return {"timeclock_language": language_mode_for_person(person)}


@pass_context
def t(ctx, text: str, **kwargs) -> str | Markup:
    english = _fill(text, kwargs)
    if ctx.get("timeclock_language", "en") != "es_primary":
        return english
    spanish_template = TRANSLATIONS.get(text)
    if not spanish_template:
        return english
    spanish = _fill(spanish_template, kwargs)
    return Markup(
        '<span class="k-bi k-bi-es-primary">'
        '<span class="k-es k-primary">{}</span>'
        '<span class="k-en k-secondary">{}</span>'
        '</span>'
    ).format(spanish, english)
```

Replace the old `.k-es`-always-small CSS with role-based styling:

```css
.k-bi { display:inline-flex; flex-direction:column; align-items:center; vertical-align:middle; line-height:1.15; }
.k-primary { font-size:1em; font-weight:inherit; color:inherit; }
.k-secondary { font-size:.6em; font-weight:400; font-style:italic; color:#64748b; line-height:1.2; margin-top:.2em; opacity:.9; }
.k-btn:not(.secondary) .k-secondary { color:rgba(255,255,255,.9); opacity:1; }
```

- [ ] **Step 4: Update the template-render fixtures and verify output order**

```python
def _ctx(spanish_level):
    person = {"name": "Maria Garcia", "spanish_level": spanish_level}
    return {
        "person": person,
        "token": "t",
        "is_clocked_in": False,
        "scheduled_wc": None,
        "sync_warning": None,
        "time_off_enabled": True,
        "pending_time_off_count": 0,
        "timeclock_language": timeclock_i18n.language_mode_for_person(person),
    }
```

Assert level 3 contains Spanish before English and level 2 contains no Spanish.

- [ ] **Step 5: Run the i18n and direct-template tests**

Run: `pytest tests/test_timeclock_i18n.py tests/test_timeclock_bilingual_render.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the centralized rendering mode**

```bash
git add src/zira_dashboard/timeclock_i18n.py src/zira_dashboard/templates/timeclock_base.html tests/test_timeclock_i18n.py tests/test_timeclock_bilingual_render.py
git commit -m "feat: render level-three Spanish first"
```

---

### Task 3: Apply the mode to every personalized timeclock route

**Files:**
- Modify: `src/zira_dashboard/routes/timeclock.py`
- Modify: `src/zira_dashboard/routes/timeclock_time_off.py`
- Modify: `tests/test_timeclock_notifications_routes.py`
- Modify: `tests/test_timeclock_time_off_static.py`
- Modify: `tests/test_timeclock_bilingual_render.py`

**Interfaces:**
- Consumes: `timeclock_i18n.context_for_person(person)` from Task 2.
- Preserves: shared `/timeclock` rendering without person-specific context.
- Produces: every personalized template context contains `timeclock_language`.

- [ ] **Step 1: Add failing route coverage for levels 3 and 2**

```python
from datetime import date

PERSON_ES = {
    "id": 2, "name": "José", "odoo_id": 7,
    "wage_type": "hourly", "spanish_speaker": True, "spanish_level": 3,
}
PERSON_LEVEL_2 = {
    "id": 3, "name": "Luis", "odoo_id": 8,
    "wage_type": "hourly", "spanish_speaker": True, "spanish_level": 2,
}


def test_notifications_level_three_is_spanish_first(monkeypatch):
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON_ES)
    monkeypatch.setattr(employee_notifications, "list_unacknowledged", lambda oid: [{
        "id": 1, "kind": "time_off_approved",
        "leave_date_from": date(2026, 7, 1), "leave_date_to": date(2026, 7, 1),
    }])
    response = client.get(f"/timeclock/notifications/{timeclock._mint_token(2)}")
    assert response.status_code == 200
    assert response.text.index("Tiempo libre aprobado") < response.text.index("Time off approved")


def test_notifications_level_two_is_english_only(monkeypatch):
    monkeypatch.setattr(timeclock, "_person_by_id", lambda pid: PERSON_LEVEL_2)
    monkeypatch.setattr(employee_notifications, "list_unacknowledged", lambda oid: [{
        "id": 1, "kind": "time_off_approved",
        "leave_date_from": date(2026, 7, 1), "leave_date_to": date(2026, 7, 1),
    }])
    response = client.get(f"/timeclock/notifications/{timeclock._mint_token(3)}")
    assert response.status_code == 200
    assert "Time off approved" in response.text
    assert "Tiempo libre aprobado" not in response.text
```

Add `test_time_off_request_level_three_context_is_spanish_primary` to the
existing time-off route test module. Capture the template context through its
existing `templates.TemplateResponse` monkeypatch and assert
`context["timeclock_language"] == "es_primary"` for `PERSON_ES`; repeat with
`PERSON_LEVEL_2` and assert `"en"`. These assertions prevent the second router
from retaining the old `bilingual` flag.

- [ ] **Step 2: Run the personalized route tests and verify they fail**

Run: `pytest tests/test_timeclock_notifications_routes.py tests/test_timeclock_time_off_static.py -v`

Expected: FAIL because route contexts still use `spanish_speaker`/`bilingual`.

- [ ] **Step 3: Select the new column and centralize route context**

Update `_person_by_id`:

```python
rows = db.query(
    "SELECT id, name, odoo_id, wage_type, spanish_speaker, spanish_level "
    "FROM people WHERE id = %s AND active = TRUE",
    (person_id,),
)
```

Import `timeclock_i18n` in both route modules. Replace every personalized
context entry:

```python
"bilingual": bool(p.get("spanish_speaker")),
```

with:

```python
**timeclock_i18n.context_for_person(p),
```

Use `rg -n 'bilingual|spanish_speaker' src/zira_dashboard/routes/timeclock.py src/zira_dashboard/routes/timeclock_time_off.py` to verify no personalized context still uses the retired flag. `spanish_speaker` may remain in the SELECT for backward-compatible data consumers, but it must not choose UI language.

- [ ] **Step 4: Run all personalized timeclock rendering tests**

Run: `pytest tests/test_timeclock_i18n.py tests/test_timeclock_bilingual_render.py tests/test_timeclock_notifications_routes.py tests/test_timeclock_time_off_static.py tests/test_timeclock_time_off_only.py -v`

Expected: PASS.

- [ ] **Step 5: Run the broader timeclock and Odoo regression set**

Run: `pytest tests/test_fetch_spanish_speakers.py tests/test_odoo_sync_unit.py tests/test_timeclock_*.py tests/test_employee_notifications.py tests/test_time_off_reminder.py -v`

Expected: PASS; database-gated cases SKIP when no `DATABASE_URL` exists.

- [ ] **Step 6: Run static checks**

Run: `ruff check src/zira_dashboard/_odoo_skills.py src/zira_dashboard/odoo_client.py src/zira_dashboard/odoo_sync.py src/zira_dashboard/timeclock_i18n.py src/zira_dashboard/routes/timeclock.py src/zira_dashboard/routes/timeclock_time_off.py tests/test_fetch_spanish_speakers.py tests/test_timeclock_i18n.py tests/test_timeclock_bilingual_render.py tests/test_timeclock_notifications_routes.py`

Expected: PASS with no diagnostics.

- [ ] **Step 7: Commit the route-wide migration**

```bash
git add src/zira_dashboard/routes/timeclock.py src/zira_dashboard/routes/timeclock_time_off.py tests/test_timeclock_notifications_routes.py tests/test_timeclock_time_off_static.py tests/test_timeclock_bilingual_render.py
git commit -m "feat: apply Spanish-first mode across timeclock"
```

---

## Plan 1 Completion Gate

Before beginning Saturday recruiting, verify:

```bash
pytest tests/test_fetch_spanish_speakers.py tests/test_odoo_sync_unit.py tests/test_timeclock_i18n.py tests/test_timeclock_bilingual_render.py tests/test_timeclock_notifications_routes.py tests/test_timeclock_time_off_static.py tests/test_timeclock_time_off_only.py -v
ruff check src/zira_dashboard tests/test_fetch_spanish_speakers.py tests/test_timeclock_i18n.py tests/test_timeclock_bilingual_render.py tests/test_timeclock_notifications_routes.py
git status --short
```

Expected: tests and lint pass, and `git status --short` shows only unrelated pre-existing user files.
