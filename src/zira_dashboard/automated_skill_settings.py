"""Settings and run summaries for automated Recycled skill levels."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from . import app_settings


CONFIG_KEY = "automated_skills.bucket_settings"
RUNS_KEY = "automated_skills.last_runs"
GROUPS = ("Repair", "Dismantler")


@dataclass(frozen=True)
class BucketSettings:
    level_3_min: float = 90.0
    level_2_min: float = 80.0
    level_1_min: float = 70.0


@dataclass(frozen=True)
class RunSummary:
    group: str
    trigger: str
    evaluated: int
    changed: int
    unchanged: int
    skipped: int
    failures: tuple[dict[str, str], ...]
    run_at: str | None


def _require_group(group: str) -> None:
    if group not in GROUPS:
        raise ValueError(f"Unsupported automated-skill group: {group}")


def validate(settings: BucketSettings) -> BucketSettings:
    values = (settings.level_3_min, settings.level_2_min, settings.level_1_min)
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 <= float(value) <= 100
        for value in values
    ):
        raise ValueError("Skill bucket percentages must be numbers from 0 through 100.")
    normalized = BucketSettings(*(float(value) for value in values))
    if not normalized.level_3_min >= normalized.level_2_min >= normalized.level_1_min:
        raise ValueError("Skill buckets must satisfy Level 3 >= Level 2 >= Level 1.")
    return normalized


def _config_payload() -> dict:
    raw = app_settings.get_setting(CONFIG_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def current(group: str) -> BucketSettings:
    _require_group(group)
    raw = _config_payload().get(group)
    if not isinstance(raw, dict):
        return BucketSettings()
    try:
        return validate(BucketSettings(**raw))
    except (TypeError, ValueError):
        return BucketSettings()


def all_current() -> dict[str, BucketSettings]:
    return {group: current(group) for group in GROUPS}


def save(group: str, settings: BucketSettings) -> None:
    _require_group(group)
    settings = validate(settings)
    payload = {name: asdict(current(name)) for name in GROUPS}
    payload[group] = asdict(settings)
    app_settings.set_setting(CONFIG_KEY, payload)


def last_run(group: str) -> RunSummary | None:
    _require_group(group)
    raw = app_settings.get_setting(RUNS_KEY)
    value = raw.get(group) if isinstance(raw, dict) else None
    if not isinstance(value, dict):
        return None
    try:
        return RunSummary(
            group=str(value["group"]),
            trigger=str(value["trigger"]),
            evaluated=int(value["evaluated"]),
            changed=int(value["changed"]),
            unchanged=int(value["unchanged"]),
            skipped=int(value["skipped"]),
            failures=tuple(value.get("failures") or ()),
            run_at=value.get("run_at"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def save_last_run(summary: RunSummary) -> None:
    _require_group(summary.group)
    raw = app_settings.get_setting(RUNS_KEY)
    payload = dict(raw) if isinstance(raw, dict) else {}
    value = asdict(summary)
    value["failures"] = list(summary.failures)
    payload[summary.group] = value
    app_settings.set_setting(RUNS_KEY, payload)
