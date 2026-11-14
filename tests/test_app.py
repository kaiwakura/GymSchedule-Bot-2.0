"""Integration tests: the page, the toolbar, your own events, the AI Coach chat and exports."""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from icalendar import Calendar

import app.main as main
from app.main import app

TODAY_EVENT = {"id": "e_test1", "title": "Calc lecture", "start": "09:00", "end": "10:30", "repeat": "weekly"}


@pytest.fixture
def client(monkeypatch):
    # Rule-based coach (no API key) and no Google, so tests never touch the network.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    # https so the Secure session cookie is sent back, like on the real site.
    return TestClient(app, base_url="https://testserver")


def chat(client, message=None, events=None, **extra):
    body = {"timezone": "America/Los_Angeles", "events": events or [], **extra}
    if message is not None:
        body["message"] = message
    return client.post("/api/chat", json=body)


def every_day(event):
    return [{**event, "id": f"{event['id']}_{d}", "weekday": d} for d in range(7)]


# ------------------------------------------------------------------- page ---
def test_home_page_shows_a_muscle_building_plan(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Muscle-building plan" in r.text
    assert "Weekly sets per muscle" in r.text
    assert "planned" in r.text  # coach intro
    assert 'name="week"' not in r.text  # no week toggle any more
    assert "Sign in with Google" in r.text and "Screenshot to AI Coach" in r.text and "Add your own event" in r.text


def test_toolbar_changes_are_saved(client):
    r = client.post("/settings", data={"source": "mine", "commitment": "dedicated"})
    assert r.status_code == 200  # followed the redirect back to the page
    assert "Push/Pull/Legs x2" in r.text  # an empty schedule fits 6 sessions
    assert "Push/Pull/Legs x2" in client.get("/").text  # remembered on the next visit


def test_google_source_without_setup_falls_back(client):
    r = client.post("/settings", data={"source": "google"})
    assert "set up on this server yet" in r.text


# ------------------------------------------------------------ your events ---
def test_your_events_block_time_and_are_editable(client):
    client.post("/settings", data={"source": "mine"})
    events = every_day({**TODAY_EVENT, "start": "06:00", "end": "21:00"})  # busy all day, every day
    data = client.post("/api/plan", json={"events": events, "timezone": "America/Los_Angeles"}).json()
    assert "Rest week" in data["html"]
    assert 'data-event-id="e_test1_0"' in data["html"]  # your events are clickable to edit
    assert len(data["events"]) == 7
    assert "rest week" in data["intro"]  # the coach's opening line matches the re-planned week


def test_bad_events_are_dropped(client):
    junk = [{"title": "x", "start": "10:00", "end": "09:00", "weekday": 1}, {"nope": 1}, "string"]
    data = client.post("/api/plan", json={"events": junk + [{**TODAY_EVENT, "weekday": 2}]}).json()
    assert [e["id"] for e in data["events"]] == ["e_test1"]


def test_export_ics_includes_your_events_in_planning(client):
    client.post("/settings", data={"source": "mine", "commitment": "dedicated"})
    r = client.post("/export.ics", data={"events": "[]"})
    assert r.headers["content-type"].startswith("text/calendar")
    assert len(list(Calendar.from_ical(r.content).walk("VEVENT"))) == 6
    busy_all_week = json.dumps(every_day({**TODAY_EVENT, "start": "06:00", "end": "21:00"}))
    r = client.post("/export.ics", data={"events": busy_all_week})
    assert len(list(Calendar.from_ical(r.content).walk("VEVENT"))) == 0


# --------------------------------------------------------------- AI Coach ---
def test_chat_changes_preferences_and_replans(client):
    client.post("/settings", data={"source": "mine", "commitment": "dedicated"})
    data = chat(client, "no gym on sundays, saturdays off").json()
    assert data["changes"] == ["no Sat", "no Sun"]
    assert "PPL + Upper/Lower" in data["reply"]  # 5 open days left
    assert "Day off" in data["html"]
    assert "PPL + Upper/Lower" in client.get("/").text  # the coach remembers


def test_chat_weekly_commitment_becomes_your_event(client):
    client.post("/settings", data={"source": "mine"})
    data = chat(client, "I have soccer on tuesdays 6-7:30pm").json()
    assert [(e["title"], e["weekday"], e["start"]) for e in data["events"]] == [("Soccer", 1, "18:00")]
    assert "Soccer" in data["html"]


def test_chat_moves_workouts_to_mornings(client):
    client.post("/settings", data={"source": "mine"})
    data = chat(client, "I prefer mornings").json()
    assert data["changes"] == ["mornings"]
    assert "AM" in data["html"] and "PM</span>" not in data["html"]


def test_chat_question_without_changes_explains_the_plan(client):
    data = chat(client, "Why this split?").json()
    assert data["changes"] == []
    assert "sessions per week" in data["reply"]


def test_chat_with_unknown_request_suggests_examples(client):
    assert "I prefer mornings" in chat(client, "hello there").json()["reply"]


def test_chat_reset(client):
    chat(client, "no gym on tuesdays")
    data = chat(client, "reset").json()
    assert data["changes"] == ["reset"]
    assert "Day off" not in data["html"]


def test_empty_message_is_rejected(client):
    assert chat(client, "   ").status_code == 400


def test_replan_button(client):
    data = chat(client, action="replan").json()
    assert data["reply"]


# ------------------------------------------------------------- screenshots ---
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG fake image bytes").decode()


def test_screenshot_without_gemini_explains_how_else_to_add_events(client):
    data = chat(client, image={"data": PNG, "type": "image/png"}).json()
    assert "needs Gemini" in data["reply"]
    assert data["events"] == []


def test_screenshot_events_are_added_as_weekly_events(client, monkeypatch):
    from datetime import time

    def fake_reader(image, mime_type, note="", client=None):
        assert image == b"\x89PNG fake image bytes" and mime_type == "image/png"
        return [
            {"title": "Chem lab", "weekday": 2, "start": time(13, 0), "end": time(16, 0)},
            {"title": "Work shift", "weekday": 5, "start": time(9, 0), "end": time(17, 0)},
        ], "Found your class schedule."

    monkeypatch.setattr(main, "read_schedule_image", fake_reader)
    client.post("/settings", data={"source": "mine"})
    data = chat(client, image={"data": PNG, "type": "image/png"}).json()
    assert data["changes"] == ["+2 events"]
    assert "I added 2 weekly events: Chem lab (Wed 13:00), Work shift (Sat 09:00)" in data["reply"]
    assert {e["title"] for e in data["events"]} == {"Chem lab", "Work shift"}
    assert all(e["repeat"] == "weekly" for e in data["events"])


def test_screenshot_rejects_non_images(client):
    r = chat(client, image={"data": "data:text/plain;base64,aGk=", "type": "text/plain"})
    assert r.status_code == 400 and "PNG, JPG or WebP" in r.json()["error"]


def test_screenshot_too_large(client, monkeypatch):
    monkeypatch.setattr(main, "MAX_IMAGE_BYTES", 5)
    r = chat(client, image={"data": PNG, "type": "image/png"})
    assert r.status_code == 400 and "too large" in r.json()["error"]


def test_tampered_cookie_settings_are_ignored():
    from app.settings import Settings

    s = Settings.from_session({"source": "evil", "session_minutes": 99999, "blocked_weekdays": [9, 2], "week": "x"})
    assert s.source == "demo:busy"
    assert s.session_minutes == 180
    assert s.blocked_weekdays == [2]
