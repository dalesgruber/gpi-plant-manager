# Scheduler Draft, Posted, and Delivery Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Give every scheduler day a shared autosaved Draft/Posted lifecycle, version-specific Print/Slack completion status, and live updates for all users.

**Architecture:** Extend the Schedule record with a versioned published_delivery JSONB document. The staffing domain owns the one-way Posted-to-Draft snapshot transition and atomic delivery updates; FastAPI routes expose those operations, while the browser renders the selected version and polls a lightweight revision API.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, vanilla JavaScript/CSS, Postgres JSONB/psycopg2, pytest.

## Global Constraints

- Apply the same lifecycle to every calendar day, including Saturdays and Sundays.
- Preserve existing Saturday recruiting and staffing-minimum publication validation.
- The latest completed autosave wins; do not add merge UI or a blocking conflict dialog.
- Draft shows Publish only. Posted shows Print and Slack only.
- Print completes on browser afterprint. Slack completes only after successful upload.
- Delivery completion is bound to the exact publication version.
- Add no dependencies and retain existing cache invalidation/authentication conventions.

---

## File structure

- Modify src/zira_dashboard/_schema.py for the JSONB delivery field.
- Modify src/zira_dashboard/staffing.py for delivery persistence, snapshot lifecycle, revision reads, and atomic version-qualified updates. Extend its existing collections.abc import to include Mapping.
- Modify src/zira_dashboard/routes/staffing.py for form/Hours lifecycle application plus live and print routes.
- Modify src/zira_dashboard/routes/share.py to verify, render, and mark one posted version.
- Modify the staffing template, JavaScript, and CSS for the new header and live refresh.
- Modify focused existing scheduler tests and add tests/test_staffing_delivery.py for database and route contracts.

### Task 1: Add versioned delivery persistence and snapshot lifecycle

**Files:**
- Modify: src/zira_dashboard/_schema.py:156-193
- Modify: src/zira_dashboard/staffing.py:330-625
- Modify: tests/test_staffing_schedule_metadata.py
- Modify: tests/test_staffing_custom_hours.py
- Create: tests/test_staffing_delivery.py

**Interfaces:**
- Produces Schedule.published_delivery: dict[str, str].
- Produces new_published_delivery() -> dict[str, str].
- Produces draft_from_posted(schedule: Schedule) -> Schedule.
- Produces schedule_revision(day: date) -> str | None.
- Produces delivery_for_version(day: date, version: str) -> dict[str, str] | None.
- Produces record_delivery(day: date, version: str, fields: Mapping[str, str]) -> dict[str, str] | None.

- [ ] **Step 1: Write failing lifecycle tests**

Add to tests/test_staffing_schedule_metadata.py:

~~~
def test_snapshot_includes_hours_and_delivery():
    posted = _schedule(
        published=True,
        custom_hours={"start": "06:00", "end": "12:00", "breaks": []},
        published_delivery={"version": "v1", "printed_at": "2026-07-14T12:00:00+00:00"},
    )

    snapshot = staffing.snapshot_of(posted)

    assert snapshot["custom_hours"] == posted.custom_hours
    assert snapshot["published_delivery"] == posted.published_delivery


def test_draft_from_posted_preserves_official_version_and_clears_draft_delivery():
    posted = _schedule(
        published=True,
        notes="official",
        published_delivery={"version": "v1", "printed_at": "now"},
    )

    draft = staffing.draft_from_posted(posted)

    assert draft.published is False
    assert draft.published_delivery == {}
    assert draft.published_snapshot["notes"] == "official"
    assert draft.published_snapshot["published_delivery"] == {"version": "v1", "printed_at": "now"}
~~~

Add to tests/test_staffing_custom_hours.py:

~~~
def test_schedule_delivery_defaults_to_empty_mapping():
    assert Schedule(day=date(2026, 4, 28)).published_delivery == {}
~~~

Create tests/test_staffing_delivery.py:

~~~
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

from zira_dashboard import db, staffing

@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")
def test_record_delivery_updates_only_matching_current_version():
    day = date(2099, 12, 30)
    db.execute("DELETE FROM schedules WHERE day = %s", (day,))
    try:
        staffing.save_schedule(staffing.Schedule(
            day=day, published=True, published_delivery={"version": "current"},
        ))

        delivery = staffing.record_delivery(
            day, "current", {"printed_at": "2099-12-30T12:00:00+00:00"},
        )

        assert delivery["version"] == "current"
        assert delivery["printed_at"] == "2099-12-30T12:00:00+00:00"
        assert staffing.record_delivery(day, "old", {"printed_at": "no"}) is None
    finally:
        db.execute("DELETE FROM schedules WHERE day = %s", (day,))
~~~

- [ ] **Step 2: Run the tests to verify they fail**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_schedule_metadata.py tests/test_staffing_custom_hours.py tests/test_staffing_delivery.py -q
~~~

Expected: FAIL because Schedule has no published_delivery or lifecycle/delivery helpers.

- [ ] **Step 3: Add the field and lifecycle helper**

Add the schema migration immediately after the existing schedules migrations:

~~~
ALTER TABLE schedules
  ADD COLUMN IF NOT EXISTS published_delivery JSONB NOT NULL DEFAULT '{}'::jsonb;

UPDATE schedules
   SET published_delivery = jsonb_build_object('version', 'legacy-' || day::text)
 WHERE published
   AND COALESCE(published_delivery->>'version', '') = '';
~~~

In staffing.py add a dict field to Schedule:

~~~
    published_delivery: dict[str, str] = field(default_factory=dict)
~~~

Import deepcopy and uuid4, then add:

~~~
def new_published_delivery() -> dict[str, str]:
    return {"version": uuid4().hex}


def _delivery_mapping(value) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    keys = ("version", "printed_at", "slack_posted_at", "slack_permalink")
    return {key: value[key] for key in keys if isinstance(value.get(key), str) and value[key]}


def draft_from_posted(schedule: Schedule) -> Schedule:
    if not schedule.published:
        return schedule
    draft = deepcopy(schedule)
    draft.published_snapshot = (
        deepcopy(schedule.published_snapshot)
        if schedule.published_snapshot else snapshot_of(schedule)
    )
    draft.published = False
    draft.published_delivery = {}
    return draft
~~~

Make snapshot_of include:

~~~
        "custom_hours": deepcopy(sched.custom_hours),
        "published_delivery": _delivery_mapping(sched.published_delivery),
~~~

Select, normalize, and persist published_delivery in single/bulk load and save_schedule. The insert/upsert must serialize an empty mapping as JSONB, not SQL NULL.
The bootstrap backfill gives every already-posted day a stable legacy version so its
Print and Slack actions work immediately; the next successful publish replaces it
with a new UUID version.

- [ ] **Step 4: Implement revision and atomic delivery storage**

Add these functions adjacent to save_schedule:

~~~
def schedule_revision(day: date) -> str | None:
    from . import db
    rows = db.query(
        "SELECT updated_at::text AS revision FROM schedules WHERE day = %s", (day,)
    )
    return rows[0]["revision"] if rows else None


def delivery_for_version(day: date, version: str) -> dict[str, str] | None:
    schedule = load_schedule(day)
    current = _delivery_mapping(schedule.published_delivery)
    if schedule.published and current.get("version") == version:
        return current
    prior = _delivery_mapping((schedule.published_snapshot or {}).get("published_delivery"))
    return prior if prior.get("version") == version else None


def record_delivery(day: date, version: str, fields: Mapping[str, str]) -> dict[str, str] | None:
    from . import db
    patch = {
        key: value for key, value in fields.items()
        if key in {"printed_at", "slack_posted_at", "slack_permalink"}
        and isinstance(value, str) and value
    }
    if not version or not patch:
        return None
    with db.cursor() as cur:
        cur.execute(
            """
            UPDATE schedules
               SET published_delivery = CASE
                     WHEN published THEN published_delivery || %s::jsonb
                     ELSE published_delivery END,
                   published_snapshot = CASE
                     WHEN NOT published THEN jsonb_set(
                       published_snapshot, '{published_delivery}',
                       COALESCE(published_snapshot->'published_delivery', '{}'::jsonb) || %s::jsonb)
                     ELSE published_snapshot END,
                   updated_at = now()
             WHERE day = %s
               AND ((published AND published_delivery->>'version' = %s)
                 OR (NOT published AND published_snapshot->'published_delivery'->>'version' = %s))
             RETURNING CASE WHEN published THEN published_delivery
                            ELSE published_snapshot->'published_delivery' END AS delivery
            """,
            (json.dumps(patch), json.dumps(patch), day, version, version),
        )
        row = cur.fetchone()
    _invalidate_schedule_cache(day)
    return _delivery_mapping(row["delivery"]) if row else None
~~~

The conditional SQL is required: a read-then-save update could mark a later publication after a concurrent edit.

- [ ] **Step 5: Run the focused tests and commit**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_schedule_metadata.py tests/test_staffing_custom_hours.py tests/test_staffing_delivery.py -q
~~~

Expected: PASS, with database tests skipped when DATABASE_URL is unset.

~~~
git add src/zira_dashboard/_schema.py src/zira_dashboard/staffing.py tests/test_staffing_schedule_metadata.py tests/test_staffing_custom_hours.py tests/test_staffing_delivery.py
git commit -m "feat: add versioned schedule delivery state"
~~~

### Task 2: Apply Posted-to-Draft conversion to every schedule edit

**Files:**
- Modify: src/zira_dashboard/routes/staffing.py:1280-1675,2136-2179
- Modify: tests/test_staffing_schedule_metadata.py
- Modify: tests/test_staffing_custom_hours.py
- Modify: tests/test_staffing_saturday_recruiting.py

**Interfaces:**
- Consumes staffing.draft_from_posted before every user-originated schedule content mutation.
- Produces save JSON fields revision, published, has_snapshot, and posted_version.
- Produces GET /staffing/live?day=YYYY-MM-DD with a no-store revision response.

- [ ] **Step 1: Write failing form and Hours lifecycle tests**

Replace the notes-only expectation with:

~~~
def test_notes_save_on_posted_schedule_creates_draft_snapshot(monkeypatch):
    existing = _schedule(
        published=True,
        notes="posted",
        published_delivery={"version": "v1", "printed_at": "now"},
    )
    saved = _capture_route_save(monkeypatch, existing)

    staffing_routes._staffing_save_work(
        SimpleNamespace(headers={}), DAY, 0, _save_form("save", notes="draft note"),
    )

    assert saved[0].published is False
    assert saved[0].notes == "draft note"
    assert saved[0].published_delivery == {}
    assert saved[0].published_snapshot["published_delivery"]["version"] == "v1"
~~~

Add an async form request helper and Hours test:

~~~
class _FormRequest:
    def __init__(self, values):
        self._values = FormData(values)
    async def form(self):
        return self._values


def test_hours_save_on_posted_schedule_starts_draft(monkeypatch):
    saved = []
    posted = staffing.Schedule(day=DAY, published=True, published_delivery={"version": "v1"})
    monkeypatch.setattr(staffing_routes.staffing, "load_schedule", lambda _day: posted)
    monkeypatch.setattr(staffing_routes.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(staffing_routes._http_cache, "invalidate_today_cache", lambda: None)

    response = asyncio.run(staffing_routes.staffing_hours_save(_FormRequest({
        "day": DAY.isoformat(), "start": "06:00", "end": "12:00",
    })))

    assert response.status_code == 200
    assert saved[0].published is False
    assert saved[0].published_snapshot["published_delivery"]["version"] == "v1"
~~~

- [ ] **Step 2: Verify the tests fail**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_schedule_metadata.py tests/test_staffing_custom_hours.py tests/test_staffing_saturday_recruiting.py -q
~~~

Expected: FAIL because save_notes and Hours retain Posted state.

- [ ] **Step 3: Use the lifecycle helper from form mutations**

In _staffing_save_work:

1. Accept only save and publish in this form route; remove save_notes, discard_draft, and unpublish from its action set and delete their branches. The separate past-schedules route is not changed by this feature.
2. Keep the posted-view rejection for every mutation so the historical tab remains read-only.
3. Before forming a normal save, use:

~~~
    existing = staffing.load_schedule(d)
    if action == "save":
        existing = staffing.draft_from_posted(existing)
~~~

4. On a successful publish create the sole active official version:

~~~
    if action == "publish" and not publish_block:
        published = True
        published_snapshot = None
        published_delivery = staffing.new_published_delivery()
    else:
        published = existing.published
        published_snapshot = existing.published_snapshot
        published_delivery = existing.published_delivery
~~~

Pass published_delivery through every Schedule constructor. Failed publication must retain the newly created Draft and its snapshot, exactly as other edits do.

In staffing_hours_save and direct schedule-changing route handlers (clear testing day, clear/restore partial time off), start with:

~~~
sched = staffing.draft_from_posted(staffing.load_schedule(d))
~~~

Perform this before setting values in both Hours reset and normal-save branches. Do not apply it to read-only pages or delivery status endpoints.

- [ ] **Step 4: Add the live revision route and response fields**

Add in routes/staffing.py:

~~~
@router.get("/staffing/live")
def staffing_live(day: str = Query(...)):
    try:
        target_day = date.fromisoformat(day)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad day"}, status_code=400)
    sched = staffing.load_schedule(target_day)
    delivery = (
        sched.published_delivery if sched.published
        else (sched.published_snapshot or {}).get("published_delivery") or {}
    )
    response = JSONResponse({
        "ok": True,
        "revision": staffing.schedule_revision(target_day),
        "published": sched.published,
        "has_snapshot": bool(sched.published_snapshot) and not sched.published,
        "posted_version": delivery.get("version"),
    })
    response.headers["Cache-Control"] = "no-store"
    return response
~~~

Add the same lifecycle fields to successful form JSON responses. The posted version comes from current delivery when published and snapshot delivery when Draft.

- [ ] **Step 5: Run scheduler mutations and commit**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_schedule_metadata.py tests/test_staffing_custom_hours.py tests/test_staffing_saturday_recruiting.py tests/test_staffing_rotations.py -q
~~~

Expected: PASS.

~~~
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_schedule_metadata.py tests/test_staffing_custom_hours.py tests/test_staffing_saturday_recruiting.py
git commit -m "feat: start drafts when posted schedules change"
~~~

### Task 3: Add exact-version Print and Slack delivery routes

**Files:**
- Modify: src/zira_dashboard/routes/staffing.py
- Modify: src/zira_dashboard/routes/share.py
- Modify: tests/test_staffing_delivery.py
- Modify: tests/test_share_route.py

**Interfaces:**
- Produces POST /staffing/mark-printed?day=...&version=....
- Changes POST /staffing/share-to-slack to require version and return delivery.
- Both routes consume delivery_for_version and record_delivery.

- [ ] **Step 1: Write failing delivery route tests**

Add to tests/test_staffing_delivery.py:

~~~
def test_mark_printed_records_matching_posted_version(monkeypatch):
    monkeypatch.setattr(staffing, "delivery_for_version", lambda _day, version: {"version": version})
    monkeypatch.setattr(
        staffing, "record_delivery",
        lambda _day, version, fields: {"version": version, **fields},
    )

    response = TestClient(app).post("/staffing/mark-printed?day=2026-07-14&version=v1")

    assert response.status_code == 200
    assert response.json()["delivery"]["version"] == "v1"
    assert "printed_at" in response.json()["delivery"]


def test_mark_printed_rejects_stale_version(monkeypatch):
    monkeypatch.setattr(staffing, "delivery_for_version", lambda *_args: None)

    response = TestClient(app).post("/staffing/mark-printed?day=2026-07-14&version=old")

    assert response.status_code == 409
~~~

Update share-route fixtures to send version=v1, monkeypatch delivery_for_version and record_delivery, assert staffing_page is called with view="posted", and add a stale-version test asserting _render_pdf and upload_pdf were not called.

- [ ] **Step 2: Verify delivery tests fail**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_delivery.py tests/test_share_route.py -q
~~~

Expected: FAIL because neither endpoint recognizes a publication version.

- [ ] **Step 3: Implement print completion**

Add to routes/staffing.py:

~~~
@router.post("/staffing/mark-printed")
def staffing_mark_printed(day: str = Query(...), version: str = Query(...)):
    try:
        target_day = date.fromisoformat(day)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad day"}, status_code=400)
    if not staffing.delivery_for_version(target_day, version):
        return JSONResponse({"ok": False, "error": "This posted schedule has changed."}, status_code=409)
    delivery = staffing.record_delivery(
        target_day, version, {"printed_at": plant_now().isoformat()},
    )
    if not delivery:
        return JSONResponse({"ok": False, "error": "This posted schedule has changed."}, status_code=409)
    _http_cache.invalidate_today_cache()
    return JSONResponse({"ok": True, "delivery": delivery})
~~~

- [ ] **Step 4: Make Slack version-qualified and Posted-only**

Require version: str = Query(...) in share_to_slack. Before rendering:

~~~
    try:
        target_day = date.fromisoformat(day)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad day"}, status_code=400)
    if not staffing.delivery_for_version(target_day, version):
        return JSONResponse(
            {"ok": False, "error": "This posted schedule has changed."}, status_code=409,
        )
~~~

Call staffing_page with view="posted". After upload succeeds:

~~~
    delivery = staffing.record_delivery(target_day, version, {
        "slack_posted_at": plant_now().isoformat(),
        "slack_permalink": result["permalink"],
    })
    if not delivery:
        return JSONResponse(
            {"ok": False, "error": "Schedule changed while Slack was posting; delivery was not marked."},
            status_code=409,
        )
~~~

Return delivery with the existing channel response. Do not call publish from this route or browser code.

- [ ] **Step 5: Run delivery tests and commit**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_delivery.py tests/test_share_route.py -q
~~~

Expected: PASS.

~~~
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/routes/share.py tests/test_staffing_delivery.py tests/test_share_route.py
git commit -m "feat: track printed and Slack-posted schedules"
~~~

### Task 4: Replace the header and implement browser live updates

**Files:**
- Modify: src/zira_dashboard/routes/staffing.py:887-925,1190-1248
- Modify: src/zira_dashboard/templates/staffing.html:171-231,485-487
- Modify: src/zira_dashboard/static/staffing.js:16-60,1148-1358
- Modify: src/zira_dashboard/static/staffing.css:260-286,642-650,789-810
- Modify: tests/test_staffing_static.py
- Modify: tests/test_staffing_schedule_metadata.py

**Interfaces:**
- Produces template values posted_delivery, posted_version, and schedule_revision.
- Produces window.SCHEDULE_POSTED_VERSION and window.SCHEDULE_REVISION.
- Produces afterprint persistence and a three-second idle-safe revision poll.

- [ ] **Step 1: Write failing UI contract tests**

Replace the edit-gate assertions in tests/test_staffing_static.py with:

~~~
def test_header_uses_only_orange_draft_and_green_posted_toggle():
    html = _template()
    css = _style()

    assert 'class="draft-label"' not in html
    assert 'id="edit-schedule-btn"' not in html
    assert 'view-toggle-btn draft' in html
    assert 'view-toggle-btn posted' in html
    assert ".view-toggle-btn.active.draft {" in css
    assert ".view-toggle-btn.active.posted {" in css


def test_draft_has_publish_and_posted_has_delivery_actions():
    html = _template()

    assert "{% if published or viewing_posted %}" in html
    assert 'onclick="printSchedule(this)"' in html
    assert 'onclick="postToSlack(this)"' in html
    assert "discard_draft" not in html
    assert "save_notes" not in html


def test_browser_records_print_after_dialog_and_polls_live_revision():
    js = _script()

    assert "window.addEventListener('afterprint'" in js
    assert "/staffing/mark-printed?day=" in js
    assert "setInterval(checkLiveRevision, 3000);" in js
    assert "/staffing/live?day=" in js
    assert "window.SCHEDULE_REVISION = data.revision;" in js
~~~

- [ ] **Step 2: Verify the UI contract fails**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_static.py tests/test_staffing_schedule_metadata.py -q
~~~

Expected: FAIL because the current toolbar contains red Draft/Edit controls and has no delivery/revision browser logic.

- [ ] **Step 3: Pass the selected official version to the template**

When staffing_page renders a posted snapshot, copy the snapshot values onto its display-only deepcopy:

~~~
        sched.custom_hours = copy.deepcopy(snap.get("custom_hours"))
        sched.published_delivery = staffing._delivery_mapping(snap.get("published_delivery"))
~~~

Before TemplateResponse derive:

~~~
    posted_delivery = (
        dict(sched.published_delivery or {}) if (sched.published or viewing_posted) else {}
    )
    posted_version = posted_delivery.get("version")
~~~

Pass posted_delivery, posted_version, and schedule_revision=staffing.schedule_revision(d) in the context. This must not mutate the cached active draft.

- [ ] **Step 4: Render mutually exclusive Draft and Posted actions**

Replace the header with a segmented control that always renders Draft and renders Posted when the current schedule is published or has a snapshot:

~~~
<div class="view-toggle" role="tablist" aria-label="Schedule version">
  <a href="/staffing?day={{ day }}&amp;view=draft"
     class="view-toggle-btn draft {% if not viewing_posted and not published %}active{% endif %}">Draft</a>
  {% if published or has_snapshot %}
    <a href="/staffing?day={{ day }}&amp;view=posted"
       class="view-toggle-btn posted {% if viewing_posted or published %}active{% endif %}">Posted</a>
  {% endif %}
</div>
~~~

Use this mutually exclusive action condition:

~~~
{% if published or viewing_posted %}
  <!-- Print and Slack icon buttons; add complete when their posted_delivery timestamp is present -->
{% else %}
  <span id="autosave-indicator" class="autosave clean" aria-live="polite"></span>
  <button type="submit" name="action" value="publish" class="publish-btn publish-submit" aria-busy="false">Publish</button>
{% endif %}
~~~

Retain existing icons. Add .complete buttons with green background/border and a CSS checkmark. Add orange .active.draft and green .active.posted rules. Remove the red Draft label, Edit button, discard action, and their CSS.

- [ ] **Step 5: Update autosave, delivery, and live polling JavaScript**

Initialize:

~~~
window.SCHEDULE_POSTED_VERSION = {{ posted_version|tojson }};
window.SCHEDULE_REVISION = {{ schedule_revision|tojson }};
~~~

Remove unlocked/Edit and notesOnly logic. On autosave success parse JSON and set:

~~~
window.SCHEDULE_REVISION = data.revision || window.SCHEDULE_REVISION;
~~~

Replace print with a pending-button afterprint flow:

~~~
let pendingPrintButton = null;

function printSchedule(button) {
  if (!window.SCHEDULE_POSTED_VERSION) return;
  pendingPrintButton = button;
  window.print();
}

window.addEventListener("afterprint", async () => {
  const button = pendingPrintButton;
  pendingPrintButton = null;
  if (!button || !window.SCHEDULE_POSTED_VERSION) return;
  const url = "/staffing/mark-printed?day=" + encodeURIComponent(window.SCHEDULE_DAY)
    + "&version=" + encodeURIComponent(window.SCHEDULE_POSTED_VERSION);
  const response = await fetch(url, {method: "POST", headers: {"Accept": "application/json"}});
  const data = await response.json();
  if (!response.ok) return showToast(data.error || "Print status was not saved", null, "error");
  button.classList.add("complete");
  button.title = "Printed";
  button.setAttribute("aria-label", "Printed");
});
~~~

Change postToSlack to require SCHEDULE_POSTED_VERSION and call only:

~~~
const url = "/staffing/share-to-slack?day=" + encodeURIComponent(day)
  + "&version=" + encodeURIComponent(window.SCHEDULE_POSTED_VERSION);
const r = await fetch(url, {method: "POST", headers: {"Accept": "application/json"}});
~~~

On success add complete and set title/aria-label to Posted to Slack. Remove publish/republish confirmation and form POST logic.

Set window.schedulerAutosaveBusy whenever autosave state is not clean and add:

~~~
async function checkLiveRevision() {
  if (document.visibilityState !== "visible" || !window.SCHEDULE_DAY) return;
  const response = await fetch("/staffing/live?day=" + encodeURIComponent(window.SCHEDULE_DAY), {
    headers: {"Accept": "application/json", "Cache-Control": "no-cache"},
  });
  const data = await response.json();
  if (!response.ok || !data.revision || data.revision === window.SCHEDULE_REVISION) return;
  if (window.schedulerAutosaveBusy) return;
  showToast("Schedule updated by another user — refreshed just now.");
  window.location.reload();
}
document.addEventListener("visibilitychange", checkLiveRevision);
setInterval(checkLiveRevision, 3000);
~~~

A dirty page keeps its local input; its next successful save updates the revision and therefore wins, exactly as requested.

- [ ] **Step 6: Run UI regression tests and commit**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_static.py tests/test_staffing_schedule_metadata.py tests/test_staffing_rotations.py tests/test_staffing_saturday_recruiting.py -q
~~~

Expected: PASS.

~~~
git add src/zira_dashboard/routes/staffing.py src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py tests/test_staffing_schedule_metadata.py
git commit -m "feat: simplify scheduler draft and posted workflow"
~~~

### Task 5: Verify weekday/weekend parity and full scheduler regression

**Files:**
- Modify: tests/test_staffing_delivery.py
- Modify: tests/test_staffing_saturday_recruiting.py
- Modify: tests/test_share_route.py

**Interfaces:**
- Consumes all lifecycle, delivery, and live revision functions from Tasks 1–4.
- Produces regression coverage for weekday, Saturday, Sunday, stale delivery, and Slack failure behavior.

- [ ] **Step 1: Add cross-day version tests**

Add to tests/test_staffing_delivery.py:

~~~
@pytest.mark.parametrize("day", [date(2026, 7, 15), date(2026, 7, 18), date(2026, 7, 19)])
def test_every_day_uses_the_same_draft_and_posted_transition(day):
    posted = staffing.Schedule(
        day=day,
        published=True,
        assignments={"Repair 1": ["Jordan"]},
        published_delivery={"version": "v1"},
    )

    draft = staffing.draft_from_posted(posted)

    assert draft.published is False
    assert draft.published_delivery == {}
    assert draft.published_snapshot["published_delivery"]["version"] == "v1"
~~~

In tests/test_staffing_saturday_recruiting.py retain the existing pre-deadline publish rejection and add a normal save assertion: a recruiting Saturday can save a Draft but cannot publish until existing validation permits it. In tests/test_share_route.py assert record_delivery is not called when upload_pdf raises SlackError.

- [ ] **Step 2: Run cross-day and delivery tests**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_delivery.py tests/test_staffing_saturday_recruiting.py tests/test_share_route.py -q
~~~

Expected: PASS.

- [ ] **Step 3: Run complete scheduler verification**

Run:

~~~
ZIRA_API_KEY=test .venv/bin/python -m pytest tests/test_staffing_*.py tests/test_share_route.py tests/test_shift_config_saturday.py tests/test_saturday_recruiting*.py -q
git diff --check
git status --short
~~~

Expected: all tests pass, no whitespace errors, and only intended lifecycle/test changes remain.

- [ ] **Step 4: Commit final regression additions**

~~~
git add tests/test_staffing_delivery.py tests/test_staffing_saturday_recruiting.py tests/test_share_route.py
git commit -m "test: cover scheduler delivery lifecycle"
~~~
