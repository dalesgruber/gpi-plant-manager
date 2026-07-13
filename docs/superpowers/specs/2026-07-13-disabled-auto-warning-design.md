# Disabled Auto Work-Center Warning Design

## Goal

Remove an automatic staffing warning as soon as its work center is turned off
with the Scheduler's Auto checkbox.

## Current behavior and cause

The saved Auto setting already excludes an off center from the server's next
automatic scheduling pass. The browser, however, retains the warning banner
from the earlier pass after saving the checkbox. This leaves a stale message
such as "Big Build #1 is staffed below its minimum" visible until a later
rebuild or page load.

## Behavior

After the Auto checkbox save succeeds, the Scheduler immediately removes
automatic center-specific warnings for every currently disabled Auto center.
The warning is removed from both the visible banner and the client-side warning
state, with no schedule rebuild and no assignment mutation.

Only warnings tied to a disabled work center are removed:

- "<work center> is staffed below its minimum ..."
- "No safe operator pairing available for <work center>."

Training-block and other warnings without a disabled work-center target remain
visible. If the Auto-toggle save fails, the checkbox is restored and warnings
are not changed. A subsequent page render remains authoritative and already
omits off centers from automatic scheduling warnings.

## Verification

Add a browser-script contract test for the disabled-center warning filter and
run the Staffing rotation/static tests. The test must prove Big Build #1's
minimum-staffing warning is removed when it is disabled while an unrelated
warning remains.
