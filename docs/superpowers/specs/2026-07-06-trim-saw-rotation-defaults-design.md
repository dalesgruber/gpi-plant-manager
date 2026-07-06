# Trim Saw Rotation Defaults Design

## Summary

Add a focused smart-default behavior for `Trim Saw 1` on the staffing scheduler. The scheduler should auto-fill Trim Saw from defaults plus a rotation suggestion, while still letting Dale override the assignment manually on any day.

This is version 1 for Trim Saw only. The scoring and pairing logic should be implemented as a small backend module so other rotating work centers can use the same pattern later.

## Goals

- Stop requiring the scheduler to look back through past schedules to know who recently worked Trim Saw.
- Keep the best Trim Saw operators working the position more often than newer or weaker operators.
- Preserve default people as pinned starting points when they are available.
- Enforce safe pairings based on Trim Saw skill levels.
- Auto-fill new days and reset-to-defaults flows, but never fight a manual override on an already-saved day.

## Non-Goals

- No generic rotation settings UI in this version.
- No automatic reshuffling after every manual picker change.
- No changes to the skill matrix model.
- No changes to how non-Trim Saw defaults work.

## Current Context

The scheduler already has the required data:

- `staffing.LOCATIONS` contains `Trim Saw 1`, with min and max operators set to 2.
- The roster loads active people and their numeric skill levels from `people`, `skills`, and `person_skills`.
- Work-center defaults come from `work_center_default_people`.
- Daily assignments are stored in `schedules` and `schedule_assignments`.
- `GET /staffing` seeds a brand-new empty day from per-work-center defaults.
- Successful publish can pre-fill the next working day from defaults.
- The `Reset to defaults` button is client-side and currently uses `window.DEFAULTS_BY_LOC`.
- Time off entries are already available to the staffing route and render model.

## Behavior

`Trim Saw 1` becomes the only smart-default work center in this version.

The app builds smart Trim Saw defaults in these moments:

1. A brand-new day is opened and has no saved assignments.
2. A successful publish pre-fills the next working day.
3. The user clicks `Reset to defaults`.

The app does not auto-change Trim Saw on an existing saved day unless the user clicks `Reset to defaults`. Manual edits remain the source of truth for that day.

## Availability

A person is available for Trim Saw smart defaults only when they are:

- Active on the roster.
- Not a reserve.
- Not already assigned to another non-Trim Saw work center in the default assignment map being built.
- Not on full-day time off for the target day.

Partial-day time off does not exclude the person. It remains visible through existing scheduler badges.

## Pinned Defaults

Stored default people for `Trim Saw 1` are treated as pinned first choices.

- If a pinned default is available, keep them in Trim Saw.
- If a pinned default is unavailable, skip them and fill the slot with a suggestion.
- If more than two defaults are stored, keep the first two available defaults in stored order.
- If a pinned default has Trim Saw level 0 or 1, they may stay only if paired with a level 3 operator.

## Pairing Rules

The final Trim Saw pair must satisfy these rules:

- A level 3 operator can pair with level 1, 2, or 3.
- A level 2 operator must pair with level 2 or 3.
- A level 1 or level 0 operator must pair with level 3.

Equivalently:

- If either person is level 0 or 1, the other person must be level 3.
- Otherwise, the lowest allowed pair is level 2 plus level 2.

If the app cannot find a valid two-person pair, it should return the best safe partial assignment and leave the remaining slot empty. It should not assign an unsafe pair.

## History Window

Rotation history looks back from the target day and uses the most recent 20 saved schedule rows before that day.

History counting rules:

- Only dates before the target day count.
- Testing days do not count.
- If a past day has a published snapshot because it was edited after posting, count the published snapshot for that day.
- Otherwise, count the saved assignments for that day.
- Count appearances on `Trim Saw 1` only.
- If fewer than 20 saved schedules exist, use all available history.

This keeps the suggestion based on recent reality without permanently penalizing someone for older assignments.

## Scoring

The suggestion engine ranks available candidates with two signals:

- Skill strength: level 3 is preferred over level 2, level 2 over level 1, and level 0 is only used when pinned or when no safer candidate exists.
- Due-ness: people with fewer Trim Saw assignments in the 20-day window rank higher than people who worked it more recently or more often.

Recommended first-pass weighting:

- Level 3 base weight: 100.
- Level 2 base weight: 70.
- Level 1 base weight: 25.
- Level 0 base weight: 0 unless pinned.
- Subtract 12 points for each Trim Saw appearance in the history window.
- Subtract an additional 8 points if the person worked Trim Saw on the most recent counted schedule.

These numbers intentionally make level 3 operators show up more often, while still allowing level 2 operators to rotate in when they are due.

Tie-breakers:

1. Higher score.
2. Higher Trim Saw skill level.
3. Fewer history appearances.
4. Alphabetical by name for deterministic output.

## Pair Selection

The engine should search valid pairs rather than greedily choosing one person at a time.

Process:

1. Build the pinned list from available Trim Saw defaults.
2. Build the candidate list from available active non-reserve people.
3. If there are two pinned defaults, validate the pair. If invalid, keep the strongest pinned person and choose the best compatible partner.
4. If there is one pinned default, choose the highest-ranked compatible partner.
5. If there are no pinned defaults, choose the highest-ranked valid pair, favoring at least one level 3 anchor when available.
6. Return up to two names in the intended schedule order.

## Backend Shape

Add a new helper module such as `src/zira_dashboard/rotation_suggestions.py`. The module should keep scoring and pair selection independent from FastAPI and templates; database access is limited to the bounded history lookup.

Suggested public functions:

- `smart_defaults_for_day(day, roster, base_assignments, time_off_entries) -> dict[str, list[str]]`
- `suggest_trim_saw_pair(day, roster, pinned_names, unavailable_names) -> list[str]`

`smart_defaults_for_day` should copy the provided assignment map, replace only `Trim Saw 1` with the smart pair, and return the new map. Other work centers remain unchanged.

The module can query recent schedules directly or use `staffing.load_schedules_bulk`, but it should do the history lookup in a bounded set-based way. It should avoid loading every historical schedule into the per-day schedule cache.

## Route Integration

`GET /staffing` should call smart defaults only when the day had no saved assignments and the route is seeding defaults. The route already has roster and time-off entries available.

The successful publish path should use the same helper when it pre-fills the next working day. Because pairing must respect full-day absences, that path should fetch time-off entries for the next day before calling the helper. If the fetch fails, pass an empty list and rely on the existing fallback behavior.

The template should expose `SMART_DEFAULTS_BY_LOC` alongside `DEFAULTS_BY_LOC`. For all work centers except `Trim Saw 1`, smart defaults match stored defaults. For `Trim Saw 1`, smart defaults contain the computed pair for the current day.

## Client Integration

The existing `Reset to defaults` handler in `staffing.js` should use `window.SMART_DEFAULTS_BY_LOC || window.DEFAULTS_BY_LOC`.

That preserves current reset behavior for every other work center while letting Trim Saw reset to the computed smart pair.

Manual picker changes continue to work as they do today:

- The scheduler may choose different people.
- Autosave persists the manual assignment.
- The next page load of that saved day shows the manual assignment.

## Error Handling

If history lookup fails, the app should fall back to skill-based suggestions from the current roster and pinned defaults.

If skill data is missing for a defaulted person, treat the person as level 0 for pairing safety.

If the final safe pair cannot be completed, leave Trim Saw partially filled rather than assigning an unsafe partner.

If the helper raises unexpectedly in the route, the route should fall back to existing stored defaults so the scheduler page still loads.

## Testing

Add focused unit tests for the new rotation module:

- Level 3 default can pair with level 1, 2, or 3.
- Level 2 default only pairs with level 2 or 3.
- Level 1 or 0 default requires a level 3 partner.
- Recent history reduces a candidate's rank.
- Level 3 operators still outrank level 2 operators when both are similarly due.
- Unavailable and reserve people are excluded.
- Invalid two-default pair is repaired by choosing a compatible partner.
- No safe pair returns a partial assignment instead of an unsafe pair.

Add route or render-model coverage for:

- Brand-new day seeding uses smart Trim Saw defaults.
- Successful publish pre-fills the next day with smart Trim Saw defaults.
- Template context includes `SMART_DEFAULTS_BY_LOC`.

Add client coverage only if the project has existing JS test infrastructure. Otherwise keep the JS change small and verify manually with a browser or rendered template.

## Future Extension

The Trim Saw implementation should keep configuration constants isolated:

- Work-center name.
- Skill name.
- Lookback size.
- Skill weights.
- Pairing rule.

That makes a later generic rotation engine possible without building a full settings system now.
