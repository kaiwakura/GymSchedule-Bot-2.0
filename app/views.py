"""Turn a weekly plan into what the page shows: a calendar grid, one column per day.

Like Google Calendar, each day is a column of hours. Events and workouts are
placed at their real times: ``top`` and ``height`` are percentages of the
column, so the CSS just positions them. Overlapping events share the width
side by side (``left`` / ``width``). Pure functions, no I/O.
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


def grid_hours(prefs: Preferences) -> tuple[int, int]:
    """First and last hour shown in the grid: 6 AM to 10 PM, widened to fit the user's window."""
    first = min(6, prefs.earliest_start.hour)
    last = max(22, prefs.latest_end.hour + (1 if prefs.latest_end.minute else 0))
    return first, min(last, 24)


def _position(start: datetime, end: datetime, grid_start: datetime, grid_end: datetime) -> dict | None:
    """Top/height in % of the column, or None if the block is outside the visible hours."""
    start, end = max(start, grid_start), min(end, grid_end)
    if start >= end:
        return None
    total = (grid_end - grid_start).total_seconds()
    return {
        "top": round((start - grid_start).total_seconds() / total * 100, 3),
        "height": round((end - start).total_seconds() / total * 100, 3),
    }


def assign_lanes(items: list[dict]) -> None:
    """Give overlapping blocks side-by-side lanes (sets ``left`` and ``width`` in %).

    Items need ``_start``/``_end``. Walk through them in time order; a "cluster" is a
    run of blocks that overlap each other. Each block takes the first free lane, and
    every block in a cluster gets width = 100% / (lanes used by that cluster).
    """
    items.sort(key=lambda i: (i["_start"], -i["_end"].timestamp()))
    cluster: list[dict] = []
    lane_ends: list[datetime] = []
    cluster_end: datetime | None = None

    def close() -> None:
        for item in cluster:
            item["width"] = round(100 / len(lane_ends), 3)
            item["left"] = round(item["lane"] * item["width"], 3)

    for item in items:
        if cluster_end is not None and item["_start"] >= cluster_end:
            close()
            cluster, lane_ends, cluster_end = [], [], None
        lane = next((i for i, end in enumerate(lane_ends) if end <= item["_start"]), None)
        if lane is None:
            lane_ends.append(item["_end"])
            lane = len(lane_ends) - 1
        else:
            lane_ends[lane] = item["_end"]
        item["lane"] = lane
        cluster.append(item)
        cluster_end = item["_end"] if cluster_end is None else max(cluster_end, item["_end"])
    if cluster:
        close()


def build_days(
    plan: WeeklyPlan,
    busy: list[BusyBlock],
    prefs: Preferences,
    today: date | None = None,
    now: datetime | None = None,
) -> list[dict]:
    tz = ZoneInfo(prefs.timezone)
    first_hour, last_hour = grid_hours(prefs)
    workouts_by_day = {w.day: w for w in plan.workouts}
    days = []
    for offset in range(7):
        day = plan.week_start + timedelta(days=offset)
        day_start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        grid_start = day_start + timedelta(hours=first_hour)
        grid_end = day_start + timedelta(hours=last_hour)

        events = []
        for b in sorted(busy, key=lambda b: b.start):
            start, end = b.start.astimezone(tz), b.end.astimezone(tz)
            if end <= day_start or start >= day_end:
                continue
            label = short_clock(start) if start >= day_start else f"until {short_clock(end)}"
            event = {"time": label, "title": b.title, "id": b.event_id}
            pos = _position(start, end, grid_start, grid_end)
            if pos:
                event.update(pos)
                event["range"] = f"{short_clock(start)} – {short_clock(end)}"
                event["_start"], event["_end"] = max(start, grid_start), min(end, grid_end)
            events.append(event)
        assign_lanes([e for e in events if "_start" in e])
        for e in events:
            e.pop("_start", None)
            e.pop("_end", None)

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
                "range": f"{clock(workout.start)} – {clock(workout.end)}",
                "minutes": int((workout.end - workout.start).total_seconds() // 60),
                "exercises": [str(e) for e in workout.exercises],
                "sets": sum(e.sets for e in workout.exercises),
                "id": f"w{offset}",
                **(_position(workout.start, workout.end, grid_start, grid_end) or {"top": 0, "height": 0}),
            }
        if blocked:
            rest = ("Day off", "Your choice")
        elif past:
            rest = ("Past", "Already happened")
        elif day not in plan.free_days:
            rest = ("Rest", "No free window")
        else:
            rest = ("Rest", "Recover well")

        now_top = None
        if now is not None and day == now.date():
            pos = _position(now, now + timedelta(minutes=1), grid_start, grid_end)
            now_top = pos["top"] if pos else None

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
                "now_top": now_top,
            }
        )
    return days


def hour_labels(prefs: Preferences) -> list[dict]:
    """Labels for the left gutter: '6 AM', '7 AM', ... with their position in %."""
    first, last = grid_hours(prefs)
    span = last - first
    labels = []
    for h in range(first, last):
        suffix = "AM" if h < 12 else "PM"
        labels.append({"label": f"{(h - 1) % 12 + 1} {suffix}", "top": round((h - first) / span * 100, 3)})
    return labels


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
