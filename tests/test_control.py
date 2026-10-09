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


def test_manual_start_is_reported():
    c = started()
    c.decide(False, T0)
    assert c.observe(S.CHARGING, minutes(10)) == E.MANUAL_START


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
