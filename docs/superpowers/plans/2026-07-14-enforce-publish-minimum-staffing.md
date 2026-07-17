# Enforce Publish Minimum Staffing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep under-minimum default crews allowed, while making every publish path reject schedules that fall below any work center's configured staffing minimum.

**Architecture:** The staffing POST route becomes the source of truth for publish shortages and saves a rejected publish as a draft. The staffing GET model presents the same below-minimum state after a form redirect, while the JSON response lets the Slack workflow stop before it invokes the share endpoint. Settings remains untouched: its partial-default warning stays advisory.

**Tech Stack:** Python 3.11, FastAPI, Jinja2, vanilla JavaScript, pytest.

## Global Constraints

- The Settings default-people picker must continue to allow one selected default for a work center whose `min_ops` is two.
- Every `action=publish` request must require `len(assignments[work_center]) >= work_centers_store.min_ops(work_center)` for every configured work center; `override=1` has no effect.
- A rejected publish persists the submitted schedule as a draft and preserves an existing published version as its snapshot.
- Native forms redirect with `publish_blocked=1`; JSON publish callers receive HTTP 409 with `ok: false` and the exact shortage list.
- Do not change save, unpublish, notes-only, discard-draft, automatic scheduling, or minimum configuration behavior.

---

## File Structure

- Modify `src/zira_dashboard/routes/staffing.py`: calculate publish shortages, save failed publishes as drafts, and return a 409 JSON failure to fetch callers.
- Modify `src/zira_dashboard/staffing_view.py`: include every below-minimum row in the post-block display model, including empty and minimum-one work centers.
- Modify `src/zira_dashboard/templates/staffing.html`: remove the override publish controls and give the blocking banner an actionable instruction.
- Modify `src/zira_dashboard/static/staffing.js`: surface the server's JSON publish failure before the Post to Slack flow can call its sharing endpoint.
- Modify `src/zira_dashboard/static/staffing.css`: delete the now-unused `.override-btn` styling.
- Modify `tests/test_staffing_schedule_metadata.py`: characterize normal, JSON, zero-count, and previously-published rejected publish behavior.
- Modify `tests/test_staffing_view.py`: characterize block reasons for partial and empty work centers regardless of their minimum.
- Modify `tests/test_staffing_static.py`: protect the removal of the override UI and the JSON-aware Slack stop condition.
- Create `tests/test_settings_default_people_static.py`: protect the existing advisory partial-default picker behavior.

### Task 1: Add failing contracts for publish validation and the advisory default picker

**Files:**
- Modify: `tests/test_staffing_schedule_metadata.py`
- Modify: `tests/test_staffing_view.py`
- Modify: `tests/test_staffing_static.py`
- Create: `tests/test_settings_default_people_static.py`

**Interfaces:**
- Consumes: `_staffing_save_work(request, day, auto, form)`, `staffing.Location`, and `work_centers_store.min_ops`.
- Produces: regression contracts for rejected native/JSON publishes, the post-redirect shortage list, the non-bypassable template, and the still-advisory Settings picker.

- [ ] **Step 1: Write the failing route tests**

Add these helpers and tests after `_capture_route_save` in `tests/test_staffing_schedule_metadata.py`:

```python
def _publish_location(name, *, min_ops):
    return staffing.Location(
        name, "Repair", "Bay 1", "Recycled", None,
        min_ops=min_ops, max_ops=min_ops,
    )


def _capture_publish(monkeypatch, locs, existing=None):
    saved = []
    existing = existing or staffing.Schedule(day=DAY, published=False, assignments={})
    monkeypatch.setattr(staffing_routes.staffing, "LOCATIONS", tuple(locs))
    monkeypatch.setattr(
        staffing_routes.work_centers_store, "min_ops", lambda loc: loc.min_ops,
    )
    monkeypatch.setattr(staffing_routes.staffing, "load_schedule", lambda _day: existing)
    monkeypatch.setattr(staffing_routes.staffing, "save_schedule", saved.append)
    monkeypatch.setattr(staffing_routes._http_cache, "invalidate_today_cache", lambda: None)
    return saved


def test_publish_override_cannot_bypass_two_person_minimum(monkeypatch):
    pair = _publish_location("Hand Build #1", min_ops=2)
    saved = _capture_publish(monkeypatch, [pair])

    response = staffing_routes._staffing_save_work(
        SimpleNamespace(headers={}), DAY, 0,
        FormData([
            ("action", "publish"),
            ("loc__Hand Build #1", "Jordan"),
            ("override", "1"),
        ]),
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/staffing?day={DAY.isoformat()}&publish_blocked=1"
    assert saved[0].published is False
    assert saved[0].assignments == {"Hand Build #1": ["Jordan"]}


def test_publish_blocks_an_empty_one_person_work_center(monkeypatch):
    solo = _publish_location("Junior #1", min_ops=1)
    saved = _capture_publish(monkeypatch, [solo])

    staffing_routes._staffing_save_work(
        SimpleNamespace(headers={}), DAY, 0, FormData({"action": "publish"}),
    )

    assert saved[0].published is False


def test_json_publish_below_minimum_returns_conflict_with_shortages(monkeypatch):
    pair = _publish_location("Hand Build #1", min_ops=2)
    saved = _capture_publish(monkeypatch, [pair])

    response = staffing_routes._staffing_save_work(
        SimpleNamespace(headers={"accept": "application/json"}), DAY, 0,
        FormData([("action", "publish"), ("loc__Hand Build #1", "Jordan")]),
    )

    assert response.status_code == 409
    assert response.body == (
        b'{"ok":false,"error":"Publish blocked — staff every work center to its minimum.",'
        b'"publish_block_reasons":["Hand Build #1 requires 2 operators — currently 1."]}'
    )
    assert saved[0].published is False


def test_failed_republish_preserves_the_posted_version_as_a_snapshot(monkeypatch):
    pair = _publish_location("Hand Build #1", min_ops=2)
    posted = staffing.Schedule(
        day=DAY, published=True, assignments={"Hand Build #1": ["Jordan", "Taylor"]},
    )
    saved = _capture_publish(monkeypatch, [pair], existing=posted)

    staffing_routes._staffing_save_work(
        SimpleNamespace(headers={}), DAY, 0,
        FormData([("action", "publish"), ("loc__Hand Build #1", "Jordan")]),
    )

    assert saved[0].published is False
    assert saved[0].published_snapshot == staffing.snapshot_of(posted)
```

- [ ] **Step 2: Run the route tests to verify they fail**

Run: `pytest tests/test_staffing_schedule_metadata.py -q`

Expected: FAIL because `override=1` still publishes, zero-assigned `min_ops=1` is skipped, the JSON response is HTTP 200 with `ok: true`, and a failed re-publish does not create a posted snapshot.

- [ ] **Step 3: Write the failing display and client contracts**

Replace the two existing publish-block tests in `tests/test_staffing_view.py` with this one:

```python
def test_publish_block_reasons_include_every_below_minimum_work_center(patch_wcs):
    pair = _loc("Hand Build #1", bay="Bay 6", min_ops=2, max_ops=2, required=("Repair",))
    solo = _loc("Junior #1", bay="Bay 16", min_ops=1, max_ops=1, required=("Repair",))
    patch_wcs([
        (pair, {"required": ("Repair",), "min": 2, "max": 2, "defaults": []}),
        (solo, {"required": ("Repair",), "min": 1, "max": 1, "defaults": []}),
    ])

    model = staffing_view.build_staffing_bays(
        roster=[_person("Jordan", Repair=3)],
        sched=_sched({"Hand Build #1": ["Jordan"]}),
        time_off_entries=[],
        publish_blocked=1,
    )

    assert model["publish_block_reasons"] == [
        "Hand Build #1 requires 2 operators — currently 1.",
        "Junior #1 requires 1 operators — currently 0.",
    ]
```

Change `test_staffing_publish_submit_buttons_expose_busy_state` in
`tests/test_staffing_static.py` to assert the normal publish busy-state
contract while rejecting the old override markup, and add this client-flow
contract:

```python
def test_staffing_publish_banner_has_no_override_and_slack_stops_on_json_failure():
    html = _template()
    js = _script()
    slack_post = js.split("async function postToSlack(btn) {", 1)[1].split(
        "// ---------- Rotation goal", 1,
    )[0]

    assert "Override &amp; Publish" not in html
    assert "publish-override" not in html
    assert 'class="override-btn' not in html
    assert "if (!pubRes.ok)" in slack_post
    assert slack_post.index("if (!pubRes.ok)") < slack_post.index("// Step 2: post the resulting PDF to Slack.")
```

Create `tests/test_settings_default_people_static.py` with this contract:

```python
from pathlib import Path


def test_partial_default_crew_remains_an_advisory_warning():
    js = Path("src/zira_dashboard/static/settings.js").read_text()

    assert "checked > 0 && checked < min" in js
    assert "title: loc + ' · Fewer than min'" in js
    assert "overrideLabel: 'OK'" in js
    assert "onCancel: () => { picker.open = true; }" in js
```

- [ ] **Step 4: Run the display/client contracts to verify they fail**

Run: `pytest tests/test_staffing_view.py tests/test_staffing_static.py tests/test_settings_default_people_static.py -q`

Expected: FAIL because the current display omits the empty one-person center,
the template still renders the override button, and Slack checks only the HTTP
status without parsing the JSON publish error.

### Task 2: Enforce the minimum in the server-side publish path

**Files:**
- Modify: `src/zira_dashboard/routes/staffing.py`
- Test: `tests/test_staffing_schedule_metadata.py`

**Interfaces:**
- Consumes: submitted `assignments`, `staffing.LOCATIONS`, and `work_centers_store.min_ops(loc)`.
- Produces: `_publish_shortages(assignments) -> list[str]` and an HTTP 409 JSON response with `{ok, error, publish_block_reasons}` when publishing cannot proceed.

- [ ] **Step 1: Add the pure shortage formatter**

Add this helper directly below `_effective_minimum` in
`src/zira_dashboard/routes/staffing.py`:

```python
def _publish_shortages(assignments: dict[str, list[str]]) -> list[str]:
    """Return each configured work center whose submitted crew is below minimum."""
    shortages = []
    for loc in staffing.LOCATIONS:
        minimum = _effective_minimum(loc)
        count = len(assignments.get(loc.name, []))
        if count < minimum:
            shortages.append(
                f"{loc.name} requires {minimum} operators — currently {count}."
            )
    return shortages
```

- [ ] **Step 2: Replace override-aware partial validation with the helper**

In `_staffing_save_work`, delete:

```python
    override = (form.get("override") or "").strip() == "1"
```

Replace the current `Publish-only block` loop with:

```python
    publish_block = _publish_shortages(assignments) if action == "publish" else []
```

Do not read or branch on `override`; a stale or manually constructed
`override=1` value must be unable to affect publishing.

- [ ] **Step 3: Make failed publishes drafts and preserve a posted snapshot**

Replace the published-state and snapshot branching with this logic after
`existing = staffing.load_schedule(d)` and the early notes/discard returns:

```python
    if publish_block:
        published = False
    elif action == "publish":
        published = True
    elif action == "unpublish":
        published = False
    else:
        published = existing.published

    published_snapshot = existing.published_snapshot
    if action == "publish":
        if publish_block:
            if existing.published:
                published_snapshot = staffing.snapshot_of(existing)
        else:
            published_snapshot = None
    elif existing.published and published_snapshot is None:
        published_snapshot = staffing.snapshot_of(existing)
        published = False
```

Keep the existing `staffing.save_schedule(...)` call immediately after this
logic so a rejected request retains the user's submitted assignments as the
new draft.

- [ ] **Step 4: Return a conflict response to JSON callers after saving**

Replace the current combined auto/JSON response branch with:

```python
    wants_json = auto or (request.headers.get("accept") or "").startswith("application/json")
    if publish_block and wants_json:
        return JSONResponse(
            {
                "ok": False,
                "error": "Publish blocked — staff every work center to its minimum.",
                "publish_block_reasons": publish_block,
            },
            status_code=409,
        )
    if wants_json:
        return JSONResponse({"ok": True, "published": published, "testing_day": testing_day})
```

Leave the existing normal-form `publish_blocked=1` redirect below it intact.

- [ ] **Step 5: Run the focused route tests to verify they pass**

Run: `pytest tests/test_staffing_schedule_metadata.py -q`

Expected: PASS, including the new under-minimum native, JSON, empty-center,
and failed-republish regression tests.

- [ ] **Step 6: Commit the server enforcement**

```bash
git add src/zira_dashboard/routes/staffing.py tests/test_staffing_schedule_metadata.py
git commit -m "fix: enforce minimum staffing before publish"
```

### Task 3: Remove the UI bypass and present all shortages

**Files:**
- Modify: `src/zira_dashboard/staffing_view.py`
- Modify: `src/zira_dashboard/templates/staffing.html`
- Modify: `src/zira_dashboard/static/staffing.css`
- Modify: `src/zira_dashboard/static/staffing.js`
- Test: `tests/test_staffing_view.py`
- Test: `tests/test_staffing_static.py`
- Test: `tests/test_settings_default_people_static.py`

**Interfaces:**
- Consumes: the saved draft rendered after `publish_blocked=1` and the 409 JSON payload from `/staffing`.
- Produces: a banner containing every below-minimum center, no override control, and a Slack workflow that ends before `/staffing/share-to-slack` when publishing fails.

- [ ] **Step 1: Expand post-block display reasons**

In `src/zira_dashboard/staffing_view.py`, replace the current
`hc_status == "under" and r["min_ops"] >= 2` gate with:

```python
                if len(r["assigned"]) < r["min_ops"]:
                    publish_block_reasons.append(
                        f"{r['loc'].name} requires {r['min_ops']} operators — currently {len(r['assigned'])}."
                    )
```

The `publish_blocked` outer gate remains unchanged. Using `assigned` here
matches the route's submitted-assignment count, so the redirect banner and
the server decision report identical values.

- [ ] **Step 2: Remove the override markup and dead styling**

Replace the override `<div>` inside the `publish_block_reasons` banner in
`src/zira_dashboard/templates/staffing.html` with:

```html
  <p style="margin:0.4rem 0 0;font-size:0.8rem;color:var(--muted)">
    Schedule every work center to its configured minimum, then publish again.
  </p>
```

Delete the entire `.override-btn` and `.override-btn:hover` rules from
`src/zira_dashboard/static/staffing.css`. Retain the generic `.save-block`
styling because the block banner continues to be rendered.

- [ ] **Step 3: Surface the JSON failure before posting to Slack**

In `postToSlack` in `src/zira_dashboard/static/staffing.js`, replace:

```javascript
      if (pubRes.status >= 400) {
        throw new Error('Publish failed: HTTP ' + pubRes.status);
      }
```

with:

```javascript
      if (!pubRes.ok) {
        const data = await pubRes.json().catch(() => ({}));
        throw new Error(data.error || ('Publish failed: HTTP ' + pubRes.status));
      }
```

Keep this condition before the `// Step 2: post the resulting PDF to Slack.`
section. That control flow ensures a 409 has no Slack side effect and gives
the scheduler user the precise staffing error.

- [ ] **Step 4: Run the display and client contract tests to verify they pass**

Run: `pytest tests/test_staffing_view.py tests/test_staffing_static.py tests/test_settings_default_people_static.py -q`

Expected: PASS. The default-picker test confirms the one-of-two default
warning remains advisory; the scheduler tests confirm every below-minimum
work center is named and no override UI survives.

- [ ] **Step 5: Commit the UI and client enforcement**

```bash
git add src/zira_dashboard/staffing_view.py src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.css src/zira_dashboard/static/staffing.js tests/test_staffing_view.py tests/test_staffing_static.py tests/test_settings_default_people_static.py
git commit -m "fix: remove publish staffing override"
```

### Task 4: Run the complete verification set

**Files:**
- Verify: `src/zira_dashboard/routes/staffing.py`
- Verify: `src/zira_dashboard/staffing_view.py`
- Verify: `src/zira_dashboard/templates/staffing.html`
- Verify: `src/zira_dashboard/static/staffing.js`
- Verify: `src/zira_dashboard/static/staffing.css`
- Verify: `tests/test_staffing_schedule_metadata.py`
- Verify: `tests/test_staffing_view.py`
- Verify: `tests/test_staffing_static.py`
- Verify: `tests/test_settings_default_people_static.py`

**Interfaces:**
- Consumes: the finished server, render model, template, and browser script.
- Produces: fresh evidence that the no-bypass rule works and surrounding Staffing behavior remains intact.

- [ ] **Step 1: Run all touched test modules**

Run: `pytest tests/test_staffing_schedule_metadata.py tests/test_staffing_view.py tests/test_staffing_static.py tests/test_settings_default_people_static.py -q`

Expected: PASS with no failures.

- [ ] **Step 2: Run the broader Staffing regression suite**

Run: `pytest tests/test_staffing_*.py -q`

Expected: PASS with no failures.

- [ ] **Step 3: Run static analysis on changed Python modules**

Run: `ruff check src/zira_dashboard/routes/staffing.py src/zira_dashboard/staffing_view.py tests/test_staffing_schedule_metadata.py tests/test_staffing_view.py tests/test_staffing_static.py tests/test_settings_default_people_static.py`

Expected: `All checks passed!`

- [ ] **Step 4: Inspect the final scope**

Run: `git status --short && git log -2 --oneline`

Expected: only the two implementation commits are new for this feature, and
unrelated pre-existing working-tree changes remain unstaged and untouched.
