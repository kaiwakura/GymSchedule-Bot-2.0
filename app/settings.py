"""A visitor's saved planner settings.

Settings live in the signed session cookie, so the AI Coach "remembers" how
someone likes to train across visits in the same browser. Everything here is
small, JSON-safe data; the scheduler only ever sees a ``Preferences`` object.
(The user's own calendar events are bigger, so the browser stores those; see
``app.sources.custom``.)
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import time
from typing import Any

from app.ai.assistant import ParsedNotes
from app.models import Commitment, Preferences, TimeOfDay

# "mine" = only the events the user added; "google" and the demos also include them.
SOURCES = ("demo:busy", "demo:light", "mine", "google")


@dataclass
class Settings:
    source: str = "demo:busy"
    timezone: str = "America/Los_Angeles"
    commitment: str = Commitment.MODERATE.value
    preferred_time: str = TimeOfDay.EVENING.value  # most people train after work or class
    session_minutes: int = 60
    buffer_minutes: int = 15
    earliest_start: str = "06:00"
    latest_end: str = "22:00"
    blocked_weekdays: list[int] = field(default_factory=list)

    # ------------------------------------------------------------- storage ---
    @classmethod
    def from_session(cls, data: dict[str, Any] | None) -> Settings:
        """Rebuild settings from the cookie, ignoring anything unknown or malformed."""
        settings = cls()
        if not isinstance(data, dict):
            return settings
        for key, value in data.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        return settings.validated()

    def to_session(self) -> dict[str, Any]:
        return asdict(self)

    def validated(self) -> Settings:
        """Clamp every field to a safe value (the cookie could be old or edited)."""
        defaults = Settings()
        if self.source not in SOURCES:
            self.source = defaults.source
        if self.commitment not in {c.value for c in Commitment}:
            self.commitment = defaults.commitment
        if self.preferred_time not in {t.value for t in TimeOfDay}:
            self.preferred_time = defaults.preferred_time
        try:
            self.session_minutes = max(20, min(180, int(self.session_minutes)))
            self.buffer_minutes = max(0, min(60, int(self.buffer_minutes)))
            time.fromisoformat(self.earliest_start)
            time.fromisoformat(self.latest_end)
        except (TypeError, ValueError):
            return defaults
        self.blocked_weekdays = sorted({int(d) for d in self.blocked_weekdays if 0 <= int(d) <= 6})
        return self

    # ------------------------------------------------------------ planning ---
    def to_preferences(self) -> Preferences:
        return Preferences(
            timezone=self.timezone,
            commitment=Commitment(self.commitment),
            preferred_time=TimeOfDay(self.preferred_time),
            session_minutes=self.session_minutes,
            buffer_minutes=self.buffer_minutes,
            earliest_start=time.fromisoformat(self.earliest_start),
            latest_end=time.fromisoformat(self.latest_end),
            blocked_weekdays=set(self.blocked_weekdays),
        )

    def apply(self, notes: ParsedNotes) -> Settings:
        """Layer changes from a chat message on top of these settings.

        Weekly commitments in the message ("soccer Tuesdays 6-7:30pm") aren't settings:
        the chat endpoint turns those into the user's own calendar events instead.
        """
        if notes.preferred_time is not None:
            self.preferred_time = notes.preferred_time.value
        if notes.commitment is not None:
            self.commitment = notes.commitment.value
        if notes.session_minutes is not None:
            self.session_minutes = notes.session_minutes
        if notes.earliest_start is not None:
            self.earliest_start = notes.earliest_start.strftime("%H:%M")
        if notes.latest_end is not None:
            self.latest_end = notes.latest_end.strftime("%H:%M")
        blocked = (set(self.blocked_weekdays) | notes.blocked_weekdays) - notes.allowed_weekdays
        self.blocked_weekdays = sorted(blocked)
        return self.validated()
