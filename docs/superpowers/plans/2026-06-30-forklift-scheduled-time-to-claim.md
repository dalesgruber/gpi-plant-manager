# Forklift Scheduled Time-to-Claim Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Forklift bay cell predict time-to-claim from the actually scheduled drivers while keeping the suggested driver count visible.

**Architecture:** Reuse the existing calibrated Erlang-C queue model in `forklift_queue.py`. `forklift_advisor.build_advisor()` continues to compute the SLA recommendation, then separately computes scheduled-day predicted claim time from the scheduled driver count and exposes status fields for the template.

**Tech Stack:** Python, pytest, Jinja2 templates, existing static CSS.

## Global Constraints

- Keep `N Suggested` visible in the compact Forklift bay cell.
- Display `Predicted Time-to-Claim x.x` using the scheduled driver count.
- Remove check, warning, and `!!` symbols while keeping color coding.
- Color by scheduled prediction: green at or below target, yellow up to 1.5x target, red above 1.5x target or overloaded/unbounded.
- Do not change the underlying Erlang-C queue model, scheduled-count derivation, settings preview, or database schema.

---

### Task 1: Advisor Scheduled Prediction

**Files:**
- Modify: `src/zira_dashboard/forklift_advisor.py`
- Test: `tests/test_forklift_advisor.py`

**Interfaces:**
- Consumes: `forklift_queue.erlang_c_wait_seconds(c: int, lambda_per_hr: float, mean_handle_seconds: float) -> float`
- Produces: advisor fields `predicted_scheduled_claim_seconds: float | None`, `scheduled_prediction_overloaded: bool`, `scheduled_prediction_status: str`

- [x] **Step 1: Write failing advisor tests**

Add tests proving `recommended` is still target-sized while scheduled prediction uses `scheduled`, and overloaded/zero scheduled becomes red.

- [x] **Step 2: Run focused failing tests**

Run: `PYTHONPATH=src pytest tests/test_forklift_advisor.py -q`

- [x] **Step 3: Implement advisor fields**

Add helper functions in `forklift_advisor.py` for scheduled prediction and status. Use the same demand percentile, handle time, and calibration factor as the recommendation.

- [x] **Step 4: Run focused advisor tests**

Run: `PYTHONPATH=src pytest tests/test_forklift_advisor.py -q`

### Task 2: Compact Bay Cell Rendering

**Files:**
- Modify: `src/zira_dashboard/templates/staffing.html`
- Modify: `src/zira_dashboard/static/staffing.css`
- Test: `tests/test_staffing_forklift_card.py`

**Interfaces:**
- Consumes: advisor fields from Task 1
- Produces: compact bay cell with no symbol span and spaced `Predicted Time-to-Claim x.x`

- [x] **Step 1: Update failing template tests**

Assert symbols are absent, `Predicted Time-to-Claim x.x` appears, and status classes still render.

- [x] **Step 2: Run focused failing tests**

Run: `PYTHONPATH=src pytest tests/test_staffing_forklift_card.py -q`

- [x] **Step 3: Implement template and CSS**

Remove `.forklift-bay-status` output, switch status source to `scheduled_prediction_status`, and add spacing for the prediction line.

- [x] **Step 4: Run focused template tests**

Run: `PYTHONPATH=src pytest tests/test_staffing_forklift_card.py -q`

### Task 3: Verification

**Files:**
- Verify: `src/zira_dashboard/forklift_advisor.py`
- Verify: `src/zira_dashboard/templates/staffing.html`
- Verify: `src/zira_dashboard/static/staffing.css`
- Verify: `tests/test_forklift_advisor.py`
- Verify: `tests/test_staffing_forklift_card.py`

- [x] **Step 1: Run focused forklift/staffing tests**

Run: `PYTHONPATH=src pytest tests/test_forklift_advisor.py tests/test_staffing_forklift_card.py -q`

- [x] **Step 2: Compile the staffing template**

Run: `PYTHONPATH=src python -c "from zira_dashboard.deps import templates; templates.get_template('staffing.html'); print('ok')"`

- [x] **Step 3: Review git diff**

Run: `git diff -- src/zira_dashboard/forklift_advisor.py src/zira_dashboard/templates/staffing.html src/zira_dashboard/static/staffing.css tests/test_forklift_advisor.py tests/test_staffing_forklift_card.py`
