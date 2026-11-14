"""Sample calendars so anyone (including recruiters) can try the app without logging in."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import BusyBlock

# (weekday, start, end, title) — weekday 0 = Monday
_BUSY_WEEK = [
    (0, "06:30", "21:00", "Classes + part-time job"),
    (1, "09:00", "17:00", "Lab"),
    (1, "18:00", "19:30", "Intramural soccer"),
    (2, "06:30", "21:00", "Classes + part-time job"),
    (3, "08:00", "20:30", "Classes + study group"),
    (4, "09:00", "17:00", "Classes"),
    (5, "07:00", "22:00", "Friend's wedding"),
    (6, "06:00", "22:00", "Travel home"),
]

_LIGHT_WEEK = [
    (0, "09:00", "17:00", "Work"),
    (1, "09:00", "17:00", "Work"),
    (2, "09:00", "17:00", "Work"),
    (2, "18:30", "20:00", "Dinner with friends"),
    (3, "09:00", "17:00", "Work"),
    (4, "09:00", "17:00", "Work"),
    (5, "10:00", "12:00", "Brunch"),
]

DEMO_CALENDARS = {
    "busy": ("Busy student week", _BUSY_WEEK),
    "light": ("Normal 9-to-5 week", _LIGHT_WEEK),
}


def demo_busy_blocks(name: str, start: date, timezone: str, days: int = 7) -> list[BusyBlock]:
    """The demo week's events for the `days` dates beginning at `start` (any weekday)."""
    tz = ZoneInfo(timezone)
    _, rows = DEMO_CALENDARS[name]
    blocks = []
    for offset in range(days):
        day = start + timedelta(days=offset)
        for weekday, begin, end, title in rows:
            if weekday == day.weekday():
                blocks.append(
                    BusyBlock(
                        datetime.combine(day, time.fromisoformat(begin), tzinfo=tz),
                        datetime.combine(day, time.fromisoformat(end), tzinfo=tz),
                        title,
                    )
                )
    return blocks
