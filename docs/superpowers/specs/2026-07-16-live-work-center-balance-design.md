# Live Work-Center Balance Design

## Goal

Make the scheduler's work-center On/Off recommendation update immediately as
the planner changes work-center toggles. Make a balanced result (zero) green
and every nonzero result red.

## Scope

This is a browser-only presentation correction. It does not alter saved
work-center state, API payloads, scheduler placement rules, or the existing
minimum-crew balance calculation on the server.

## Design

The staffing page will expose a small client-side refresh function for the
minimum-crew balance summary. After a work-center toggle succeeds, the
existing row state is used to recalculate the displayed recommendation and
numeric delta immediately, rather than retaining the pre-toggle summary.

The summary element receives a semantic state class:

- `is-balanced` when the computed delta is exactly zero; it renders green.
- `is-unbalanced` when the delta is above or below zero; it renders red.

The current recommendation copy remains unchanged: nonzero results instruct
the planner to turn a number of work centers On or Off, while zero renders the
existing ready/balanced copy. Server data remains authoritative on initial
page load and after the save response; client-side recomputation only keeps
the visible value synchronized with the just-applied row state.

## Failure Handling

The indicator changes only after a successful toggle save. When a save fails,
the existing rollback leaves both the row state and summary unchanged.

## Verification

Focused frontend-contract tests will assert that toggle handling refreshes the
summary and that the stylesheet declares the green balanced and red unbalanced
states. The focused scheduler test suite will then be run.
