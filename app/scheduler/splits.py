"""Training split definitions and the rules for choosing one.

A *split* is an ordered list of session templates for one week. Which split we
use depends on two things:
  * how many days the user actually has free this week, and
  * how committed they are (which caps the number of sessions).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Commitment, Exercise


@dataclass(frozen=True)
class SessionTemplate:
    name: str
    muscle_groups: frozenset[str]
    exercises: tuple[Exercise, ...]
    focus: str = ""  # short label shown on the workout card, e.g. "Chest · Shoulders"


@dataclass(frozen=True)
class Split:
    name: str
    sessions: tuple[SessionTemplate, ...]

    @property
    def days(self) -> int:
        return len(self.sessions)

    @property
    def hits_every_muscle_twice(self) -> bool:
        """The core muscle-building rule: every major muscle is trained at least twice a week."""
        return all(sum(muscle in s.muscle_groups for s in self.sessions) >= 2 for muscle in MAJOR_MUSCLES)


# ---------------------------------------------------------------- sessions ---
# Built for muscle building (hypertrophy): mostly 6-12 reps on compound lifts and
# 10-15 on isolation work. Each exercise lists the muscles it directly trains, which
# is what the weekly "sets per muscle" counter adds up.
CHEST, BACK, SHOULDERS, BICEPS, TRICEPS, QUADS, HAMSTRINGS, GLUTES, CALVES, CORE = (
    "chest", "back", "shoulders", "biceps", "triceps",
    "quads", "hamstrings", "glutes", "calves", "core",
)  # fmt: skip

E = Exercise

# The muscles the "train everything twice a week" rule checks.
MAJOR_MUSCLES = (CHEST, BACK, SHOULDERS, QUADS, HAMSTRINGS, GLUTES)

FULL_BODY = SessionTemplate(
    "Full Body",
    frozenset({CHEST, BACK, SHOULDERS, QUADS, HAMSTRINGS, GLUTES, CORE}),
    (E("Back Squat", 4, "6-10", (QUADS, GLUTES)),
     E("Bench Press", 4, "6-10", (CHEST,)),
     E("Barbell Row", 4, "8-12", (BACK,)),
     E("Romanian Deadlift", 3, "8-12", (HAMSTRINGS, GLUTES)),
     E("Lateral Raise", 3, "12-15", (SHOULDERS,)),
     E("EZ-Bar Curl", 2, "10-15", (BICEPS,)),
     E("Triceps Pushdown", 2, "10-15", (TRICEPS,))),
    "Compound · Core",
)  # fmt: skip
PUSH = SessionTemplate(
    "Push",
    frozenset({CHEST, SHOULDERS, TRICEPS}),
    (E("Bench Press", 4, "6-10", (CHEST,)),
     E("Incline Dumbbell Press", 3, "8-12", (CHEST,)),
     E("Seated Dumbbell Shoulder Press", 3, "8-12", (SHOULDERS,)),
     E("Lateral Raise", 4, "12-15", (SHOULDERS,)),
     E("Cable Fly", 3, "12-15", (CHEST,)),
     E("Overhead Triceps Extension", 3, "10-15", (TRICEPS,))),
    "Chest · Shoulders · Triceps",
)  # fmt: skip
PULL = SessionTemplate(
    "Pull",
    frozenset({BACK, BICEPS}),
    (E("Pull-up or Lat Pulldown", 4, "6-10", (BACK,)),
     E("Chest-Supported Row", 4, "8-12", (BACK,)),
     E("Seated Cable Row", 3, "10-12", (BACK,)),
     E("Face Pull", 3, "12-15", (SHOULDERS,)),
     E("Incline Dumbbell Curl", 3, "10-12", (BICEPS,)),
     E("Hammer Curl", 2, "10-15", (BICEPS,))),
    "Back · Biceps",
)  # fmt: skip
LEGS = SessionTemplate(
    "Legs",
    frozenset({QUADS, HAMSTRINGS, GLUTES, CALVES, CORE}),
    (E("Back Squat", 4, "6-10", (QUADS, GLUTES)),
     E("Romanian Deadlift", 3, "8-12", (HAMSTRINGS, GLUTES)),
     E("Leg Press", 3, "10-15", (QUADS,)),
     E("Lying Leg Curl", 3, "10-15", (HAMSTRINGS,)),
     E("Leg Extension", 3, "12-15", (QUADS,)),
     E("Standing Calf Raise", 4, "10-15", (CALVES,))),
    "Quads · Glutes · Hamstrings",
)  # fmt: skip
UPPER = SessionTemplate(
    "Upper",
    frozenset({CHEST, BACK, SHOULDERS, BICEPS, TRICEPS}),
    (E("Bench Press", 4, "6-10", (CHEST,)),
     E("Barbell Row", 4, "8-12", (BACK,)),
     E("Incline Dumbbell Press", 3, "8-12", (CHEST,)),
     E("Lat Pulldown", 3, "10-12", (BACK,)),
     E("Lateral Raise", 3, "12-15", (SHOULDERS,)),
     E("EZ-Bar Curl", 3, "10-15", (BICEPS,)),
     E("Triceps Pushdown", 3, "10-15", (TRICEPS,))),
    "Chest · Back · Arms",
)  # fmt: skip
LOWER = SessionTemplate(
    "Lower",
    frozenset({QUADS, HAMSTRINGS, GLUTES, CALVES, CORE}),
    (E("Back Squat", 4, "6-10", (QUADS, GLUTES)),
     E("Romanian Deadlift", 3, "8-12", (HAMSTRINGS, GLUTES)),
     E("Leg Press", 3, "10-15", (QUADS,)),
     E("Lying Leg Curl", 3, "10-15", (HAMSTRINGS,)),
     E("Standing Calf Raise", 4, "10-15", (CALVES,)),
     E("Hanging Leg Raise", 3, "10-15", (CORE,))),
    "Quads · Glutes · Hamstrings",
)  # fmt: skip

# ------------------------------------------------------------------ splits ---
SPLITS: dict[str, Split] = {
    s.name: s
    for s in (
        Split("Full Body x1", (FULL_BODY,)),
        Split("Full Body x2", (FULL_BODY, FULL_BODY)),
        Split("Upper/Lower x1", (UPPER, LOWER)),
        Split("Full Body x3", (FULL_BODY, FULL_BODY, FULL_BODY)),
        Split("Push/Pull/Legs", (PUSH, PULL, LEGS)),
        Split("Upper/Lower x2", (UPPER, LOWER, UPPER, LOWER)),
        Split("PPL + Upper/Lower", (PUSH, PULL, LEGS, UPPER, LOWER)),
        Split("Push/Pull/Legs x2", (PUSH, PULL, LEGS, PUSH, PULL, LEGS)),
    )
}

# Most sessions per week each commitment level asks for.
MAX_DAYS: dict[Commitment, int] = {
    Commitment.LIGHT: 2,
    Commitment.MODERATE: 4,
    Commitment.DEDICATED: 6,
}


def candidate_splits(days: int, commitment: Commitment) -> list[Split]:
    """Splits that use exactly ``days`` sessions, best option first.

    Splits that train every muscle twice a week come first. The ones that don't
    (Push/Pull/Legs once, Upper/Lower once, Full Body once) are last resorts the
    planner only uses when nothing better fits.
    """
    if days <= 0:
        return []
    by_days: dict[int, list[str]] = {
        1: ["Full Body x1"],
        2: ["Full Body x2", "Upper/Lower x1"],
        3: ["Full Body x3", "Push/Pull/Legs"],
        4: ["Upper/Lower x2"],
        5: ["PPL + Upper/Lower"],
        6: ["Push/Pull/Legs x2"],
    }  # fmt: skip
    return [SPLITS[name] for name in by_days.get(min(days, 6), [])]


# ------------------------------------------------------------- session length ---
# Rough time per working set, including rest between sets, plus a warm-up.
MINUTES_PER_SET = 2.5
WARMUP_MINUTES = 5
MIN_SETS_PER_EXERCISE = 2
MAX_SETS_COMPOUND = 5  # the first two lifts of each session are the big compound ones
MAX_SETS_ISOLATION = 4


def set_budget(minutes: int) -> int:
    """How many working sets fit in a session of this length (45 min -> 16, 60 -> 22, 90 -> 34)."""
    return max(6, int((minutes - WARMUP_MINUTES) / MINUTES_PER_SET))


def _max_sets(index: int) -> int:
    return MAX_SETS_COMPOUND if index < 2 else MAX_SETS_ISOLATION


def fit_to_duration(template: SessionTemplate, minutes: int) -> SessionTemplate:
    """Shrink or grow a session so its sets fit the workout length.

    Shorter: take a set from whichever exercise has the most (later exercises first
    on ties), down to 2 sets each, then drop exercises from the end (keeping 3+).
    Longer: add one set at a time, compound lifts first, up to 5 sets for the two
    main lifts and 4 for the rest. Extra time beyond that becomes longer rests.
    """
    sets = [ex.sets for ex in template.exercises]
    budget = set_budget(minutes)

    while sum(sets) > budget:
        most = max(sets)
        if most > MIN_SETS_PER_EXERCISE:
            i = max(i for i, n in enumerate(sets) if n == most)
            sets[i] -= 1
        elif len(sets) > 3:
            sets.pop()
        else:
            break
    while sum(sets) < budget:
        grew = False
        for i, n in enumerate(sets):
            if sum(sets) < budget and n < _max_sets(i):
                sets[i] += 1
                grew = True
        if not grew:
            break

    exercises = tuple(
        Exercise(ex.name, n, ex.reps, ex.muscles) for ex, n in zip(template.exercises, sets, strict=False)
    )
    return SessionTemplate(template.name, template.muscle_groups, exercises, template.focus)


# Main muscles shown in the weekly volume counter, in display order.
VOLUME_MUSCLES = (CHEST, BACK, SHOULDERS, BICEPS, TRICEPS, QUADS, HAMSTRINGS, GLUTES)
# Research suggests about 10+ direct sets per muscle per week to maximize growth (see README).
TARGET_WEEKLY_SETS = 10


def weekly_sets(sessions) -> dict[str, int]:
    """Direct working sets per muscle across a week's sessions (anything with `.exercises`)."""
    totals = dict.fromkeys(VOLUME_MUSCLES, 0)
    for session in sessions:
        for ex in session.exercises:
            for muscle in ex.muscles:
                if muscle in totals:
                    totals[muscle] += ex.sets
    return totals
