"""Turn a weekly plan into what the page shows: one column per day.

Each day gets a busyness tag (Light / Medium / Busy / Off), the events from the
calendar, and either a workout card or a rest card. Pure functions, no I/O.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import BusyBlock, Preferences, WeeklyPlan
from app.scheduler.availability import merge_intervals
from app.scheduler.splits import TARGET_WEEKLY_SETS, weekly_sets

LIGHT_UNDER_MINUTES = 4 * 60  # a few meetings
MEDIUM_UNDER_MINUTES = 9 * 60  # a normal 9-to-5 workday is Medium; anything more is Busy


def clock(dt: datetime) -> str:
    """7:05 AM style, without a leading zero."""
    return dt.strftime("%I:%M %p").lstrip("0")


def short_clock(dt: datetime) -> str:
    """Compact time for event chips: 9:00 AM, 4:30 PM."""
    return clock(dt)


def busy_minutes(day: date, busy: list[BusyBlock], prefs: Preferences) -> int:
    """Minutes of the day's waking window (earliest start to latest end) that are booked."""
    tz = ZoneInfo(prefs.timezone)
    window_start = datetime.combine(day, prefs.earliest_start, tzinfo=tz)
    window_end = datetime.combine(day, prefs.latest_end, tzinfo=tz)
    clipped = [
        (max(b.start, window_start), min(b.end, window_end))
        for b in busy
        if b.end > window_start and b.start < window_end
    ]
    return int(sum((end - start).total_seconds() for start, end in merge_intervals(clipped)) // 60)


def busyness(minutes: int) -> str:
    if minutes < LIGHT_UNDER_MINUTES:
        return "light"
    if minutes < MEDIUM_UNDER_MINUTES:
        return "medium"
    return "busy"


def slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def build_days(plan: WeeklyPlan, busy: list[BusyBlock], prefs: Preferences, today: date | None = None) -> list[dict]:
    tz = ZoneInfo(prefs.timezone)
    workouts_by_day = {w.day: w for w in plan.workouts}
    days = []
    for offset in range(7):
        day = plan.week_start + timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        day_end = day_start + timedelta(days=1)

        events = []
        for b in sorted(busy, key=lambda b: b.start):
            start, end = b.start.astimezone(tz), b.end.astimezone(tz)
            if end <= day_start or start >= day_end:
                continue
            label = short_clock(start) if start >= day_start else f"until {short_clock(end)}"
            events.append({"time": label, "title": b.title, "id": b.event_id})

        blocked = day.weekday() in prefs.blocked_weekdays
        past = today is not None and day < today
        tag = "off" if blocked else busyness(busy_minutes(day, busy, prefs))

        workout = workouts_by_day.get(day)
        card = None
        if workout:
            card = {
                "name": workout.name,
                "slug": slug(workout.name),
                "focus": workout.focus,
                "time": clock(workout.start),
                "minutes": int((workout.end - workout.start).total_seconds() // 60),
                "exercises": [str(e) for e in workout.exercises],
                "id": f"w{offset}",
            }
        if blocked:
            rest = ("Day off", "Your choice")
        elif past:
            rest = ("Past", "Already happened")
        elif day not in plan.free_days:
            rest = ("Rest", "No free window")
        else:
            rest = ("Rest", "Recover well")

        days.append(
            {
                "weekday": day.strftime("%a").upper(),
                "weekday_index": day.weekday(),
                "weekday_long": day.strftime("%A"),
                "iso": day.isoformat(),
                "date_num": day.day,
                "date_label": day.strftime("%b %d").replace(" 0", " "),
                "tag": tag,
                "tag_label": tag.capitalize(),
                "events": events,
                "workout": card,
                "rest_title": rest[0],
                "rest_sub": rest[1],
                "is_today": today == day,
            }
        )
    return days


def summary_line(plan: WeeklyPlan) -> str:
    count = len(plan.workouts)
    rest = 7 - count
    return f"{count} workout{'s' if count != 1 else ''} · {rest} rest day{'s' if rest != 1 else ''}"


def volume_chips(plan: WeeklyPlan) -> list[dict]:
    """Weekly direct sets per muscle, flagged when they reach the muscle-building target."""
    return [
        {"muscle": muscle.capitalize(), "sets": sets, "on_target": sets >= TARGET_WEEKLY_SETS}
        for muscle, sets in weekly_sets(plan.workouts).items()
    ]
