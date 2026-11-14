"""Plain-English preferences in, structured settings out (and a friendly explanation back).

Design rule: the language model only *translates* — free text into structured
preferences, and the planner's reasons into a readable summary. Every actual
scheduling decision is made by the deterministic, tested code in
``app.scheduler``. That keeps plans predictable and makes the AI optional:
without an API key, a small rule-based parser does the same job (less flexibly).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models import BusyBlock, Commitment, Preferences, TimeOfDay, WeeklyPlan

log = logging.getLogger(__name__)

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class RecurringBusy:
    """A weekly commitment mentioned in the notes, e.g. "soccer Tuesdays 6-7:30pm"."""

    weekday: int
    start: time
    end: time
    title: str


@dataclass
class ParsedNotes:
    preferred_time: TimeOfDay | None = None
    commitment: Commitment | None = None
    session_minutes: int | None = None
    earliest_start: time | None = None
    latest_end: time | None = None
    blocked_weekdays: set[int] = field(default_factory=set)
    allowed_weekdays: set[int] = field(default_factory=set)  # days to un-block ("Sundays are fine now")
    recurring_busy: list[RecurringBusy] = field(default_factory=list)
    source: str = "rules"  # "ai" or "rules"

    def is_empty(self) -> bool:
        return not self.describe()

    def describe(self) -> list[str]:
        """Short human-readable list of what these notes change, e.g. ["mornings", "no Sundays"]."""
        short = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        parts: list[str] = []
        if self.preferred_time is not None:
            parts.append("any time of day" if self.preferred_time is TimeOfDay.ANY else f"{self.preferred_time.value}s")
        if self.commitment is not None:
            parts.append(f"{self.commitment.value} commitment")
        if self.session_minutes is not None:
            parts.append(f"{self.session_minutes}-minute sessions")
        if self.earliest_start is not None:
            parts.append(f"nothing before {self.earliest_start.strftime('%H:%M')}")
        if self.latest_end is not None:
            parts.append(f"done by {self.latest_end.strftime('%H:%M')}")
        parts += [f"no {short[d]}" for d in sorted(self.blocked_weekdays)]
        parts += [f"{short[d]} is open again" for d in sorted(self.allowed_weekdays)]
        parts += [
            f"{r.title} {short[r.weekday]} {r.start.strftime('%H:%M')}-{r.end.strftime('%H:%M')}"
            for r in self.recurring_busy
        ]
        return parts

    def apply(self, prefs: Preferences) -> Preferences:
        """Return a copy of ``prefs`` with these notes layered on top."""
        updates = {
            k: v
            for k, v in {
                "preferred_time": self.preferred_time,
                "commitment": self.commitment,
                "session_minutes": self.session_minutes,
                "earliest_start": self.earliest_start,
                "latest_end": self.latest_end,
            }.items()
            if v is not None
        }
        return replace(prefs, blocked_weekdays=prefs.blocked_weekdays | self.blocked_weekdays, **updates)

    def busy_blocks(self, week_start, timezone: str) -> list[BusyBlock]:
        tz = ZoneInfo(timezone)
        blocks = []
        for r in self.recurring_busy:
            day = week_start + timedelta(days=r.weekday)
            blocks.append(
                BusyBlock(datetime.combine(day, r.start, tzinfo=tz), datetime.combine(day, r.end, tzinfo=tz), r.title)
            )
        return blocks


# ============================================================ rule-based ===
_DAY_RE = r"(mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?s?"
_TIME_RE = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"


def _weekday(token: str) -> int:
    return next(i for i, name in enumerate(WEEKDAYS) if name.startswith(token[:3].lower()))


def _to_time(hour: str, minute: str | None, ampm: str | None, fallback_ampm: str | None = None) -> time:
    h, m = int(hour), int(minute or 0)
    ampm = ampm or fallback_ampm
    if ampm == "pm" and h < 12:
        h += 12
    if ampm == "am" and h == 12:
        h = 0
    return time(min(h, 23), min(m, 59))


def parse_notes_with_rules(text: str) -> ParsedNotes:
    """Small keyword parser used when no API key is set. Handles common phrasings only."""
    t = text.lower()
    out = ParsedNotes(source="rules")

    if re.search(r"(no|hate|not a|avoid)\s+(early\s+)?morning", t):
        out.earliest_start = time(10, 0)
        out.preferred_time = TimeOfDay.EVENING
    elif re.search(r"morning|before (work|class)|early", t):
        out.preferred_time = TimeOfDay.MORNING
    elif re.search(r"lunch|midday|noon", t):
        out.preferred_time = TimeOfDay.MIDDAY
    elif re.search(r"evening|after (work|class)|night", t):
        out.preferred_time = TimeOfDay.EVENING

    if re.search(r"dedicated|serious|committed|bodybuild|compet", t):
        out.commitment = Commitment.DEDICATED
    elif re.search(r"busy|consistent|maintain|just start|beginner|light", t):
        out.commitment = Commitment.LIGHT
    elif re.search(r"moderate|normal week", t):
        out.commitment = Commitment.MODERATE

    if m := re.search(r"(\d{2,3})\s*(?:min|minute)", t):
        out.session_minutes = max(20, min(180, int(m.group(1))))
    elif m := re.search(r"(\d(?:\.\d)?)\s*(?:hour|hr)", t):
        out.session_minutes = max(20, min(180, int(float(m.group(1)) * 60)))

    # "no gym on sundays", "never on saturday", "sundays off", "rest on friday"
    for m in re.finditer(rf"\b(?:no|never|not|rest|off)\b[^.,;]{{0,20}}?\b{_DAY_RE}", t):
        out.blocked_weekdays.add(_weekday(m.group(1)))
    for m in re.finditer(rf"\b{_DAY_RE}\s+(?:off|are off|is off|rest)", t):
        out.blocked_weekdays.add(_weekday(m.group(1)))
    # "sundays are fine", "I can train on saturdays"
    for m in re.finditer(rf"\b{_DAY_RE}\s+(?:are|is)\s+(?:fine|ok|okay|free|open)", t):
        out.allowed_weekdays.add(_weekday(m.group(1)))
    for m in re.finditer(rf"\bcan\s+(?:train|lift|go|do)\b[^.,;]{{0,15}}?\b{_DAY_RE}", t):
        out.allowed_weekdays.add(_weekday(m.group(1)))
    out.blocked_weekdays -= out.allowed_weekdays

    # "soccer on tuesdays 6-7:30pm", "work saturday 9am to 5pm"
    pattern = rf"([a-z][a-z ]{{1,30}}?)\s+(?:on\s+)?{_DAY_RE}\s+(?:from\s+)?{_TIME_RE}\s*(?:-|to|until)\s*{_TIME_RE}"
    for m in re.finditer(pattern, t):
        title, day, h1, m1, ap1, h2, m2, ap2 = m.groups()
        end = _to_time(h2, m2, ap2)
        start = _to_time(h1, m1, ap1, fallback_ampm=ap2)
        if start < end:
            title = re.sub(r"^(i have|have|i've got|got|i do|and|also)\s+", "", title.strip())
            out.recurring_busy.append(RecurringBusy(_weekday(day), start, end, title.capitalize() or "Busy"))
    return out


# ================================================================== Gemini ===
# JSON Schema for Gemini's structured output: the model must answer with exactly this shape.
PREFERENCES_SCHEMA = {
    "type": "object",
    "properties": {
        "preferred_time": {"type": "string", "enum": [t.value for t in TimeOfDay]},
        "commitment": {
            "type": "string",
            "enum": [c.value for c in Commitment],
            "description": "light = busy / just staying consistent; dedicated = training is a top priority",
        },
        "session_minutes": {"type": "integer", "minimum": 20, "maximum": 180},
        "earliest_start": {"type": "string", "description": "HH:MM, 24h. Earliest they'd start a workout."},
        "latest_end": {"type": "string", "description": "HH:MM, 24h. Latest a workout may end."},
        "blocked_weekdays": {
            "type": "array",
            "items": {"type": "string", "enum": WEEKDAYS},
            "description": "Days they never want to train.",
        },
        "allowed_weekdays": {
            "type": "array",
            "items": {"type": "string", "enum": WEEKDAYS},
            "description": "Days they previously blocked but now say are fine for training.",
        },
        "recurring_busy": {
            "type": "array",
            "description": "Weekly commitments mentioned in the note that may not be on their calendar.",
            "items": {
                "type": "object",
                "properties": {
                    "weekday": {"type": "string", "enum": WEEKDAYS},
                    "start": {"type": "string", "description": "HH:MM, 24h"},
                    "end": {"type": "string", "description": "HH:MM, 24h"},
                    "title": {"type": "string"},
                },
                "required": ["weekday", "start", "end", "title"],
            },
        },
    },
}

PARSE_INSTRUCTIONS = (
    "You extract workout-scheduling preferences from a short note. "
    "Only include fields the note actually mentions. Use 24-hour HH:MM times."
)
EXPLAIN_INSTRUCTIONS = (
    "You are a friendly strength coach. In 2-3 short sentences, explain this week's workout "
    "plan to the user using ONLY the facts given. Mention why this split was chosen. "
    "No lists, no markdown, no made-up details."
)


def _client():
    """A Gemini client, or None when no API key is set (the app then uses the rule-based parser)."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None
    from google import genai

    return genai.Client(api_key=api_key)


def _model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")


def _notes_from_ai_json(data: dict) -> ParsedNotes:
    out = ParsedNotes(source="ai")
    if v := data.get("preferred_time"):
        out.preferred_time = TimeOfDay(v)
    if v := data.get("commitment"):
        out.commitment = Commitment(v)
    if v := data.get("session_minutes"):
        out.session_minutes = max(20, min(180, int(v)))
    if v := data.get("earliest_start"):
        out.earliest_start = time.fromisoformat(v)
    if v := data.get("latest_end"):
        out.latest_end = time.fromisoformat(v)
    out.blocked_weekdays = {WEEKDAYS.index(d) for d in data.get("blocked_weekdays", [])}
    out.allowed_weekdays = {WEEKDAYS.index(d) for d in data.get("allowed_weekdays", [])}
    out.blocked_weekdays -= out.allowed_weekdays
    for r in data.get("recurring_busy", []):
        start, end = time.fromisoformat(r["start"]), time.fromisoformat(r["end"])
        if start < end:
            out.recurring_busy.append(RecurringBusy(WEEKDAYS.index(r["weekday"]), start, end, r["title"]))
    return out


def parse_notes(text: str, client=None) -> ParsedNotes:
    """Turn the user's free-text note into structured preferences.

    Uses Gemini when an API key is configured, otherwise (or on any error) the rule-based parser.
    """
    if not text.strip():
        return ParsedNotes(source="none")
    client = client or _client()
    if client is None:
        return parse_notes_with_rules(text)
    try:
        from google.genai import types

        response = client.models.generate_content(
            model=_model(),
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=PARSE_INSTRUCTIONS,
                response_mime_type="application/json",
                response_json_schema=PREFERENCES_SCHEMA,
            ),
        )
        return _notes_from_ai_json(json.loads(response.text))
    except Exception:
        log.exception("AI preference parsing failed; falling back to rules")
        return parse_notes_with_rules(text)


def fallback_explanation(plan: WeeklyPlan) -> str:
    if not plan.workouts:
        return "Your calendar is packed this week, so this is a rest week. " + " ".join(plan.notes)
    days = ", ".join(w.start.strftime("%A") for w in plan.workouts)
    return f"This week you're doing {plan.split_name} on {days}. " + " ".join(plan.notes)


def explain_plan(plan: WeeklyPlan, client=None) -> str:
    """A short, friendly summary of why the plan looks the way it does."""
    client = client or _client()
    if client is None:
        return fallback_explanation(plan)
    facts = {
        "split": plan.split_name,
        "sessions": [{"name": w.name, "when": w.start.strftime("%A %I:%M %p")} for w in plan.workouts],
        "planner_reasons": plan.notes,
    }
    try:
        from google.genai import types

        response = client.models.generate_content(
            model=_model(),
            contents=json.dumps(facts),
            config=types.GenerateContentConfig(system_instruction=EXPLAIN_INSTRUCTIONS),
        )
        text = (response.text or "").strip()
        return text or fallback_explanation(plan)
    except Exception:
        log.exception("AI explanation failed; using fallback")
        return fallback_explanation(plan)


# ============================================================ AI Coach chat ===
COACH_INSTRUCTIONS = (
    "You are the AI Coach inside GymScheduleBot 2.0, a muscle-building workout planner. The app's own scheduling code "
    "decides when workouts happen; your job is to (1) turn the user's message into setting changes "
    "and (2) write a short, friendly reply (1-2 sentences, no markdown). "
    "Only put a field in `changes` if the user asked for it. If they ask a question, answer it using "
    "ONLY the plan facts given, and leave `changes` empty. Never invent workouts, times or numbers. "
    "Use 24-hour HH:MM times."
)

COACH_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "1-2 friendly sentences to the user."},
        "changes": PREFERENCES_SCHEMA,
    },
    "required": ["reply"],
}


@dataclass
class CoachTurn:
    changes: ParsedNotes
    reply: str | None  # None means "let the app write the reply"


def plan_facts(plan: WeeklyPlan, prefs: Preferences) -> dict:
    """The only things the coach is allowed to talk about."""
    return {
        "split": plan.split_name,
        "sessions": [
            {"workout": w.name, "day": w.start.strftime("%A"), "time": w.start.strftime("%H:%M")} for w in plan.workouts
        ],
        "open_days": len(plan.free_days),
        "settings": {
            "commitment": prefs.commitment.value,
            "preferred_time": prefs.preferred_time.value,
            "session_minutes": prefs.session_minutes,
            "blocked_days": [WEEKDAYS[d] for d in sorted(prefs.blocked_weekdays)],
        },
        "planner_reasons": plan.notes,
    }


def coach_turn(message: str, plan: WeeklyPlan, prefs: Preferences, client=None) -> CoachTurn:
    """Read one chat message: what should change, and what should the coach say?"""
    client = client or _client()
    if client is None:
        return CoachTurn(parse_notes_with_rules(message), None)
    try:
        from google.genai import types

        prompt = json.dumps({"current_plan": plan_facts(plan, prefs), "user_message": message})
        response = client.models.generate_content(
            model=_model(),
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=COACH_INSTRUCTIONS,
                response_mime_type="application/json",
                response_json_schema=COACH_SCHEMA,
            ),
        )
        data = json.loads(response.text)
        reply = str(data.get("reply", "")).strip() or None
        return CoachTurn(_notes_from_ai_json(data.get("changes") or {}), reply)
    except Exception:
        log.exception("AI coach failed; falling back to rules")
        return CoachTurn(parse_notes_with_rules(message), None)


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def plan_summary(plan: WeeklyPlan) -> str:
    """One factual sentence about the plan, written by code (never by the AI)."""
    if not plan.workouts:
        return "Your week is packed, so this is a rest week."
    days = _join([w.start.strftime("%a") for w in plan.workouts])
    count = len(plan.workouts)
    return f"{plan.split_name}: {count} session{'s' if count != 1 else ''} on {days}."


def coach_intro(plan: WeeklyPlan) -> str:
    """The coach's first message when the page opens (no AI call, so page loads stay fast and free)."""
    if not plan.workouts:
        return "Your calendar is packed this week, so I've planned a rest week. Want me to try shorter sessions?"
    count = len(plan.workouts)
    days = _join([w.start.strftime("%a") for w in plan.workouts])
    return (
        f"I've planned {count} session{'s' if count != 1 else ''} ({plan.split_name}) around your calendar "
        f"on {days}. What would you like to change?"
    )


def fallback_reply(changes: ParsedNotes, plan: WeeklyPlan) -> str:
    if changes.is_empty():
        return (
            "I can change your times, session length, days off or how hard you train. "
            'Try "I prefer mornings" or "no gym on Sundays".'
        )
    return f"Got it: {_join(changes.describe())}. I've re-planned your week."


# ======================================================= schedule screenshots ===
SCREENSHOT_INSTRUCTIONS = (
    "You read screenshots of schedules (class timetables, calendar week views, work rosters) and list "
    "every event as a weekly event: its title, weekday, start and end time (24-hour HH:MM). "
    "Only list events you can actually see; skip anything unreadable. Ignore all-day items. "
    "Also write a one-sentence friendly `reply` about what you found. If the image is not a schedule, "
    "return no events and say so in the reply."
)

SCREENSHOT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "weekday": {"type": "string", "enum": WEEKDAYS},
                    "start": {"type": "string", "description": "HH:MM, 24h"},
                    "end": {"type": "string", "description": "HH:MM, 24h"},
                },
                "required": ["title", "weekday", "start", "end"],
            },
        },
    },
    "required": ["reply", "events"],
}

SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


class ScreenshotUnavailable(RuntimeError):
    """Reading screenshots needs Gemini; raised when no API key is configured."""


def read_schedule_image(image: bytes, mime_type: str, note: str = "", client=None) -> tuple[list[dict], str]:
    """Ask Gemini to turn a schedule screenshot into weekly events.

    Returns (events, reply). Each event is {"title", "weekday" (0=Mon), "start", "end"} with
    `start`/`end` as datetime.time; the caller validates and stores them.
    """
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise ValueError("Please send a PNG, JPG or WebP image.")
    client = client or _client()
    if client is None:
        raise ScreenshotUnavailable("Reading screenshots needs Gemini, which isn't set up on this server.")
    from google.genai import types

    response = client.models.generate_content(
        model=_model(),
        contents=[types.Part.from_bytes(data=image, mime_type=mime_type), note or "Read this schedule."],
        config=types.GenerateContentConfig(
            system_instruction=SCREENSHOT_INSTRUCTIONS,
            response_mime_type="application/json",
            response_json_schema=SCREENSHOT_SCHEMA,
        ),
    )
    data = json.loads(response.text)
    events = []
    for e in data.get("events", [])[:60]:
        try:
            start, end = time.fromisoformat(e["start"]), time.fromisoformat(e["end"])
            weekday = WEEKDAYS.index(e["weekday"])
        except (KeyError, ValueError):
            continue
        if start < end:
            events.append({"title": str(e.get("title") or "Busy"), "weekday": weekday, "start": start, "end": end})
    return events, str(data.get("reply") or "").strip()
