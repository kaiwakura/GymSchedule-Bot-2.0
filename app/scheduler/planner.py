"""Build a 7-day muscle-building plan from free slots.

Training rules:
  * every major muscle is trained twice a week (splits that can't are last resorts)
  * at least 48 hours between sessions that train the same muscle (hard rule),
    ideally 72 hours (the planner prefers it but will accept 48)

Steps:
  1. Find free slots for each day (``availability.find_free_slots``).
  2. Decide how many sessions to aim for: min(commitment cap, free days).
  3. For each candidate split, search every way of placing its sessions on the
     free days and, on each day, a few possible start times (preferred, earliest,
     latest). That's at most C(7, k) * 3^k options (~5,000 for 6 sessions), small
     enough to check them all. Keep the best *valid* placement:
       - valid  = sessions sharing a muscle start at least 48 hours apart
       - best   = lowest score, adding up: uneven spacing across the week,
                  hours of rest short of the ideal 72, and hours away from the
                  preferred time of day
  4. If nothing fits, try fewer sessions. Splits that train every muscle twice
     are always tried before the ones that don't (e.g. 3 free days in a row:
     Full Body x3 can't get 48h of rest, so Full Body x2 on the 1st and 3rd day
     beats Push/Pull/Legs, which would train each muscle only once).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from itertools import combinations, pairwise, product
from statistics import pvariance
from zoneinfo import ZoneInfo

from app.models import BusyBlock, Preferences, Slot, TimeOfDay, WeeklyPlan, Workout
from app.scheduler.availability import find_free_slots, round_up
from app.scheduler.splits import MAX_DAYS, SessionTemplate, Split, candidate_splits, fit_to_duration

PREFERRED_TARGET: dict[TimeOfDay, time | None] = {
    TimeOfDay.MORNING: time(7, 0),
    TimeOfDay.MIDDAY: time(12, 0),
    TimeOfDay.EVENING: time(18, 0),
    TimeOfDay.ANY: None,
}

# How much one hour away from the preferred time "costs" compared to uneven spacing.
TIME_PENALTY_PER_HOUR = 0.25

MIN_REST_HOURS = 48  # never train the same muscle again sooner than this
IDEAL_REST_HOURS = 72  # aim for this much rest
# Cost of each hour of rest short of the ideal (48h of rest costs 24 * 0.05 = 1.2).
REST_PENALTY_PER_HOUR = 0.05


def start_options(day: date, slots: list[Slot], prefs: Preferences) -> list[tuple[datetime, float]]:
    """A few possible start times on ``day``: closest to the preferred time, earliest, latest.

    Returns (start, hours_away_from_preference) pairs, best first. Offering more than one
    time lets the planner shift a session (say, Wednesday evening instead of morning)
    to keep 48+ hours of rest between sessions for the same muscles.
    """
    duration = timedelta(minutes=prefs.session_minutes)
    target_time = PREFERRED_TARGET[prefs.preferred_time]
    options: dict[datetime, float] = {}

    for slot in slots:
        latest = slot.end - duration
        earliest = round_up(slot.start) if round_up(slot.start) <= latest else slot.start
        latest_rounded = latest.replace(minute=latest.minute - latest.minute % 15, second=0, microsecond=0)
        if latest_rounded < earliest:
            latest_rounded = latest
        if target_time is None:  # no preference: earliest is best
            target = slot.start
            preferred = earliest
        else:
            target = datetime.combine(day, target_time, tzinfo=slot.start.tzinfo)
            preferred = min(max(target, slot.start), latest)  # clamp into the slot
            rounded = round_up(preferred)
            preferred = rounded if rounded <= latest else preferred
        for candidate in (preferred, earliest, latest_rounded):
            options[candidate] = abs((candidate - target).total_seconds()) / 3600

    ranked = sorted(options.items(), key=lambda kv: (kv[1], kv[0]))
    preferred_first = ranked[:1]
    by_time = sorted(ranked, key=lambda kv: kv[0])
    extremes = [by_time[0], by_time[-1]]
    picked = list(dict.fromkeys(preferred_first + extremes))
    return picked


def best_start(day: date, slots: list[Slot], prefs: Preferences) -> tuple[datetime, float]:
    """The start time on ``day`` closest to the preferred time of day."""
    return start_options(day, slots, prefs)[0]


def rest_hours(first: datetime, second: datetime) -> float:
    """Hours between the starts of two sessions."""
    return abs((second - first).total_seconds()) / 3600


def rest_penalty(sessions: tuple[SessionTemplate, ...], starts: tuple[datetime, ...]) -> float | None:
    """None if two sessions training the same muscle are under 48 hours apart, else the cost of
    every hour of rest short of the ideal 72."""
    cost = 0.0
    for i in range(len(starts)):
        for j in range(i + 1, len(starts)):
            if sessions[i].muscle_groups & sessions[j].muscle_groups:
                gap = rest_hours(starts[i], starts[j])
                if gap < MIN_REST_HOURS:
                    return None
                cost += max(0.0, IDEAL_REST_HOURS - gap) * REST_PENALTY_PER_HOUR
    return cost


def violates_recovery(sessions: tuple[SessionTemplate, ...], starts: tuple[datetime, ...]) -> bool:
    """True if two sessions that train the same muscle start less than 48 hours apart."""
    return rest_penalty(sessions, starts) is None


def spacing_penalty(days: tuple[date, ...]) -> float:
    """Variance of the gaps between sessions, including the wrap to the next week.

    Mon/Thu -> gaps [3, 4] -> low penalty. Mon/Tue -> gaps [1, 6] -> high penalty.
    """
    if len(days) < 2:
        return 0.0
    gaps = [(b - a).days for a, b in pairwise(days)]
    gaps.append(7 - (days[-1] - days[0]).days)
    return pvariance(gaps)


def place_split(
    split: Split, free_days: list[date], slots_by_day: dict[date, list[Slot]], prefs: Preferences
) -> list[tuple[SessionTemplate, datetime]] | None:
    """Best valid placement of ``split`` onto ``free_days``, or None if impossible."""
    best_score: float | None = None
    best_starts: tuple[datetime, ...] | None = None
    options = {d: start_options(d, slots_by_day[d], prefs) for d in free_days}

    for days in combinations(free_days, split.days):  # already in date order
        spacing = spacing_penalty(days)
        for choice in product(*(options[d] for d in days)):
            starts = tuple(start for start, _ in choice)
            rest = rest_penalty(split.sessions, starts)
            if rest is None:
                continue
            time_cost = sum(hours_away for _, hours_away in choice) * TIME_PENALTY_PER_HOUR
            score = spacing + rest + time_cost
            if best_score is None or score < best_score:
                best_score, best_starts = score, starts

    if best_starts is None:
        return None
    return list(zip(split.sessions, best_starts, strict=True))


def week_start_for(day: date) -> date:
    """Monday of the week containing ``day``."""
    return day - timedelta(days=day.weekday())


def build_plan(
    busy: list[BusyBlock],
    week_start: date,
    prefs: Preferences,
    now: datetime | None = None,
) -> WeeklyPlan:
    """Main entry point: busy calendar + preferences -> weekly workout plan."""
    slots_by_day = find_free_slots(busy, week_start, prefs, now)
    free_days = [d for d in sorted(slots_by_day) if slots_by_day[d]]
    cap = MAX_DAYS[prefs.commitment]
    target = min(cap, len(free_days))
    duration = timedelta(minutes=prefs.session_minutes)

    notes = [
        f"You have {len(free_days)} day(s) in the next 7 with a free "
        f"{prefs.session_minutes}-minute window (plus {prefs.buffer_minutes} min travel buffer).",
        f"A '{prefs.commitment.value}' commitment allows up to {cap} sessions per week.",
    ]

    # Pass 1: only splits that train every muscle twice. Pass 2 (last resort): anything that fits.
    for twice_only in (True, False):
        for days in range(target, 0, -1):
            for split in candidate_splits(days, prefs.commitment):
                if split.hits_every_muscle_twice != twice_only:
                    continue
                placement = place_split(split, free_days, slots_by_day, prefs)
                if placement is None:
                    notes.append(
                        f"{split.name} didn't fit with 48+ hours of rest between sessions for the same muscles."
                    )
                    continue
                if days < target:
                    notes.append(
                        f"Planned {days} session(s) instead of {target} so every muscle gets 48+ hours of rest."
                    )
                if not twice_only:
                    notes.append(
                        "There isn't enough free time to train every muscle twice this week, so this is the best fit."
                    )
                workouts = [
                    Workout(t.name, t.muscle_groups, t.exercises, start, start + duration, t.focus)
                    for t, start in ((fit_to_duration(t, prefs.session_minutes), start) for t, start in placement)
                ]
                rests = [
                    rest_hours(a.start, b.start)
                    for i, a in enumerate(workouts)
                    for b in workouts[i + 1 :]
                    if a.muscle_groups & b.muscle_groups
                ]
                if rests:
                    notes.append(f"Shortest rest for any muscle: {min(rests):.0f} hours (minimum 48, ideal 72).")
                return WeeklyPlan(week_start, split.name, workouts, free_days, target, notes)

    notes.append("There isn't a free window long enough for a full session this week.")
    return WeeklyPlan(week_start, "Rest week", [], free_days, target, notes)


def local_now(prefs: Preferences) -> datetime:
    return datetime.now(ZoneInfo(prefs.timezone))
