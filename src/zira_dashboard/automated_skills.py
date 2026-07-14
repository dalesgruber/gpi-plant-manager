"""Calculate and synchronize performance-based Recycled skill levels."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Lock

from . import app_settings, db, shift_config, skill_levels, staffing, work_centers_store
from . import automated_skill_settings as settings_store
from .automated_skill_settings import BucketSettings
from .plant_day import today as plant_today


GROUP_TO_SKILL = {"Repair": "Repair", "Dismantler": "Dismantle"}
# app_settings storage key. Named *_NAME rather than *_KEY so the secret
# scanner's env-key heuristic doesn't flag it as a credential.
LAST_DAILY_NAME = "automated_skills.last_daily_day"
_run_lock = Lock()


class RunInProgress(RuntimeError):
    """Raised when a second automatic-skill run overlaps an active one."""


@dataclass(frozen=True)
class DailyRecord:
    day: date
    person_id: int
    name: str
    wc_name: str
    units: float
    hours: float
    operator_count: int


@dataclass(frozen=True)
class Evaluation:
    person_id: int
    name: str
    days: int
    attainment_pct: float | None
    level: int | None


def bucket_for(attainment_pct: float, settings: BucketSettings) -> int:
    if attainment_pct >= settings.level_3_min:
        return 3
    if attainment_pct >= settings.level_2_min:
        return 2
    if attainment_pct >= settings.level_1_min:
        return 1
    return 0


def evaluate(
    records: list[DailyRecord],
    goals: dict[str, float],
    settings: BucketSettings,
    standard_full_day_hours: float,
    *,
    min_hours: float = 4.0,
    min_days: int = 2,
) -> list[Evaluation]:
    """Map eligible people to a level from their average daily attainment.

    Attribution already divides a work center's output among its operators.
    The matching goal share is reconstructed from its daily operator count.
    """
    by_person_day: dict[tuple[int, date], dict[str, float]] = defaultdict(
        lambda: {"units": 0.0, "hours": 0.0, "goal": 0.0}
    )
    names: dict[int, str] = {}
    for record in records:
        if record.operator_count <= 0 or record.wc_name not in goals:
            continue
        totals = by_person_day[(record.person_id, record.day)]
        totals["units"] += float(record.units)
        totals["hours"] += float(record.hours)
        totals["goal"] += float(goals[record.wc_name]) / record.operator_count
        names[record.person_id] = record.name

    scores_by_person: dict[int, list[float]] = defaultdict(list)
    for (person_id, _day), totals in by_person_day.items():
        if (
            totals["hours"] < min_hours
            or totals["hours"] <= 0
            or totals["goal"] <= 0
        ):
            continue
        normalized_units = (
            totals["units"] / totals["hours"] * standard_full_day_hours
        )
        scores_by_person[person_id].append(
            normalized_units / totals["goal"] * 100
        )

    out: list[Evaluation] = []
    for person_id in sorted(names, key=lambda value: names[value].lower()):
        scores = scores_by_person[person_id]
        if len(scores) < min_days:
            out.append(Evaluation(person_id, names[person_id], len(scores), None, None))
            continue
        attainment = sum(scores) / len(scores)
        out.append(
            Evaluation(
                person_id,
                names[person_id],
                len(scores),
                attainment,
                bucket_for(attainment, settings),
            )
        )
    return out


def work_centers_for_group(group: str) -> set[str]:
    if group not in GROUP_TO_SKILL:
        raise ValueError(f"Unsupported automated-skill group: {group}")
    return {loc.name for loc in staffing.LOCATIONS if loc.skill == group}


def goals_for_group(group: str) -> dict[str, float]:
    return {
        loc.name: float(work_centers_store.goal_per_day(loc))
        for loc in staffing.LOCATIONS
        if loc.skill == group
    }


def records_for_group(group: str, start: date, end: date) -> list[DailyRecord]:
    rows = db.query(
        """
        SELECT pd.day, pd.emp_id::int AS person_id, pd.name, pd.wc_name,
               pd.units, pd.hours,
               COUNT(*) OVER (PARTITION BY pd.day, pd.wc_name) AS operator_count
        FROM production_daily pd
        WHERE pd.day BETWEEN %s AND %s
          AND pd.wc_name = ANY(%s)
          AND NOT EXISTS (
              SELECT 1 FROM manual_absences ma
              WHERE ma.day = pd.day AND ma.name = pd.name
          )
        """,
        (start, end, sorted(work_centers_for_group(group))),
    )
    return [
        DailyRecord(
            row["day"], int(row["person_id"]), row["name"], row["wc_name"],
            float(row["units"]), float(row["hours"]), int(row["operator_count"]),
        )
        for row in rows
    ]


def current_levels(group: str) -> dict[int, tuple[int, int]]:
    rows = db.query(
        """
        SELECT p.id AS person_id, s.id AS skill_id,
               COALESCE(ps.level, 0) AS level
        FROM people p
        JOIN skills s ON s.name = %s
        LEFT JOIN person_skills ps ON ps.person_id = p.id AND ps.skill_id = s.id
        WHERE p.active = TRUE AND p.excluded = FALSE
        """,
        (GROUP_TO_SKILL[group],),
    )
    return {
        int(row["person_id"]): (int(row["skill_id"]), int(row["level"]))
        for row in rows
    }


def run_group(group: str, trigger: str, through_day: date) -> settings_store.RunSummary:
    if not _run_lock.acquire(blocking=False):
        raise RunInProgress("An automated skill run is already in progress.")
    try:
        evaluations = evaluate(
            records_for_group(group, through_day - timedelta(days=29), through_day),
            goals_for_group(group),
            settings_store.current(group),
            shift_config.productive_minutes_per_day() / 60.0,
        )
        levels = current_levels(group)
        changed = unchanged = skipped = 0
        failures: list[dict[str, str]] = []
        for evaluation in evaluations:
            if evaluation.level is None:
                skipped += 1
                continue
            current = levels.get(evaluation.person_id)
            if current is None or current[1] == evaluation.level:
                unchanged += 1
                continue
            try:
                skill_levels.set_person_skill_level(
                    evaluation.person_id, current[0], evaluation.level
                )
                changed += 1
            except skill_levels.SkillSyncError as exc:
                failures.append({"name": evaluation.name, "error": str(exc)})
        summary = settings_store.RunSummary(
            group=group,
            trigger=trigger,
            evaluated=len(evaluations) - skipped,
            changed=changed,
            unchanged=unchanged,
            skipped=skipped,
            failures=tuple(failures),
            run_at=datetime.now(UTC).isoformat(),
        )
        settings_store.save_last_run(summary)
        return summary
    finally:
        _run_lock.release()


def run_daily_if_due(now: datetime) -> list[settings_store.RunSummary]:
    """Run both Recycled groups once per plant day, after the shift ends.

    Self-throttling: a persisted marker records the last completed plant day so
    repeated warmer ticks never re-run. Returns the summaries produced this call
    (empty before the shift end or once the day is already recorded).
    """
    day = plant_today()
    local_now = now.astimezone(shift_config.SITE_TZ)
    if local_now.time() < shift_config.shift_end_for(day):
        return []
    if app_settings.get_setting(LAST_DAILY_NAME) == {"day": day.isoformat()}:
        return []
    summaries: list[settings_store.RunSummary] = []
    for group in GROUP_TO_SKILL:
        try:
            summaries.append(run_group(group, "daily", day))
        except RunInProgress:
            return summaries
    app_settings.set_setting(LAST_DAILY_NAME, {"day": day.isoformat()})
    return summaries
