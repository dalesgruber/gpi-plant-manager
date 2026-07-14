# Reset Default-Group Balancing Design

## Goal

When Reset to defaults schedules people assigned to a default group, spread
them across that group's enabled Auto work centers instead of allowing them to
accumulate at one center.

## Scope

Exact work-center defaults stay pinned to their saved center. This change
affects only default-group people in the reset-only scheduling path; ordinary
automatic rebuilds and manual assignments are unchanged.

## Scheduling rule

1. Build exact defaults first, retaining their current active/non-reserve/
   not-full-day-absent checks.
2. For each default group, consider only that group's enabled Auto centers.
3. Place each eligible, not-already-assigned group-default person at a center
   with the smallest current assignment count, including exact defaults and
   earlier group-default placements from this same reset.
4. Exclude a center that has reached its configured maximum operators. If no
   candidate has capacity, leave that default-group person unscheduled.
5. If several centers have the same current count, use the existing
   per-person rotation-history selector to prefer the center the person has
   used least and avoid their previous center. A stable center-name sort makes
   any remaining tie deterministic.

The count-first rule prevents a batch of people with identical history from
all selecting Repair 1. History still rotates individual people fairly when
the group has multiple equally balanced centers.

## Implementation

Extend the reset-only assignment builder in `routes/rotations.py` with the
configured capacity map. Its group-default candidate chooser will receive the
current assignments, filter full centers, choose the least-loaded candidate,
then apply the existing `choose_center` rule only within that tied set.

## Tests

- Multiple Repair group defaults distribute across enabled Repair centers
  rather than all landing in Repair 1.
- A pinned exact default counts toward balancing and capacity.
- A full center is skipped, and a person remains unscheduled only when every
  eligible group center is full.
- Existing reset behavior for unavailable and disabled centers remains intact.

## Review

No placeholders or ambiguous behavior remain: balancing means the least
currently assigned eligible center, capacity is a hard limit, and exact
defaults remain pinned.
