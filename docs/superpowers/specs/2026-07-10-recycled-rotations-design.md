# Recycled Smart Rotations Design

## Purpose

Generalize the existing Trim Saw smart-default logic into one Recycled-area rotation engine. It will schedule Dismantler, Repair, and Trim Saw operators with safe skill coverage, person-level role preferences, day-level scheduling goals, and fair rotation across the work centers within each area.

The design also introduces a focused level-0 training block. It is separate from the daily Training schedule goal: a block gives a person a planned multi-day ramp-up in one rotation group and promotes them to level 1 when completed.

## Goals

1. Suggest safe Recycled assignments for Dismantler, Repair, and Trim Saw.
2. Give each person a soft preference per group: `primary`, `regular`, `occasional`, or `never`.
3. Offer three persisted schedule goals: `optimized`, `normal` (default), and `training`.
4. Spread a person evenly across the individual centers in their group; e.g. Repair 1 → Repair 2 → Repair 3 instead of repeatedly choosing Repair 1.
5. Support multi-day level-0 training blocks: a green trainer on day one, independent group assignment for the remaining attended workdays, and absence-driven extension.
6. Promote a completed trainee from level 0 to level 1 for the target skill.
7. Preserve manual assignments and make recommendation behavior explainable.

## Non-goals

- Replace the existing scheduler or alter non-Recycled default behavior.
- Build exact weekly quota or percentage targets per person.
- Change skill-level definitions or build a generic score-weight editor.

## Existing Integration Points

- `src/zira_dashboard/rotation_suggestions.py` currently supplies the Trim-Saw-only smart default. It becomes the pure generic recommendation engine while preserving its public Trim Saw behavior during migration.
- `src/zira_dashboard/routes/staffing.py` seeds empty days and the next working day after publish through `_smart_defaults_for_day`.
- `src/zira_dashboard/staffing.py::LOCATIONS` supplies authoritative Recycled locations, required skills, and min/max staffing.
- `people`, `skills`, `person_skills`, `schedules`, and schedule assignments already persist the roster, skill levels, and history. The current skill update path in `routes/skills.py` remains the promotion integration point.

## Data Model

### Rotation groups

Define Dismantler, Repair, and Trim Saw from Recycled locations and their required skill. A group can contain several individual work centers. The recommender chooses a person for the group first and then chooses the specific center.

### Person preferences

Add an app-owned `person_rotation_preferences` table keyed by `(person_id, rotation_group)` with `preference` constrained to `primary`, `regular`, `occasional`, or `never`.

Missing rows mean `regular`, keeping existing people immediately eligible. `never` prohibits automatic placement in the group but still permits a manager to assign the person manually.

### Per-day mode and assignment source

Store the selected Recycled mode on the schedule, defaulting to `normal`. Keep assignment-source metadata so an explicit manual assignment is distinguishable from a generated one. A rebuild changes only generated assignments; it never replaces a manual lock.

### Training blocks

Add an app-owned `rotation_training_blocks` record with trainee, target rotation group/skill, start date, planned attended-workday count, day-one green trainer, status, completed attended-day count, and completion metadata. It references people and the existing skill instead of duplicating skill levels.

The block derives active days from working days and full-day absences. An absent day does not consume a training day, so the block naturally extends until the requested number of attended days is reached. On its later active days, the block is a scoped eligibility exception for the level-0 trainee; it does not change `person_skills` until successful completion.

## Recommendation Pipeline

For a new or explicitly rebuilt Recycled schedule:

1. Preserve all manual locks.
2. Apply active level-0 training blocks. On the first attended day, place the trainee and designated green (level-3) trainer in the target group. This supervised pair may temporarily exceed ordinary center staffing. On later attended days, reserve the trainee in the group as a normal operator slot.
3. Select sufficient green coverage and satisfy work-center min/max staffing.
4. Rank remaining eligible people according to the selected schedule mode.
5. For each selected person/group combination, choose the eligible center they worked least often and least recently in the bounded history window. This produces an even Repair 1 → 2 → 3 cycle when coverage permits.
6. Return the suggestion with per-assignment reasons. The scheduler displays reasons and never silently changes a saved manual choice.

History uses posted schedule data when it exists, otherwise the past draft, following the current Trim Saw convention. It excludes testing days and uses a bounded recent schedule window for predictable runtime.

## Schedule Modes

All modes enforce absence, one-person-one-location, training-block, and green-coverage constraints. A mode changes only valid-candidate ranking.

| Mode | Ranking priority |
|---|---|
| `optimized` | Maximize level-3 coverage across all Recycled openings, then use the highest available skill. Preferences and rotation history break close ties. |
| `normal` | Maintain green coverage, then give comparable weight to group preference, time since the group, and center-level rotation fairness. |
| `training` | Maintain the same green coverage, then deliberately select level-1 and level-2 people for development placements paired with a level-3 operator. Cap these placements per day; default to two and expose the cap as a small Recycled setting. |

Level 0 is never introduced by the daily `training` mode. It is eligible only through an active training block.

## UI

### Staffing scheduler

Show a compact Recycled goal control—Optimized, Normal, Training—on Staffing. Explain the active goal and provide a `Reset non-manual assignments` action. It recomputes only generated Recycled assignments and leaves manual locks intact.

Each Recycled assignment shows its reason, such as “green coverage,” “least-recent Repair center,” “primary Repair operator,” or “training pair.” Unresolvable coverage renders as a clear warning, never as an unsafe automatic assignment.

### People settings

Add a Recycled Rotation section to existing people/skills management. It presents three selectors—Dismantler, Repair, Trim Saw—for Primary, Regular, Occasional, and Never.

### Training-block workflow

Provide an explicit `Start training block` form: trainee, target group, start date, attended workdays, and day-one green trainer. Creation is rejected if the trainee is not level 0, the trainer is not level 3 in the target skill, either is unavailable on day one, or a conflicting active block exists. Display active blocks and remaining attended days. Managers can pause or end early; a manual override on one day warns and locks that day rather than silently defeating it.

On completion, use the existing skill write/mirroring path to set the target skill to level 1. If external synchronization cannot complete, retain the local promotion in its normal dirty/pending state and report the pending external sync.

## Error Handling

- Do not start a block without a valid day-one green trainer.
- A full-day absence skips the training day and extends the block; show the extension in the scheduler.
- If a later active-block day cannot safely be staffed, leave the unresolved assignment visible with an actionable warning instead of choosing an unsafe substitute.
- If a manual lock conflicts with an active block, preserve the manual choice and mark that block day as needing manager attention.
- A failed history or preference read falls back to normal eligible candidates and retains the current Trim Saw fallback behavior.

## Verification

Add focused unit tests for:

1. preference ordering and `never` exclusion;
2. optimized, normal, and training rankings;
3. level-1/2 training placements requiring a level-3 partner;
4. level-0 exclusion outside a block;
5. first-day trainer pairing, subsequent-day reservation, absence extension, conflict detection, early stop, and exactly-once level-1 promotion;
6. fair center selection across Repair and Dismantler histories;
7. manual-lock preservation when recomputing suggestions; and
8. schema/store plus Staffing route/template/API contracts.

Add route/UI tests for the mode selector, preference editor, block form, warnings, and non-manual reset path. Regression-test existing Trim Saw smart defaults so its pairing guarantees and next-day seeding remain intact.

## Acceptance Criteria

- A manager can set a different soft group preference for every person.
- Normal scheduling does not repeatedly place a qualified Repair person at the same Repair center when comparable centers are open and recent history calls for another center.
- Optimized scheduling places as much level-3 Recycled coverage as available.
- Training scheduling develops up to the configured number of level-1/2 people while keeping level-3 coverage.
- A level-0 trainee is paired with a chosen green trainer on their first attended day, reserved for the group on later attended days, receives all requested attended days despite absences, and becomes level 1 on completion.
- Manual staffing decisions are never overwritten by a suggested rebuild.
