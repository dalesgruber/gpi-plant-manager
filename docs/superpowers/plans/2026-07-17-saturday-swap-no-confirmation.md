# Saturday availability swap without confirmation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Saturday Unassigned/Off swap save after one press without a confirmation dialog.

**Architecture:** The client keeps the existing Saturday availability API and successful in-place row-move helper. The swap button invokes a new direct-save helper, which disables only that button until the request completes, moves the row after a successful response, and reports a failed request with the page's existing error toast.

**Tech Stack:** Jinja2 templates, vanilla JavaScript, CSS, pytest static-contract tests.

## Global Constraints

- Do not change the `/api/staffing/saturday-availability` API or the persisted Saturday availability mapping.
- Move the person only after an `{ok: true}` API response.
- Keep the swap button unavailable while its request is pending; on failure, leave the row in place and re-enable that button.
- Preserve the hover and keyboard-focus visibility of the swap icon.

---

## File Structure

- `src/zira_dashboard/templates/staffing.html` renders the two Saturday list controls and will no longer render a confirmation dialog.
- `src/zira_dashboard/static/staffing.js` owns the direct request, disabled state, successful left-rail move, and failure toast.
- `src/zira_dashboard/static/staffing.css` retains only swap-control styling and removes dialog-only styles.
- `tests/test_staffing_static.py` locks the rendered/static direct-save contract.

### Task 1: Save Saturday availability directly from the swap button

**Files:**
- Modify: `tests/test_staffing_static.py:87-99`
- Modify: `src/zira_dashboard/templates/staffing.html:464-474`
- Modify: `src/zira_dashboard/static/staffing.js:505-605`
- Modify: `src/zira_dashboard/static/staffing.css:264-277`

**Interfaces:**
- Consumes: a `.saturday-availability-swap` button with `data-name` and `data-destination`; `window.SCHEDULE_DAY`; `POST /api/staffing/saturday-availability` returning `{ok, unassigned_count, off_count}`.
- Produces: `_saveSaturdayAvailability(button)`, which performs one request, calls `_moveSaturdayAvailabilityRow(name, destination, data)` after a successful response, and restores the clicked button after a failed request.

- [ ] **Step 1: Write the failing static contract test**

  Replace the existing confirmation-oriented test with:

  ```python
  def test_saturday_availability_swap_is_left_rail_only_and_saves_immediately():
      html = _template()
      js = _script()
      css = _style()

      assert 'class="saturday-availability-swap"' in html
      assert 'aria-label="Move {{ n }} to Off"' in html
      assert 'aria-label="Move {{ n }} to Unassigned"' in html
      assert 'saturday-availability-confirm' not in html
      assert "/api/staffing/saturday-availability" in js
      assert "_saveSaturdayAvailability(button)" in js
      assert "button.disabled = true;" in js
      assert "showToast(error.message || 'Could not update Saturday availability.', null, 'error');" in js
      assert "showModal()" not in js[js.index("const __saturdayRecruiting"):js.index("// Partial-day off labels")]
      assert ".saturday-availability-swap { opacity: 0;" in css
      assert ".saturday-person-row:hover .saturday-availability-swap" in css
      assert ".saturday-availability-confirm" not in css
  ```

- [ ] **Step 2: Run the test to verify it fails for the current confirmation flow**

  Run: `pytest tests/test_staffing_static.py::test_saturday_availability_swap_is_left_rail_only_and_saves_immediately -v`

  Expected: FAIL because the template and CSS still contain `saturday-availability-confirm` and the script has no `_saveSaturdayAvailability(button)` direct-save path.

- [ ] **Step 3: Remove the dialog markup and dialog-only CSS**

  Delete this exact block from `src/zira_dashboard/templates/staffing.html`:

  ```html
  <dialog id="saturday-availability-confirm" class="saturday-availability-confirm" aria-labelledby="saturday-availability-confirm-title">
    <form id="saturday-availability-confirm-form" method="dialog">
      <h2 id="saturday-availability-confirm-title">Confirm availability change</h2>
      <p id="saturday-availability-confirm-message"></p>
      <p id="saturday-availability-confirm-error" role="alert" aria-live="assertive"></p>
      <div class="saturday-availability-confirm-actions">
        <button type="button" id="saturday-availability-confirm-cancel">Cancel</button>
        <button type="submit" id="saturday-availability-confirm-save">Move</button>
      </div>
    </form>
  </dialog>
  ```

  Delete the `.saturday-availability-confirm`, its `::backdrop`, descendant rules, `#saturday-availability-confirm-error`, and `.saturday-availability-confirm-actions` rules from `src/zira_dashboard/static/staffing.css`. Leave all `.saturday-availability-swap` and `.saturday-person-row` rules unchanged.

- [ ] **Step 4: Replace the confirmation state with the direct-save helper**

  Remove the six `__saturdayAvailability...` dialog/form/error/save/cancel constants, `__saturdayAvailabilityState`, `_openSaturdayAvailabilityConfirm`, and all dialog/form event listeners. Add this helper after `_moveSaturdayAvailabilityRow`:

  ```javascript
  async function _saveSaturdayAvailability(button) {
    if (__viewingPosted || button.disabled) return;
    const { name, destination } = button.dataset;
    if (!name || !destination) return;
    button.disabled = true;
    try {
      const response = await fetch('/api/staffing/saturday-availability', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          day: window.SCHEDULE_DAY,
          name,
          destination,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Could not update Saturday availability.');
      _moveSaturdayAvailabilityRow(name, destination, data);
    } catch (error) {
      showToast(error.message || 'Could not update Saturday availability.', null, 'error');
    } finally {
      button.disabled = false;
    }
  }
  ```

  Change the delegated click handler to invoke it directly:

  ```javascript
  document.addEventListener('click', event => {
    const button = event.target.closest('.saturday-availability-swap');
    if (button && __saturdayRecruiting) _saveSaturdayAvailability(button);
  });
  ```

- [ ] **Step 5: Run the direct-save static test to verify it passes**

  Run: `pytest tests/test_staffing_static.py::test_saturday_availability_swap_is_left_rail_only_and_saves_immediately -v`

  Expected: PASS.

- [ ] **Step 6: Run focused regression coverage**

  Run: `pytest tests/test_staffing_static.py tests/test_staffing_saturday_recruiting.py tests/test_saturday_recruiting_static.py -v`

  Expected: PASS (database-gated tests may report their established skip status).

- [ ] **Step 7: Commit the implementation**

  ```bash
  git add src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.js src/zira_dashboard/static/staffing.css tests/test_staffing_static.py
  git commit -m "fix: save Saturday availability swap immediately"
  ```
