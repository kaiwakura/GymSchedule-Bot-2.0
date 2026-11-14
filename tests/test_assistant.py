import json
from datetime import time
from types import SimpleNamespace

from app.ai.assistant import (
    coach_intro,
    coach_turn,
    explain_plan,
    fallback_reply,
    parse_notes,
    parse_notes_with_rules,
    plan_summary,
)
from app.models import Commitment, Preferences, TimeOfDay
from app.scheduler.planner import build_plan
from tests.conftest import MONDAY


# ------------------------------------------------------------ rule parser ---
def test_rules_detect_time_of_day():
    assert parse_notes_with_rules("I like to lift in the morning").preferred_time is TimeOfDay.MORNING
    assert parse_notes_with_rules("after work works best").preferred_time is TimeOfDay.EVENING
    no_mornings = parse_notes_with_rules("I hate mornings")
    assert no_mornings.preferred_time is TimeOfDay.EVENING
    assert no_mornings.earliest_start == time(10, 0)


def test_rules_detect_blocked_days():
    assert parse_notes_with_rules("no gym on sundays").blocked_weekdays == {6}
    assert parse_notes_with_rules("Saturdays off please").blocked_weekdays == {5}
    # "piano" ends in "no" — must not be read as "no ... monday"
    assert parse_notes_with_rules("piano on monday 5-6pm").blocked_weekdays == set()


def test_rules_detect_recurring_commitment():
    notes = parse_notes_with_rules("I have soccer on tuesdays 6-7:30pm")
    [r] = notes.recurring_busy
    assert (r.weekday, r.start, r.end, r.title) == (1, time(18, 0), time(19, 30), "Soccer")


def test_rules_detect_length_and_commitment():
    notes = parse_notes_with_rules("super busy semester, 45 min sessions max")
    assert notes.commitment is Commitment.LIGHT
    assert notes.session_minutes == 45


def test_notes_apply_to_preferences():
    prefs = Preferences(blocked_weekdays={0})
    updated = parse_notes_with_rules("evenings, no workouts on fridays").apply(prefs)
    assert updated.preferred_time is TimeOfDay.EVENING
    assert updated.blocked_weekdays == {0, 4}
    assert prefs.blocked_weekdays == {0}  # original untouched


def test_empty_note_changes_nothing():
    assert parse_notes("   ").apply(Preferences()) == Preferences()


# ------------------------------------------------- Gemini path (fake client) ---
class FakeGemini:
    """Stands in for google.genai.Client: client.models.generate_content(...) -> response.text"""

    def __init__(self, text=None, error=None):
        self.text, self.error, self.calls = text, error, []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def test_ai_json_output_is_converted():
    ai_json = {
        "preferred_time": "evening",
        "commitment": "dedicated",
        "blocked_weekdays": ["sunday"],
        "recurring_busy": [{"weekday": "tuesday", "start": "18:00", "end": "19:30", "title": "Soccer"}],
    }
    client = FakeGemini(text=json.dumps(ai_json))
    notes = parse_notes("evenings, very committed, no sundays, soccer tue 6-7:30pm", client=client)
    assert notes.source == "ai"
    assert notes.commitment is Commitment.DEDICATED
    assert notes.blocked_weekdays == {6}
    assert notes.recurring_busy[0].start == time(18, 0)
    config = client.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["properties"]["commitment"]["enum"] == ["light", "moderate", "dedicated"]


def test_ai_bad_json_falls_back_to_rules():
    notes = parse_notes("mornings please", client=FakeGemini(text="not json"))
    assert notes.source == "rules"
    assert notes.preferred_time is TimeOfDay.MORNING


def test_ai_failure_falls_back_to_rules():
    notes = parse_notes("mornings please", client=FakeGemini(error=RuntimeError("API down")))
    assert notes.source == "rules"
    assert notes.preferred_time is TimeOfDay.MORNING


def test_explanation_uses_ai_text():
    plan = build_plan([], MONDAY, Preferences())
    assert explain_plan(plan, client=FakeGemini(text="  Great week ahead!  ")) == "Great week ahead!"


def test_explanation_fallback_without_ai():
    plan = build_plan([], MONDAY, Preferences())
    client = FakeGemini(error=RuntimeError("API down"))
    assert plan.split_name in explain_plan(plan, client=client)


# ------------------------------------------------------------- AI Coach chat ---
def test_rules_unblock_days_and_light_week():
    notes = parse_notes_with_rules("sundays are fine now, make it a light week")
    assert notes.allowed_weekdays == {6}
    assert notes.commitment is Commitment.LIGHT


def test_describe_lists_changes_in_plain_words():
    notes = parse_notes_with_rules("mornings, 45 min, no gym on sundays, soccer on tuesdays 6-7:30pm")
    assert notes.describe() == ["mornings", "45-minute sessions", "no Sun", "Soccer Tue 18:00-19:30"]
    assert parse_notes_with_rules("hello").is_empty()


def test_coach_turn_with_gemini_returns_reply_and_changes():
    plan = build_plan([], MONDAY, Preferences())
    answer = {"reply": "Mornings it is!", "changes": {"preferred_time": "morning"}}
    client = FakeGemini(text=json.dumps(answer))
    turn = coach_turn("I like mornings", plan, Preferences(), client=client)
    assert turn.reply == "Mornings it is!"
    assert turn.changes.preferred_time is TimeOfDay.MORNING
    prompt = json.loads(client.calls[0]["contents"])
    assert prompt["current_plan"]["split"] == plan.split_name  # the coach only sees plan facts
    assert prompt["user_message"] == "I like mornings"


def test_coach_turn_falls_back_to_rules_on_bad_ai_output():
    plan = build_plan([], MONDAY, Preferences())
    turn = coach_turn("evenings please", plan, Preferences(), client=FakeGemini(text="{oops"))
    assert turn.reply is None
    assert turn.changes.preferred_time is TimeOfDay.EVENING


def test_coach_text_written_by_code():
    plan = build_plan([], MONDAY, Preferences())
    assert plan_summary(plan).startswith("Upper/Lower x2: 4 sessions on")
    assert "What would you like to change?" in coach_intro(plan)
    assert "Got it: mornings" in fallback_reply(parse_notes_with_rules("mornings"), plan)
