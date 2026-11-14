from datetime import timedelta

from app.models import Commitment
from app.scheduler.planner import build_plan
from app.views import build_days, busy_minutes, busyness, summary_line
from tests.conftest import MONDAY, block_whole_day, busy


def test_busyness_thresholds():
    assert busyness(0) == "light"
    assert busyness(8 * 60) == "medium"  # a 9-to-5 day
    assert busyness(9 * 60 + 30) == "busy"


def test_busy_minutes_merges_overlaps_and_clips_to_waking_hours(prefs):
    blocks = [busy(0, 9, 11), busy(0, 10, 12), busy(0, 0, 7)]  # 9-12 plus 6-7 inside the 6:00-22:00 window
    assert busy_minutes(MONDAY, blocks, prefs) == 4 * 60


def test_days_have_cards_rest_and_tags(prefs):
    prefs.commitment = Commitment.LIGHT
    prefs.blocked_weekdays = {6}
    calendar = [busy(0, 7, 18, "Work"), block_whole_day(2)]
    plan = build_plan(calendar, MONDAY, prefs)
    days = build_days(plan, calendar, prefs, today=MONDAY + timedelta(days=1))

    assert len(days) == 7
    assert days[0]["weekday"] == "MON" and days[0]["date_num"] == 16
    assert days[0]["tag"] == "busy" and days[0]["events"][0] == {"time": "7:00 AM", "title": "Work", "id": None}
    assert days[1]["is_today"]
    assert days[2]["workout"] is None and days[2]["rest_sub"] == "No free window"
    assert days[6]["tag"] == "off" and days[6]["rest_title"] == "Day off"
    cards = [d["workout"] for d in days if d["workout"]]
    assert [c["name"] for c in cards] == ["Full Body", "Full Body"]
    assert cards[0]["focus"] == "Compound · Core" and cards[0]["minutes"] == 60
    assert summary_line(plan) == "2 workouts · 5 rest days"


def test_past_days_are_marked(prefs):
    calendar = [block_whole_day(0)]
    plan = build_plan(calendar, MONDAY, prefs)
    days = build_days(plan, calendar, prefs, today=MONDAY + timedelta(days=3))
    assert days[0]["rest_title"] == "Past"
