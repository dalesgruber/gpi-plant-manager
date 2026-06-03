"""Pure validators for the kiosk time-off wizard: parse HH:MM, map a leave
shape + times to (hour_from, hour_to) bounds with validation, and compute the
complementary working-hours windows. No I/O — extracted from
routes/timeclock_time_off.py."""

from __future__ import annotations


def parse_time_to_float(s: str | None) -> float | None:
    """Convert a "HH:MM" string from an HTML ``<input type="time">`` into a
    decimal-hour float so it can be compared against shift bounds.

    Returns None on missing or malformed input — callers treat None as
    "no time provided" and either skip validation (full-day shape) or
    return a user-facing error (partial-day shapes that need the value)."""
    if not s:
        return None
    try:
        hh, mm = s.split(":")
        return int(hh) + int(mm) / 60.0
    except (ValueError, AttributeError):
        return None


def shape_to_hour_bounds(
    shape: str,
    time_a: str,
    time_b: str,
    shift_from: float,
    shift_to: float,
) -> tuple[float | None, float | None, str | None]:
    """Validate user-supplied times against the shape and shift window.

    Returns ``(hour_from, hour_to, error)``:
      - full_day → ``(None, None, None)``: no hours stored
      - late_arrival → arrival ``time_b`` must be inside the shift and
        after the start; hours span ``(shift_from, arrival)``
      - early_leave → leave ``time_a`` must be inside the shift and
        before the end; hours span ``(leave, shift_to)``
      - midday_gap → both ``time_a`` and ``time_b`` inside the shift,
        with ``time_b > time_a``; hours span ``(time_a, time_b)``

    Returning the (None, None, msg) tuple instead of raising lets the
    submit handler re-render the details form with the error string in
    the existing ``k-error`` banner, matching the same UX as the rest of
    the kiosk forms."""
    if shape == "full_day":
        return (None, None, None)
    a = parse_time_to_float(time_a)
    b = parse_time_to_float(time_b)
    if shape == "late_arrival":
        if b is None:
            return (None, None, "Arrival time required")
        if b <= shift_from:
            return (None, None, "Arrival time must be after shift start")
        if b > shift_to:
            return (None, None, "Arrival time must be within your shift")
        return (shift_from, b, None)
    if shape == "early_leave":
        if a is None:
            return (None, None, "Leave time required")
        if a < shift_from:
            return (None, None, "Leave time must be after shift start")
        if a >= shift_to:
            return (None, None, "Leave time must be before shift end")
        return (a, shift_to, None)
    if shape == "midday_gap":
        if a is None or b is None:
            return (None, None, "Both times required")
        if a < shift_from or b > shift_to or b <= a:
            return (None, None, "Times must be within your shift, end > start")
        return (a, b, None)
    return (None, None, f"Unknown shape: {shape}")


def compute_working_hours_json(
    shape: str,
    hour_from: float | None,
    hour_to: float | None,
    shift_from: float,
    shift_to: float,
) -> list[dict] | None:
    """Return the COMPLEMENT of the leave window — the ranges the employee
    is still working — as a list of ``{from, to}`` dicts.

    For ``full_day`` we return ``None`` (whole shift is off, no working
    complement exists). For partial-day shapes, we return up to two
    ranges: the morning window before the leave and the afternoon window
    after it. If the leave somehow covers the whole shift (shouldn't
    happen post-validation), we fall back to a single range covering the
    whole shift so the column doesn't end up empty.

    Stored in the ``working_hours_json`` JSONB column so the scheduler
    cascade and the kiosk calendar can render partial-day leaves with
    the actual hours-worked breakdown without re-deriving from times."""
    if shape == "full_day":
        return None
    if hour_from is None or hour_to is None:
        return None
    out: list[dict] = []
    if hour_from > shift_from:
        out.append({"from": shift_from, "to": hour_from})
    if hour_to < shift_to:
        out.append({"from": hour_to, "to": shift_to})
    return out or [{"from": shift_from, "to": shift_to}]
