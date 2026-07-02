# UX walkthrough — gpiplantmanager.com (2026-07-02)

Method: walked every major user path on the live site in Chrome at desktop width, logged in as Dale.
Pages covered: Recycling / New / Operator / Work Centers dashboards, Trophy Case, Leaderboards,
Plant Scheduler (today + next-day draft, assignment picker), Time Off calendar + Approvals,
Skills Matrix, People detail, Forklift leaderboards, Past Schedules, Exception Inbox (+ archive),
Settings (Work Centers & Goals, Timeclock, Forklift), Timeclock kiosk (entry, employee time-off
landing, Who's Out), What's New panel, Send-feedback modal, `/changelog`, `/admin/page-usage`.
No mutating action was clicked (nothing published, approved, corrected, or punched).

---

## 1. Things that look broken (fix before polish)

**1.1 The Forklift Demand Advisor card is missing from the scheduler.**
Settings → Forklift says "The scheduler's right rail shows a forklift-driver recommendation for
the next working day" and the toggle is ON, but the right rail contains only "Notes for the day"
on both today's and tomorrow's views (confirmed via accessibility tree — no advisor element is
rendered). The only forklift signal on the scheduler is small red text under the Forklift bay
label ("6 Suggested · Predicted Time-to-Claim 59.7"). Likely a regression from one of the
scheduler/forklift redesigns.

**1.2 `/changelog` renders as raw unstyled HTML** — browser-default serif text, no nav, no CSS.
The What's New panel is fine; the underlying page it feeds from is what users hit if they open
`/changelog` directly.

**1.3 Forklift settings "Worked example" shows em-dashes instead of numbers**:
"— / 100 with your weights · algorithm baseline —". The panel promises a live example score for
Isidro but computes nothing visible.

**1.4 Recycling dashboard number clipping.** "Total Pallets Processed" overflows its card
("1,023" is cut off at the right edge), and the Pallets/hr subtext truncates mid-token as
"(55.6-D4…" — cryptic even when it fits.

**1.5 Kiosk time-off session bounce is silent.** The token in kiosk URLs is single-use; going
back to a time-off screen (or reusing a link) drops the employee onto the name list. The code
intends an "expired" banner (`/timeclock?expired=1`), but in my test the bounce showed no banner
at all — the employee just finds themselves back at the start with no explanation.

---

## 2. Where users get confused — semantics

**2.1 Red vs. green contradicts itself on the Recycling dashboard.** The 15-minute chart shows a
mostly-green shift while Daily Progress right below is nearly all red (bars are red whenever the
cumulative total is below the pace line — even at 93.6% of goal). Same shift reads as "good" and
"failing" at once. Suggest: neutral bar color + pace line on the cumulative chart, or an explicit
"on pace / behind by N" label.

**2.2 "STOPPED" vs. "Downtime 0m" on Work Centers.** Repair 2 shows STOPPED, 231 units, downtime
0m. Nothing explains that STOPPED means "not producing right now" while Downtime is an
accumulated shift metric. One line of legend copy ("Stopped = no units in last X min") would fix
it. Also the Dismantler cards render in 1, 3, 2, 4 order.

**2.3 "% of goal = 0%" on leaderboards where no goal exists.** Trim Saw days of 4,922 units show
0%, and a 1-unit Repair row ranks with 0% — with "% of goal" as the default metric, the board
looks wrong. Either hide the % column for work centers without goals, show "no goal set", or
default those cards to Units.

**2.4 Unexplained codes and icons.** "(3)" / "(4)" after names on leaderboards; 🐐; forklift-cert
and truck badges; wrench icon; "Beat 891.0 to top Jose Ochoa"; "Predicted Time-to-Claim 59.7"
(of what — minutes? seconds? and why red?). The scheduler has a legend but it's at the very
bottom of a long page and covers only skill colors; nothing anywhere explains the icons or the
(N) suffixes. Suggest a shared legend/tooltip treatment, and always attach units to numbers
("59.7 min", "891 units").

**2.5 First-name-only forklift leaderboards are ambiguous.** The plant has Jesus Martinez, Jesus
Galindo, and Jesus Moreno; the forklift boards just say "Jesus". Use first name + last initial.

**2.6 Operator dashboard's empty state reads as catastrophe.** It defaults to "Repair 1
(unassigned)" and shows a giant red 0/750, "GOAT RACE −246 BEHIND", Pallets/hr 0.0 — when the
truth is simply "nobody is assigned here". Show a calm "No operator assigned to Repair 1 today"
state instead of red zeros, and consider defaulting to a work center that's actually staffed.

**2.7 "TESTING DAY ✕" badge.** On today's scheduler a yellow "TESTING DAY" chip appears in the
top nav with no explanation, and its ✕ looks like it clears the flag in one click. Anyone who
doesn't know the feature will wonder what it means; anyone who does can nuke it accidentally.
Add a tooltip and a confirm on the ✕.

**2.8 UTC timestamps.** Dashboards say "Refreshed 14:04:40 UTC" to a Minnesota audience. Show
local time.

**2.9 Time-off status is invisible on calendars.** The sidebar says pills cover "approved &
pending", but every pill on Time Off and Who's Out looks identical, so a supervisor can't tell a
confirmed absence from a request that might be refused. Differentiate (outline vs. solid) and add
a two-item legend.

---

## 3. Where users get confused — inconsistency

**3.1 Four different date-control paradigms**: Recycling uses range chips (Today/Yesterday/…),
New and Work Centers use a Day field + green "Update" button, People uses From/To + "Apply",
Time Off uses Prev/Today/Next. Pick one pattern (chips + optional custom range served everywhere
would cover all four) so a user's habit transfers between pages.

**3.2 Three different save models**: Forklift settings has an explicit Save; Timeclock schedule
edits appear to persist with no Save button (nothing tells you either way); the scheduler
auto-saves a DRAFT until Publish. Users can't predict what's committed. At minimum add a "saved"
toast/indicator wherever changes auto-persist.

**3.3 Dashboards don't share a visual language.** Recycling is a drag/resize widget canvas; New
is a static card page whose lone progress bar is gray at 110/110 (full but colorless, no goal
semantics); Operator is a third style. Fine to differ in layout, but color semantics (green =
at goal) should hold everywhere.

**3.4 Copy drift**: "No record yet." vs "No top-day winners for 2026."; nav says "Inbox" while
the page is "Exception Inbox"; scheduler legend says "Click + Add to assign someone" but the
actual control is a dropdown picker.

**3.5 Skills Matrix legend advertises `0 = not trained` but untrained cells render as "—"** —
the red 0 chip in the legend never appears in the grid.

---

## 4. Accidental-click hazards

- Pencil (override) icons on every Trophy Case row — one mis-tap opens an award-editing dialog.
  Consider hiding overrides behind an "Edit" mode.
- ✕ on each leaderboard card hides it; recovery path (the "Inactive (22)" drawer?) is not
  obvious. A hidden-by-accident card looks like lost data.
- Past Schedules has a bare **Delete** button on every published day in the list.
  (I did not test whether it confirms.)
- "TESTING DAY ✕" (see 2.7).

---

## 5. Kiosk-specific notes

- The entry screen is English-only ("Tap your name to clock in or out") for a heavily
  Spanish-speaking workforce; bilingual support exists per-person deeper in the flow. Put both
  languages on the headline and search placeholder.
- Salaried staff (e.g., Dale) tap their name and silently land on "Time Off" with no clock
  in/out and no explanation. One line — "Salaried — no punches needed. Request time off below."
  — would prevent "the kiosk is broken for me" reports.
- The time-off landing fades in slowly (page is blank white for a beat on the kiosk redirect).
- Session-expiry bounce is silent (see 1.5).

## 6. Smaller observations

- What's New entries are commit-log technical ("wired to POST /feedback", "accessible names",
  CSS details). Supervisors won't parse them; keep two sentences of plain language per entry and
  link the technical detail.
- Exception Inbox: the "Fix time" pill is styled like a status but is really an instruction; the
  time input + green "Correct" is good, though what happens on an empty time is untested.
- Assignment picker has no search box (fine at 13 names, worth adding if the roster grows) and
  relies on color alone to encode skill level inside the list — hard on color-blind users; the
  colored names could carry the small skill chip from the legend.
- Leaderboards live under the Trophy Case tab but at `/staffing/leaderboards` — harmless, but
  deep links look like a different section.

## 7. Suggested priority order

1. Restore the Forklift Demand Advisor card (or update the Settings copy) — 1.1
2. Style `/changelog` with the shared layout — 1.2
3. Fix clipped numbers + truncated subtext on Recycling — 1.4
4. Make the kiosk expiry banner actually show; explain the salaried bounce — 1.5, §5
5. Neutral-color the cumulative Daily Progress chart or label pace — 2.1
6. Hide/replace "% of goal" where no goal is configured — 2.3
7. Units + tooltips for Time-to-Claim, (N) suffixes, icons; legend placement — 2.4
8. Disambiguate first names on forklift boards — 2.5
9. Calm empty state for Operator dashboard — 2.6
10. Tooltip + confirm on TESTING DAY ✕; edit-mode for trophy pencils — 2.7, §4
11. Local-time timestamps — 2.8
12. Converge date controls and save-feedback patterns — 3.1, 3.2
13. Bilingual kiosk headline — §5
14. Approved-vs-pending distinction on time-off calendars — 2.9

Not assessed: phone/tablet responsive behavior (screenshots captured at a fixed desktop
viewport), Odoo write paths, TV displays (`/tv/*`), and any destructive flows (delete, publish,
approve, punch).
