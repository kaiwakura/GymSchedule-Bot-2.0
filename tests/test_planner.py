from app.models import Commitment, TimeOfDay
from app.scheduler.planner import build_plan, spacing_penalty, violates_recovery
from app.scheduler.splits import FULL_BODY, LEGS, PULL, PUSH, candidate_splits
from tests.conftest import MONDAY, at, block_whole_day, busy


def days(*offsets):
    return tuple(at(o, 0).date() for o in offsets)


# ------------------------------------------------------------------ helpers ---
def test_violates_recovery_uses_real_hours():
    assert violates_recovery((FULL_BODY, FULL_BODY), (at(0, 18), at(1, 18)))  # 24h
    assert violates_recovery((FULL_BODY, FULL_BODY), (at(0, 21), at(2, 6)))  # 33h: two days apart isn't enough
    assert not violates_recovery((FULL_BODY, FULL_BODY), (at(0, 18), at(2, 18)))  # exactly 48h


def test_ppl_on_consecutive_days_is_fine():
    assert not violates_recovery((PUSH, PULL, LEGS), (at(0, 18), at(1, 18), at(2, 18)))


def test_spacing_prefers_even_gaps():
    assert spacing_penalty(days(0, 3)) < spacing_penalty(days(0, 1))
    assert spacing_penalty(days(0, 2, 4)) < spacing_penalty(days(0, 1, 2))


def test_candidate_splits_respect_commitment_order():
    # Muscle building: 3 days means Full Body x3 (each muscle trained ~3x/week) for everyone
    assert candidate_splits(3, Commitment.MODERATE)[0].name == "Full Body x3"
    assert candidate_splits(3, Commitment.DEDICATED)[0].name == "Full Body x3"
    assert candidate_splits(3, Commitment.DEDICATED)[1].name == "Push/Pull/Legs"
    assert candidate_splits(0, Commitment.MODERATE) == []


# ------------------------------------------------------- end-to-end planning ---
def test_busy_person_gets_full_body_twice(prefs):
    prefs.commitment = Commitment.LIGHT
    plan = build_plan([], MONDAY, prefs)
    assert plan.split_name == "Full Body x2"
    gap = (plan.workouts[1].day - plan.workouts[0].day).days
    assert gap >= 3  # spread out, e.g. Mon/Thu


def test_dedicated_person_with_open_week_gets_ppl_twice(prefs):
    prefs.commitment = Commitment.DEDICATED
    plan = build_plan([], MONDAY, prefs)
    assert plan.split_name == "Push/Pull/Legs x2"
    assert [w.name for w in plan.workouts] == ["Push", "Pull", "Legs"] * 2


def test_five_free_days_gives_ppl_upper_lower(prefs):
    prefs.commitment = Commitment.DEDICATED
    plan = build_plan([block_whole_day(5), block_whole_day(6)], MONDAY, prefs)
    assert plan.split_name == "PPL + Upper/Lower"
    assert len(plan.workouts) == 5


def test_moderate_open_week_gets_upper_lower(prefs):
    plan = build_plan([], MONDAY, prefs)
    assert plan.split_name == "Upper/Lower x2"


def test_three_consecutive_free_days_keeps_every_muscle_twice(prefs):
    # Only Mon/Tue/Wed are free. Full Body x3 can't get 48h of rest, and PPL would train
    # each muscle once, so the planner picks Full Body x2 on Monday and Wednesday.
    blocked = [block_whole_day(d) for d in (3, 4, 5, 6)]
    plan = build_plan(blocked, MONDAY, prefs)
    assert plan.split_name == "Full Body x2"
    assert [w.day.weekday() for w in plan.workouts] == [0, 2]
    assert any("didn't fit" in n for n in plan.notes)


def test_two_consecutive_free_days_falls_back_to_upper_lower(prefs):
    prefs.commitment = Commitment.LIGHT
    blocked = [block_whole_day(d) for d in (2, 3, 4, 5, 6)]
    plan = build_plan(blocked, MONDAY, prefs)
    assert plan.split_name == "Upper/Lower x1"


def test_no_free_time_means_rest_week(prefs):
    plan = build_plan([block_whole_day(d) for d in range(7)], MONDAY, prefs)
    assert plan.split_name == "Rest week"
    assert plan.workouts == []


def test_workouts_never_overlap_busy_time_or_buffer(prefs):
    prefs.commitment = Commitment.DEDICATED
    calendar = [busy(d, 8, 17, "Work") for d in range(5)] + [busy(1, 18, 19.5, "Soccer")]
    plan = build_plan(calendar, MONDAY, prefs)
    for w in plan.workouts:
        for b in calendar:
            padded_start = b.start.timestamp() - prefs.buffer_minutes * 60
            padded_end = b.end.timestamp() + prefs.buffer_minutes * 60
            assert w.end.timestamp() <= padded_start or w.start.timestamp() >= padded_end


def test_evening_preference_is_respected(prefs):
    prefs.preferred_time = TimeOfDay.EVENING
    plan = build_plan([], MONDAY, prefs)
    assert all(w.start.hour == 18 for w in plan.workouts)


def test_morning_preference_moves_around_early_class(prefs):
    prefs.preferred_time = TimeOfDay.MORNING
    plan = build_plan([busy(d, 6, 8) for d in range(7)], MONDAY, prefs)
    # 6-8 is busy, +15 min buffer -> earliest start 8:15
    assert all(w.start == at((w.day - MONDAY).days, 8, 15) for w in plan.workouts)


# ------------------------------------------------------- muscle-building rules ---
def every_pair_rest(plan):
    return [
        (b.start - a.start).total_seconds() / 3600
        for i, a in enumerate(plan.workouts)
        for b in plan.workouts[i + 1 :]
        if a.muscle_groups & b.muscle_groups
    ]


def test_rest_is_at_least_48h_and_ideally_72h(prefs):
    for commitment in Commitment:
        prefs.commitment = commitment
        plan = build_plan([], MONDAY, prefs)
        assert min(every_pair_rest(plan)) >= 72  # an open week always gets the ideal rest


def test_planner_moves_a_session_later_to_reach_48h(prefs):
    # Mon is only free late, Wed only early, Fri open. Mon 9pm -> Wed 6am is 33h,
    # so Full Body can't go on Mon+Wed; the planner must find another valid pair.
    prefs.commitment = Commitment.LIGHT
    calendar = [busy(0, 6, 21), busy(2, 8, 22)] + [block_whole_day(d) for d in (1, 3, 5, 6)]
    plan = build_plan(calendar, MONDAY, prefs)
    assert plan.split_name == "Full Body x2"
    assert min(every_pair_rest(plan)) >= 48


def test_every_muscle_twice_beats_more_sessions(prefs):
    prefs.commitment = Commitment.DEDICATED
    plan = build_plan([], MONDAY, prefs)
    from app.scheduler.splits import SPLITS

    assert SPLITS[plan.split_name].hits_every_muscle_twice


def test_one_free_day_explains_it_cannot_hit_twice(prefs):
    plan = build_plan([block_whole_day(d) for d in range(1, 7)], MONDAY, prefs)
    assert plan.split_name == "Full Body x1"
    assert any("enough free time to train every muscle twice" in n for n in plan.notes)


def test_session_length_reshapes_the_workout(prefs):
    prefs.session_minutes = 45
    short = build_plan([], MONDAY, prefs).workouts[0]
    prefs.session_minutes = 90
    long = build_plan([], MONDAY, prefs).workouts[0]
    total = lambda w: sum(e.sets for e in w.exercises)  # noqa: E731
    assert total(short) == 16 and total(long) == 30
    assert (short.end - short.start).total_seconds() == 45 * 60
    assert (long.end - long.start).total_seconds() == 90 * 60
