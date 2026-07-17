# Saturday Home Schedule Banner — Design

**Date:** 2026-07-17
**Status:** Approved

## Goal

Keep the shared timeclock home screen informed about an upcoming or in-progress
Saturday shift without implying that employees can still volunteer after the
recruiting deadline. When a final Saturday schedule exists, let employees open
the header to see the official work-center assignments.

## Banner lifecycle

The home screen considers the nearest non-cancelled Saturday recruitment whose
shift has not ended. Its presentation is derived from the server-authoritative
plant time, the recruitment deadline, and the snapshotted Saturday shift end:

| Period | Header copy | Visibility |
| --- | --- | --- |
| Before the response deadline, while compatible openings remain | `Saturday Work Available` / `Trabajo disponible el sábado`, with the existing deadline and openings note | Existing recruiting behavior |
| From the response deadline until midnight before Saturday | `Saturday planned for tomorrow` | Always shown, even though recruiting is closed or all openings were filled |
| From the start of Saturday until the snapshotted Saturday shift end | `Saturday planned for today` | Always shown |
| After the snapshotted Saturday shift end, or after the Saturday is cancelled | No Saturday header | Hidden |

The Friday 7:00 AM example is not hard-coded: the deadline remains the
recruitment's recorded response deadline, so custom plant calendars continue
to behave correctly. The closed and planned states never use availability
language, remaining-opening counts, or a response deadline.

## Published schedule popup

During either planned state, the header is a large, accessible button. Tapping
it opens an in-place modal over the timeclock home screen:

- The modal title identifies the Saturday date and shows its shift hours.
- When the active Saturday schedule is published, it lists the official work
  centers in schedule order with the assigned employee names below each one.
- If the active schedule is a draft that has a preserved published snapshot,
  the modal shows that preserved official version, never the draft.
- If no official version has been published, it states `Saturday schedule has
  not been published yet.` and does not reveal draft assignments.
- The modal has a visible Close button, closes on Escape or a backdrop tap,
  and returns focus to the header button.

The availability banner remains non-interactive; it is an invitation, not a
schedule promise. The home page does not introduce a separate public schedule
URL or expose employee data beyond the existing authenticated timeclock
experience.

## Architecture

`saturday_recruiting_store.home_banner()` will become a time-state lookup that
returns the active recruitment's date, deadline, shift hours, and a display
phase (`available`, `tomorrow`, or `today`). It will include closed and
published recruitment records through the shift end, but exclude cancelled
records. Only the `available` phase evaluates remaining compatible openings.

`routes/timeclock.py` will adapt that state to the home template. For planned
phases, it will read the day schedule and provide a small, read-only published
assignment view. A helper will choose the active published schedule or, if the
active version is draft, its preserved posted snapshot.

`timeclock_home.html` and the shared timeclock styles will render the two
planned labels and the accessible modal without changing the existing name
search or htmx screen flow.

## Error handling

The home route continues to fail safely: a recruiting or schedule lookup error
omits the Saturday header rather than delaying or breaking the kiosk. An empty
published assignment list is rendered as an explicit no-assignments message,
not as an unpublished schedule.

## Verification

Route-level tests will cover the available, Friday-after-deadline, Saturday
in-progress, after-shift, and cancelled states. They will also prove that the
modal renders published assignments, uses a preserved posted snapshot instead
of a draft, and never renders draft names before publication. Static-template
tests will cover the modal accessibility controls and planned-header button.
