"""FastAPI web app: the weekly plan page, the AI Coach chat, and Google sign-in.

Run locally with:  uvicorn app.main:app --reload

Flow: every request loads the visitor's saved Settings (session cookie) and their
own events (sent by the browser), reads busy time from their calendar, runs the
scheduler for the next 7 days, and renders the week. The AI Coach only changes
settings and events; the scheduler then re-plans.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.ai.assistant import (
    ScreenshotUnavailable,
    coach_intro,
    coach_turn,
    explain_plan,
    fallback_reply,
    plan_summary,
    read_schedule_image,
)
from app.models import BusyBlock, Preferences, WeeklyPlan
from app.scheduler.planner import build_plan, local_now
from app.settings import Settings
from app.sources import custom, google_calendar
from app.sources.demo import DEMO_CALENDARS, demo_busy_blocks
from app.sources.ics import plan_to_ics
from app.views import build_days, summary_line, volume_chips

load_dotenv()
log = logging.getLogger("gymschedulebot")
BASE_DIR = Path(__file__).parent
DEFAULT_TZ = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")
MAX_IMAGE_BYTES = 6 * 1024 * 1024
PLAN_DAYS = 7
RESET_WORDS = {"reset", "start over", "reset preferences", "clear preferences"}
WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

app = FastAPI(title="GymScheduleBot 2.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET") or secrets.token_hex(32),
    same_site="lax",
    https_only=os.getenv("OAUTHLIB_INSECURE_TRANSPORT") != "1",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# =============================================================== helpers ===
def ai_enabled() -> bool:
    return bool(os.getenv("GEMINI_API_KEY"))


def load_settings(request: Request) -> Settings:
    settings = Settings.from_session(request.session.get("settings"))
    if "settings" not in request.session:
        settings.timezone = DEFAULT_TZ
    return settings


def save_settings(request: Request, settings: Settings) -> None:
    request.session["settings"] = settings.to_session()


def set_timezone(settings: Settings, timezone: str | None) -> None:
    if not timezone:
        return
    try:
        ZoneInfo(timezone)
        settings.timezone = timezone
    except Exception:
        pass


def events_from_form(raw: str | None) -> list[dict]:
    try:
        return custom.clean_events(json.loads(raw or "[]"))
    except json.JSONDecodeError:
        return []


@dataclass
class PlanState:
    settings: Settings
    prefs: Preferences
    plan: WeeklyPlan
    busy: list[BusyBlock]
    events: list[dict]
    today: date
    notice: str | None = None


def load_busy(request: Request, settings: Settings, start: date) -> tuple[list[BusyBlock], str | None]:
    """Busy blocks from the base calendar (Google or a demo) for the planning window."""
    tz = ZoneInfo(settings.timezone)
    if settings.source == "mine":
        return [], None

    if settings.source == "google":
        creds = request.session.get("google_creds")
        if not creds or not google_calendar.is_configured():
            settings.source = "mine"
            return [], "Connect Google Calendar to plan around it."
        try:
            window_start = datetime.combine(start, datetime.min.time(), tzinfo=tz)
            service = google_calendar.build_service(creds)
            return google_calendar.fetch_busy(
                service, window_start, window_start + timedelta(days=PLAN_DAYS), settings.timezone
            ), None
        except Exception:
            log.exception("Failed to read Google Calendar")
            request.session.pop("google_creds", None)
            settings.source = "mine"
            return [], "Couldn't read your Google Calendar, so I signed you out. Please connect again."

    name = settings.source.split(":", 1)[1] if settings.source.startswith("demo:") else "busy"
    return demo_busy_blocks(name if name in DEMO_CALENDARS else "busy", start, settings.timezone), None


def current_plan(request: Request, settings: Settings, events: list[dict] | None = None) -> PlanState:
    """Plan the next 7 days, starting today."""
    events = events or []
    prefs = settings.to_preferences()
    now = local_now(prefs)
    start = now.date()
    busy, notice = load_busy(request, settings, start)
    busy += custom.busy_blocks(events, start, settings.timezone, PLAN_DAYS)
    plan = build_plan(busy, start, prefs, now=now)
    save_settings(request, settings)
    return PlanState(settings, prefs, plan, busy, events, start, notice)


def plan_context(request: Request, state: PlanState) -> dict:
    end = state.plan.week_start + timedelta(days=PLAN_DAYS - 1)
    return {
        "plan": state.plan,
        "days": build_days(state.plan, state.busy, state.prefs, today=state.today),
        "summary": summary_line(state.plan),
        "volume": volume_chips(state.plan),
        "week_range": f"{state.plan.week_start.strftime('%b %d')} – {end.strftime('%b %d')}".replace(" 0", " "),
        "settings": state.settings,
        "event_count": len(state.events),
        "google_connected": "google_creds" in request.session,
        "google_configured": google_calendar.is_configured(),
        "ai_enabled": ai_enabled(),
    }


def render_plan(request: Request, state: PlanState) -> str:
    return templates.get_template("_plan.html").render(plan_context(request, state))


def plan_response(request: Request, state: PlanState, reply: str | None, changes: list[str]) -> JSONResponse:
    return JSONResponse(
        {"reply": reply, "html": render_plan(request, state), "changes": changes, "events": state.events}
    )


# ================================================================ routes ===
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # The browser re-plans right away with the user's own events (they live in localStorage).
    state = current_plan(request, load_settings(request))
    flash = request.session.pop("flash", None)
    messages = [m for m in (flash, state.notice) if m]
    messages.append(coach_intro(state.plan))
    return templates.TemplateResponse(
        request,
        "index.html",
        {**plan_context(request, state), "coach_messages": messages, "demo_calendars": DEMO_CALENDARS},
    )


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/settings")
def update_settings(
    request: Request,
    source: str | None = Form(None),
    commitment: str | None = Form(None),
    preferred_time: str | None = Form(None),
    session_minutes: int | None = Form(None),
    buffer_minutes: int | None = Form(None),
    timezone: str | None = Form(None),
    reset: str | None = Form(None),
):
    """The toolbar: base calendar, commitment and the 'More' options."""
    settings = load_settings(request)
    if reset:
        settings = Settings(source=settings.source, timezone=settings.timezone)
        request.session["flash"] = "Preferences reset to the defaults."
    for key, value in {
        "source": source,
        "commitment": commitment,
        "preferred_time": preferred_time,
        "session_minutes": session_minutes,
        "buffer_minutes": buffer_minutes,
    }.items():
        if value is not None and value != "":
            setattr(settings, key, value)
    set_timezone(settings, timezone)
    save_settings(request, settings.validated())
    if settings.source == "google" and "google_creds" not in request.session:
        return RedirectResponse("/auth/google/login", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/api/plan")
def api_plan(request: Request, payload: dict = Body(...)):
    """Re-plan with the user's own events (called on page load and after editing an event)."""
    settings = load_settings(request)
    set_timezone(settings, payload.get("timezone"))
    state = current_plan(request, settings, custom.clean_events(payload.get("events")))
    response = plan_response(request, state, None, [])
    body = json.loads(response.body)
    body["intro"] = coach_intro(state.plan)
    return JSONResponse(body)


@app.post("/api/chat")
def chat(request: Request, payload: dict = Body(...)):
    """One AI Coach turn: a message, a schedule screenshot, or the re-plan button."""
    settings = load_settings(request)
    set_timezone(settings, payload.get("timezone"))
    events = custom.clean_events(payload.get("events"))
    message = str(payload.get("message", "")).strip()[:500]
    before = current_plan(request, settings, events)

    if payload.get("action") == "replan":
        return plan_response(request, before, explain_plan(before.plan), [])

    if payload.get("image"):
        return import_screenshot(request, settings, events, payload["image"], message)

    if not message:
        return JSONResponse({"error": "Type a message first."}, status_code=400)

    if message.lower().strip(" .!") in RESET_WORDS:
        settings = Settings(source=settings.source, timezone=settings.timezone)
        after = current_plan(request, settings, events)
        return plan_response(request, after, "Done, I reset your preferences. " + plan_summary(after.plan), ["reset"])

    turn = coach_turn(message, before.plan, before.prefs)
    if turn.changes.is_empty():
        if turn.reply:
            reply = turn.reply
        elif "why" in message.lower():
            reply = " ".join(before.plan.notes)
        else:
            reply = fallback_reply(turn.changes, before.plan)
        return plan_response(request, before, reply, [])

    settings.apply(turn.changes)
    # Weekly commitments from chat ("soccer Tuesdays 6-7:30pm") become the user's own events.
    for r in turn.changes.recurring_busy:
        event = custom.weekly_event(r.title, r.weekday, r.start, r.end)
        if event:
            events.append(event)
    after = current_plan(request, settings, custom.clean_events(events))
    reply = turn.reply or fallback_reply(turn.changes, after.plan)
    return plan_response(request, after, f"{reply} {plan_summary(after.plan)}", turn.changes.describe())


def import_screenshot(request: Request, settings: Settings, events: list[dict], image: Any, note: str):
    """Read a schedule screenshot with Gemini and add what it finds as weekly events."""
    try:
        data_url = str(image.get("data", ""))
        mime_type = str(image.get("type", ""))
        raw = base64.b64decode(data_url.split(",", 1)[-1], validate=True)
    except (AttributeError, binascii.Error, ValueError):
        return JSONResponse({"error": "That image couldn't be read."}, status_code=400)
    if len(raw) > MAX_IMAGE_BYTES:
        return JSONResponse({"error": "That image is too large (6 MB max)."}, status_code=400)

    state = current_plan(request, settings, events)
    try:
        found, ai_reply = read_schedule_image(raw, mime_type, note)
    except ScreenshotUnavailable as exc:
        return plan_response(request, state, f"{exc} You can add events by clicking + on any day.", [])
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception:
        log.exception("Screenshot import failed")
        return plan_response(
            request, state, "Sorry, I couldn't read that screenshot. Try a clearer one, or add events with +.", []
        )

    added = [e for f in found if (e := custom.weekly_event(f["title"], f["weekday"], f["start"], f["end"]))]
    if not added:
        return plan_response(request, state, ai_reply or "I couldn't find any events in that image.", [])
    after = current_plan(request, settings, custom.clean_events(events + added))
    listing = ", ".join(f"{e['title']} ({WEEKDAY_SHORT[e['weekday']]} {e['start']})" for e in added[:6])
    more = f" and {len(added) - 6} more" if len(added) > 6 else ""
    reply = (
        f"{ai_reply + ' ' if ai_reply else ''}I added {len(added)} weekly event{'s' if len(added) != 1 else ''}: "
        f"{listing}{more}. Click any event to fix it. {plan_summary(after.plan)}"
    )
    return plan_response(request, after, reply, [f"+{len(added)} events"])


@app.post("/export.ics")
def export_ics(request: Request, events: str = Form("[]")):
    state = current_plan(request, load_settings(request), events_from_form(events))
    return Response(
        plan_to_ics(state.plan),
        media_type="text/calendar",
        headers={"Content-Disposition": 'attachment; filename="gymschedulebot-workouts.ics"'},
    )


# ------------------------------------------------------- Google Calendar ---
@app.get("/auth/google/login")
def google_login(request: Request):
    if not google_calendar.is_configured():
        request.session["flash"] = "Google Calendar isn't set up on this server yet."
        settings = load_settings(request)
        if settings.source == "google":
            settings.source = "mine"
            save_settings(request, settings)
        return RedirectResponse("/", status_code=303)
    url, state, verifier = google_calendar.start_login()
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = verifier
    return RedirectResponse(url)


@app.get("/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    expected = request.session.pop("oauth_state", None)
    verifier = request.session.pop("oauth_verifier", None)
    settings = load_settings(request)
    if error or not code or state != expected:
        request.session["flash"] = "Google sign-in was cancelled or failed. Please try again."
        if settings.source == "google":
            settings.source = "mine"
    else:
        try:
            request.session["google_creds"] = google_calendar.finish_login(code, state, verifier)
            settings.source = "google"
            request.session["flash"] = "Google Calendar connected. I've planned around your real week."
        except Exception:
            log.exception("OAuth token exchange failed")
            request.session["flash"] = "Couldn't finish connecting to Google. Please try again."
            settings.source = "mine"
    save_settings(request, settings)
    return RedirectResponse("/", status_code=303)


@app.post("/auth/google/logout")
def google_logout(request: Request):
    request.session.pop("google_creds", None)
    settings = load_settings(request)
    if settings.source == "google":
        settings.source = "mine"
        save_settings(request, settings)
    request.session["flash"] = "Disconnected from Google Calendar."
    return RedirectResponse("/", status_code=303)


@app.post("/google/add")
def google_add(request: Request, events: str = Form("[]")):
    creds = request.session.get("google_creds")
    if not creds:
        request.session["flash"] = "Connect your Google Calendar first."
        return RedirectResponse("/", status_code=303)
    state = current_plan(request, load_settings(request), events_from_form(events))
    try:
        service = google_calendar.build_service(creds)
        count = google_calendar.add_workouts(service, state.plan, state.settings.timezone)
        request.session["flash"] = f"Added {count} workout(s) to your Google Calendar."
    except Exception:
        log.exception("Adding events failed")
        request.session["flash"] = "Couldn't add the workouts to Google Calendar."
    return RedirectResponse("/", status_code=303)
