"""Staffing data layer: locations, roster, daily schedules.

Storage is flat JSON files so you get free history and easy manual edits.
- roster.json               — people + per-skill levels (0–3) + active flag
- schedules/YYYY-MM-DD.json — one file per day with assignments
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from threading import RLock

# Skill categories (columns in Dale's Google Sheet skill matrix).
SKILLS: tuple[str, ...] = (
    "Repair",
    "Dismantler",
    "Trim Saw",
    "Woodpecker",
    "Junior",
    "Master Recycler",
    "Hand Build",
    "Chop/Notch",
    "Forklift: Load/Jockey",
    "Forklift: Tablets",
    "Mechanic",
)


@dataclass(frozen=True)
class Location:
    name: str                               # unique display name
    skill: str                              # default required skill (legacy; required_skills is the source of truth)
    bay: str                                # grouping for display
    department: str                         # Recycled / New / Supervisor / Maintenance
    meter_id: str | None                    # Zira station ID if mapped, else None
    min_ops: int = 1                        # minimum operators required to run
    max_ops: int | None = 1                 # max operators; None = unlimited
    required_skills: tuple[str, ...] = ()   # if empty, defaults to (skill,)
    note: str | None = None                 # user-editable free-form note shown under name


# 22 work centers, in Plant-Scheduler-sheet order.
LOCATIONS: tuple[Location, ...] = (
    # Bay 1
    Location("Repair 1", "Repair", "Bay 1", "Recycled", "40721"),
    Location("Repair 2", "Repair", "Bay 1", "Recycled", "40720"),
    Location("Repair 3", "Repair", "Bay 1", "Recycled", "40719"),
    # Bay 2
    Location("Dismantler 4", "Dismantler", "Bay 2", "Recycled", "42715"),
    Location("Dismantler 3", "Dismantler", "Bay 2", "Recycled", "42714"),
    # Bay 3
    Location("Dismantler 2", "Dismantler", "Bay 3", "Recycled", "42713"),
    Location("Dismantler 1", "Dismantler", "Bay 3", "Recycled", "42711"),
    # Bay 4
    Location("Trim Saw 1", "Trim Saw", "Bay 4", "Recycled", "43286", min_ops=2, max_ops=2),
    Location("Master Recycler", "Master Recycler", "Bay 4", "Recycled", None),
    # Bay 5
    Location("Repair 4", "Repair", "Bay 5", "Recycled", None),
    Location("Repair 5", "Repair", "Bay 5", "Recycled", None),
    Location("Hand Build #2", "Hand Build", "Bay 5", "New", None, min_ops=2, max_ops=2),
    # Bay 6
    Location("Hand Build #1", "Hand Build", "Bay 6", "New", None, min_ops=2, max_ops=2),
    # Bay 14
    Location("Chop/Notch", "Chop/Notch", "Bay 14", "New", None),
    Location("Big Build #1", "Hand Build", "Bay 14", "New", None, min_ops=2, max_ops=2),
    # Bay 16
    Location("Woodpecker #1", "Woodpecker", "Bay 16", "New", None, min_ops=1, max_ops=3),
    Location("Junior #1", "Junior", "Bay 16", "New", None),
    # Bay 17
    Location("Junior #2", "Junior", "Bay 17", "New", "42345"),
    Location("Junior #3", "Junior", "Bay 17", "New", None),
    # Forklift
    Location("Loading/Jockeying", "Forklift: Load/Jockey", "Forklift", "Supervisor", None),
    Location("Tablets", "Forklift: Tablets", "Forklift", "Supervisor", None, min_ops=1, max_ops=None),
    # Maint.
    Location("Work Orders", "Mechanic", "Maint.", "Maintenance", None, min_ops=1, max_ops=None),
)


def required_skills_for(loc: Location) -> tuple[str, ...]:
    """Returns the required skills for this work center. Falls back to the single
    `skill` field when `required_skills` is unset, for backward compatibility."""
    return loc.required_skills if loc.required_skills else (loc.skill,)

DEPARTMENT_ORDER = ("Recycled", "New", "Supervisor", "Maintenance")

BAY_SUBTITLES: dict[str, str] = {
    "Bay 5": "Complete yesterdays trailers",
}

TIME_OFF_KEY = "__time_off"  # pseudo-location for day-off list; not in LOCATIONS


@dataclass
class Person:
    name: str
    active: bool = True
    reserve: bool = False
    skills: dict[str, int] = field(default_factory=dict)

    def level(self, skill: str) -> int:
        return int(self.skills.get(skill, 0))


ROSTER_PATH = Path("roster.json")
PLANT_SCHEDULER_CSV = Path("Plant Scheduler(Plant Scheduler).csv")
SCHEDULES_DIR = Path("schedules")

_lock = RLock()


# ---------- bootstrap seed ----------

# Core roster pulled from skill-matrix screenshot + names added from Plant Scheduler CSV.
_SEED_ACTIVE = [
    "Ben", "Carlos", "Christian", "Eulogio", "Francisco", "Gerardo G", "Gerardo V",
    "Iban", "Isidro", "Jesus C", "Jesus M", "Jose L", "Juan", "Lauro", "Louie",
    "Lupe", "Isaac", "Trent", "Jose O",
    # names from CSV only
    "Ian", "Dale", "Luke",
]
_SEED_INACTIVE = [
    "Adrian", "Alejandro", "Humberto", "Jesus G", "Jose C", "Pascual", "Porfirio",
]

# Partial skills that are visible in the Plant Scheduler CSV (example day 04/27/2026).
# Fill the rest in via the Roster page or by dropping a skill-matrix CSV later.
_SEED_SKILL_HINTS: dict[str, dict[str, int]] = {
    "Jesus M":    {"Trim Saw": 3},
    "Lupe":       {"Hand Build": 3},
    "Gerardo G":  {"Hand Build": 3},
    "Isaac":      {"Woodpecker": 1},
    "Carlos":     {"Woodpecker": 3},
    "Gerardo V":  {"Junior": 3},
    "Jesus C":    {"Forklift: Load/Jockey": 2},
    "Lauro":      {"Forklift: Tablets": 1},
    "Trent":      {"Forklift: Tablets": 3},
    "Isidro":     {"Forklift: Tablets": 3},
    "Iban":       {"Forklift: Tablets": 3},
    "Juan":       {"Forklift: Tablets": 3},
    "Francisco":  {"Mechanic": 3},
    "Ben":        {"Mechanic": 3},
}


def _seed_roster() -> list[Person]:
    out: list[Person] = []
    for name in _SEED_ACTIVE:
        skills = {s: 0 for s in SKILLS}
        for k, v in _SEED_SKILL_HINTS.get(name, {}).items():
            skills[k] = v
        out.append(Person(name=name, active=True, skills=skills))
    for name in _SEED_INACTIVE:
        out.append(Person(name=name, active=False, skills={s: 0 for s in SKILLS}))
    return out


# ---------- CSV import helpers ----------

def _import_skill_matrix_csv(path: Path) -> list[Person] | None:
    """Recognize a skill-matrix CSV by presence of 'Master List' + skill columns."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or "Master List" not in reader.fieldnames:
                return None
            people: list[Person] = []
            for row in reader:
                name = (row.get("Master List") or "").strip()
                if not name:
                    continue
                active_raw = (row.get("is Scheduled") or "").strip().lower()
                active = active_raw in {"true", "1", "yes", "y"}
                skills: dict[str, int] = {}
                for s in SKILLS:
                    raw = (row.get(s) or "0").strip()
                    try:
                        skills[s] = max(0, min(3, int(raw)))
                    except ValueError:
                        skills[s] = 0
                people.append(Person(name=name, active=active, skills=skills))
            return people or None
    except (OSError, csv.Error):
        return None


def _default_assignments_from_plant_scheduler() -> dict[str, list[str]]:
    """Parse 'Defaults for New Day' column to get default person per position."""
    out: dict[str, list[str]] = {}
    if not PLANT_SCHEDULER_CSV.exists():
        return out
    try:
        rows = list(csv.reader(PLANT_SCHEDULER_CSV.open(encoding="utf-8-sig")))
    except (OSError, csv.Error):
        return out
    known = {loc.name for loc in LOCATIONS}
    for r in rows[3:]:
        if len(r) < 8:
            continue
        station = (r[6] or "").strip().split("\n")[0].strip()
        default_person = (r[5] or "").strip()
        if station in known and default_person:
            out.setdefault(station, []).append(default_person)
    return out


# ---------- roster ----------

def load_roster() -> list[Person]:
    with _lock:
        if ROSTER_PATH.exists():
            try:
                data = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
                return [
                    Person(
                        name=p["name"],
                        active=bool(p.get("active", True)),
                        reserve=bool(p.get("reserve", False)),
                        skills={s: int(p.get("skills", {}).get(s, 0)) for s in SKILLS},
                    )
                    for p in data
                ]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        # First-run bootstrap: prefer skill-matrix CSV if present, else seed.
        imported: list[Person] | None = None
        for candidate in (Path("roster.csv"), Path("skills.csv")):
            if candidate.exists():
                imported = _import_skill_matrix_csv(candidate)
                if imported:
                    break
        roster = imported if imported else _seed_roster()
        save_roster(roster)
        return roster


def save_roster(people: list[Person]) -> None:
    with _lock:
        payload = [
            {"name": p.name, "active": p.active, "reserve": p.reserve, "skills": p.skills}
            for p in people
        ]
        ROSTER_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------- daily schedule ----------

@dataclass
class Schedule:
    day: date
    published: bool = False
    # location name → list of person names (ordered as the user chose)
    assignments: dict[str, list[str]] = field(default_factory=dict)
    notes: str = ""                                     # day-wide note
    wc_notes: dict[str, str] = field(default_factory=dict)  # per-work-center notes
    testing_day: bool = False  # flag: training/override day; output not counted toward people
    # When the user edits a previously-posted day, we snapshot the posted version here
    # so they can toggle between draft and posted in the UI. Cleared on re-publish.
    published_snapshot: dict | None = None


def snapshot_of(sched: "Schedule") -> dict:
    """Return a serializable snapshot of the schedule's posted-visible fields."""
    return {
        "assignments": {k: list(v) for k, v in (sched.assignments or {}).items()},
        "notes": sched.notes or "",
        "wc_notes": dict(sched.wc_notes or {}),
        "testing_day": bool(sched.testing_day),
    }


def _schedule_path(day: date) -> Path:
    return SCHEDULES_DIR / f"{day.isoformat()}.json"


def load_schedule(day: date) -> Schedule:
    with _lock:
        p = _schedule_path(day)
        if not p.exists():
            return Schedule(day=day, published=False, assignments={})
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            assignments = {str(k): [str(x) for x in v] for k, v in (data.get("assignments") or {}).items()}
            notes = data.get("notes")
            wc_notes_raw = data.get("wc_notes") or {}
            wc_notes = {str(k): str(v) for k, v in wc_notes_raw.items() if isinstance(v, str) and v} if isinstance(wc_notes_raw, dict) else {}
            snap_raw = data.get("published_snapshot")
            snap = snap_raw if isinstance(snap_raw, dict) else None
            return Schedule(
                day=day,
                published=bool(data.get("published", False)),
                assignments=assignments,
                notes=str(notes) if isinstance(notes, str) else "",
                wc_notes=wc_notes,
                testing_day=bool(data.get("testing_day", False)),
                published_snapshot=snap,
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return Schedule(day=day, published=False, assignments={})


def save_schedule(schedule: Schedule) -> None:
    with _lock:
        SCHEDULES_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "day": schedule.day.isoformat(),
            "published": schedule.published,
            "assignments": schedule.assignments,
            "notes": schedule.notes or "",
            "wc_notes": {k: v for k, v in (schedule.wc_notes or {}).items() if v},
            "testing_day": bool(schedule.testing_day),
        }
        if schedule.published_snapshot:
            payload["published_snapshot"] = schedule.published_snapshot
        _schedule_path(schedule.day).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def default_assignments() -> dict[str, list[str]]:
    """From the Plant Scheduler CSV's 'Defaults for New Day' column, if present."""
    return _default_assignments_from_plant_scheduler()


# ---------- color / level helpers ----------

SKILL_COLORS = {
    3: "#4ade80",   # green — trained & proficient
    2: "#e6edf3",   # foreground — trained & competent
    1: "#fb923c",   # orange — practicing
    0: "#ef4444",   # red — not trained
}

SKILL_LABELS = {
    0: "not trained",
    1: "practicing",
    2: "competent",
    3: "proficient",
}


def skill_color(level: int) -> str:
    return SKILL_COLORS.get(int(level), SKILL_COLORS[0])
