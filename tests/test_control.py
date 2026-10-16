"""Tests for the charger controller."""

from datetime import UTC, datetime, timedelta

from _load import load

control = load("control")
S, A, E = control.ChargerState, control.Action, control.Event
T0 = datetime(2026, 10, 10, 4, 30, tzinfo=UTC)


def minutes(n: float) -> datetime:
    return T0 + timedelta(minutes=n)


def started(state=S.PAUSED):
    c = control.Controller(start_delay=timedelta(0))
    c.observe(state, T0)
    return c


def test_starts_once_and_waits_for_the_charger():
    c = started()
    assert c.decide(True, T0) == A.START
    assert c.decide(True, minutes(1)) == A.NONE
    assert c.observe(S.CHARGING, minutes(1)) is None  # our own start
    assert c.decide(True, minutes(2)) == A.NONE


def test_retries_quickly_then_slowly_but_never_gives_up():
    c = started(S.READY)
    assert [c.decide(True, minutes(m)) for m in (0, 3, 6, 9)] == [A.START, A.START, A.START, A.NONE]
    assert c.gave_up
    assert c.decide(True, minutes(20)) == A.NONE
    assert c.decide(True, minutes(21)) == A.START, "slow retry 15 min after the last attempt"
    assert c.decide(True, minutes(37)) == A.START


def test_stops_when_not_wanted():
    c = started(S.CHARGING)
    assert c.decide(False, T0) == A.STOP
    assert c.decide(False, minutes(0.5)) == A.NONE
    assert c.observe(S.PAUSED, minutes(0.6)) is None


def test_manual_start_is_reported_when_it_lasts():
    c = started()
    c.decide(False, T0)
    assert c.observe(S.CHARGING, minutes(10)) is None, "waits to see that it lasts"
    assert c.decide(False, minutes(10.5)) == A.NONE, "not stopped while it is being confirmed"
    assert c.observe(S.CHARGING, minutes(12)) is None
    assert c.observe(S.CHARGING, minutes(15)) == E.MANUAL_START
    assert c.observe(S.CHARGING, minutes(16)) is None, "reported once"


def test_charging_that_goes_away_again_is_not_a_start():
    """An old "charging" long after our stop (outside the settling time) that is gone a minute later."""
    c = started()
    c.decide(False, T0)
    assert c.observe(S.CHARGING, minutes(30)) is None
    assert c.observe(S.PAUSED, minutes(31)) is None, "gone again: not a start, not an outside stop"
    assert c.observe(S.PAUSED, minutes(40)) is None
    assert not c.blocked


def test_a_car_that_wakes_long_after_our_start_is_still_ours():
    """14 Oct 2026 the car started 10 minutes after the first authorisation; a car that wakes after the period has
    ended must be stopped, not taken for a start from the app (which would charge to the target)."""
    c = started(S.READY)
    assert c.decide(True, T0) == A.START
    assert c.decide(True, minutes(3)) == A.START
    assert c.decide(True, minutes(6)) == A.START
    assert c.decide(False, minutes(15)) == A.NONE, "the period ended before the car woke"
    assert c.observe(S.CHARGING, minutes(40)) is None, "ours, 34 minutes after the last start"
    assert c.decide(False, minutes(40)) == A.STOP


def test_unplugging_drops_a_start_being_confirmed():
    c = started()
    c.decide(False, T0)
    assert c.observe(S.CHARGING, minutes(10)) is None
    assert c.observe(S.DISCONNECTED, minutes(11)) == E.UNPLUGGED
    assert c.observe(S.PAUSED, minutes(12)) == E.PLUGGED
    assert c.observe(S.CHARGING, minutes(13)) is None
    assert c.observe(S.CHARGING, minutes(14)) is None, "confirmation starts again after the new plug-in"


def test_external_stop_is_retried_once_then_respected():
    c = started(S.CHARGING)
    c.decide(True, T0)
    assert c.observe(S.PAUSED, minutes(5)) == E.EXTERNAL_STOP
    assert c.decide(True, minutes(6)) == A.NONE
    assert c.decide(True, minutes(15)) == A.START, "a short fault: started again after 10 minutes"
    assert c.observe(S.CHARGING, minutes(16)) is None
    assert c.observe(S.PAUSED, minutes(30)) == E.EXTERNAL_STOP
    assert c.respects_stop
    assert c.decide(True, minutes(60)) == A.NONE, "stopped from outside twice: respected"
    c.decide(False, minutes(61))
    assert c.decide(True, minutes(62)) == A.START, "a new plan period starts again"


def test_slow_car_start_is_still_ours():
    c = started(S.PAUSED)
    assert c.decide(True, T0) == A.START
    assert c.observe(S.CHARGING, minutes(9)) is None, "drawing power 9 minutes after the start"


def test_unplug_and_plug_are_reported_and_reset():
    c = started(S.CHARGING)
    c.decide(True, T0)
    c.observe(S.PAUSED, minutes(1))
    assert c.observe(S.DISCONNECTED, minutes(2)) == E.UNPLUGGED
    assert c.observe(S.READY, minutes(3)) == E.PLUGGED
    assert not c.blocked
    assert c.decide(True, minutes(3)) == A.START


def test_disconnected_charger_gets_no_commands():
    c = started(S.DISCONNECTED)
    assert c.decide(True, T0) == A.NONE


def test_first_observation_is_not_an_event():
    c = control.Controller()
    assert c.observe(S.CHARGING, T0) is None


def test_start_waits_until_the_plan_has_wanted_it_a_while():
    c = control.Controller()
    c.observe(S.PAUSED, T0)
    assert c.decide(True, T0) == A.NONE
    assert c.decide(False, T0 + timedelta(seconds=2)) == A.NONE  # a setting passed through for a moment
    assert c.decide(True, T0 + timedelta(seconds=3)) == A.NONE
    assert c.decide(True, T0 + timedelta(seconds=19)) == A.START


def test_a_stop_that_did_not_take_is_repeated_soon():
    c = started(S.CHARGING)
    assert c.decide(False, T0) == A.STOP
    assert c.decide(False, minutes(0.5)) == A.NONE
    assert c.decide(False, minutes(1)) == A.STOP


def test_charging_reported_just_after_our_stop_is_not_a_manual_start():
    """16 Oct 2026: the plan stopped the charger at the end of a period (01:00:22 paused), Zaptec's cloud said
    "charging" again two seconds later for a minute, and that was taken for a start from the app or the car: the plan
    went to "Lad nu" and charged to 100 % instead of 68 %."""
    c = started(S.CHARGING)
    assert c.decide(False, T0) == A.STOP
    assert c.observe(S.PAUSED, minutes(0.1)) is None, "our own stop"
    assert c.observe(S.CHARGING, minutes(0.15)) is None, "the charger not caught up yet – not a manual start"
    assert c.decide(False, minutes(0.2)) == A.NONE, "the stop was just sent"
    assert c.decide(False, minutes(1)) == A.STOP, "still charging: the stop is repeated"
    assert c.observe(S.PAUSED, minutes(1.1)) is None
    # A real start from the app well after the stop is still followed (once it has lasted).
    assert c.observe(S.CHARGING, minutes(10)) is None
    assert c.observe(S.CHARGING, minutes(15)) == E.MANUAL_START


def test_stopped_reported_just_after_our_start_is_not_an_outside_stop():
    """The same lag the other way: an old "stopped" right after our start must not count as the car or the app
    stopping it (two of those and the plan would leave the car uncharged)."""
    c = started(S.PAUSED)
    assert c.decide(True, T0) == A.START
    assert c.observe(S.CHARGING, minutes(0.2)) is None, "our own start"
    assert c.observe(S.PAUSED, minutes(0.25)) is None, "old state – not an outside stop"
    assert not c.blocked
    assert c.decide(True, minutes(1)) == A.NONE, "the start was just sent"
    assert c.decide(True, minutes(3.1)) == A.START, "still not charging: started again"


def test_charging_soon_after_our_start_is_ours_also_after_a_stop():
    """A start that the charger's cloud carries out late, after the plan had already stopped again."""
    c = started(S.PAUSED)
    assert c.decide(True, T0) == A.START
    assert c.decide(False, minutes(30)) == A.STOP, "the period ended before the car charged"
    assert c.observe(S.CHARGING, minutes(50)) is None
    assert c.observe(S.CHARGING, minutes(53)) is None, "ours (50 min after our start), not a start from the app"
    assert c.decide(False, minutes(53)) == A.STOP


def test_a_stop_is_sent_when_the_plan_turns_even_if_the_charger_says_stopped():
    """The cloud says "stopped" while the car charges: when the plan turns to a pause after our start the stop is sent
    anyway (a stop does no harm on a stopped charger)."""
    c = started(S.PAUSED)
    assert c.decide(True, T0) == A.START
    assert c.observe(S.CHARGING, minutes(1)) is None
    assert c.observe(S.PAUSED, minutes(20)) == E.EXTERNAL_STOP  # an old "stopped" mid-period
    assert c.decide(False, minutes(30)) == A.STOP, "sent although the charger says stopped"
    assert c.decide(False, minutes(31)) == A.NONE, "once"
