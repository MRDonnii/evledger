"""The week plan (a ready-by time per weekday, free days, a day's own target) and the one-time ready-by time."""

from datetime import datetime, time, timedelta

import pytest
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service, mock_restore_cache

from .test_new_features import car_full_at
from .test_smart_charge import DOMAIN, later, setup, state

MODE_ENTITY = "select.bil_charge_mode"


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """A time in the week of Monday 12 October 2026."""
    return datetime(2026, 10, 12 + day, hour, minute, tzinfo=dt_util.get_default_time_zone())


async def week(hass: HomeAssistant, days: dict, enabled: bool | None = True) -> None:
    data = {"entity_id": MODE_ENTITY, "days": days}
    if enabled is not None:
        data["enabled"] = enabled
    await hass.services.async_call(DOMAIN, "set_week_plan", data, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_the_week_plan_decides_the_ready_by_time(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(0, 12))  # Monday noon
    entry, _ = await setup(hass, request)
    planner = entry.runtime_data
    assert planner.deadline_source == "time"
    await week(hass, {"mon": {"time": "06:30"}, "tue": None, "wed": {"time": "05:45", "target": 90},
                      "thu": "06:30", "fri": {"time": "06:30"}})
    assert planner.deadline == at(2, 5, 45), "Tuesday is free: Wednesday morning"
    assert planner.deadline_source == "week" and planner.deadline_target == 90
    assert planner.active_target == 90 and planner.target == 90
    attributes = hass.states.get("switch.bil_week_plan").attributes
    assert state(hass, "switch.bil_week_plan") == "on"
    assert attributes["days"]["tue"] is None and attributes["days"]["wed"] == {"time": "05:45", "target": 90}
    assert attributes["next_source"] == "week" and attributes["next_target"] == 90
    start = hass.states.get("sensor.bil_next_charge_start").attributes
    assert start["deadline_target"] == 90 and start["deadline_source"] == "week"
    # The minimum level is still kept by the week's earliest time every day (Tuesday 05:45).
    assert planner.regular_deadline == at(1, 5, 45)

    # Switched off: the ready-by time again.
    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.bil_week_plan"}, blocking=True)
    assert planner.deadline_source == "time" and planner.deadline_target is None and planner.target == 80


async def test_first_switch_on_starts_from_the_ready_by_times(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(4, 12))  # Friday noon
    entry, _ = await setup(hass, request)
    planner = entry.runtime_data
    planner.async_set_ready_by(time(6, 15))
    planner.times["ready_by_weekend"] = time(9, 30)
    planner.async_set_flag("weekend_ready_by", True)
    hass.states.async_set("sensor.car_battery", "51")  # any update writes the switch again
    await hass.async_block_till_done()
    before = hass.states.get("switch.bil_week_plan").attributes
    assert before["days_set"] is False and before["days"]["mon"]["time"] == "06:15", "the days follow the times so far"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bil_week_plan"}, blocking=True)
    days = hass.states.get("switch.bil_week_plan").attributes["days"]
    assert days["mon"]["time"] == "06:15" and days["sat"]["time"] == "09:30" and days["sun"]["time"] == "09:30"
    assert planner.deadline == at(5, 9, 30)


async def test_the_one_time_ready_by_time_is_used_once(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(0, 12))
    entry, _ = await setup(hass, request)
    planner = entry.runtime_data
    await hass.services.async_call(DOMAIN, "set_ready_by_once", {
        "entity_id": MODE_ENTITY, "at": at(1, 5, 0).isoformat(), "target": 95}, blocking=True)
    assert planner.deadline == at(1, 5) and planner.deadline_source == "once" and planner.target == 95
    assert dt_util.parse_datetime(state(hass, "datetime.bil_ready_by_once")) == at(1, 5)
    assert hass.states.get("datetime.bil_ready_by_once").attributes["target"] == 95

    freezer.move_to(at(1, 5, 1))
    await later(hass, freezer, 1)
    assert planner.once is None, "used once"
    assert state(hass, "datetime.bil_ready_by_once") == "unknown"
    assert planner.deadline_source == "time" and planner.target == 80


async def test_a_later_one_time_time_keeps_the_minimum_until_then(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(0, 12))
    entry, _ = await setup(hass, request)
    planner = entry.runtime_data
    await hass.services.async_call(DOMAIN, "set_ready_by_once", {
        "entity_id": MODE_ENTITY, "at": at(3, 10).isoformat()}, blocking=True)
    assert planner.deadline == at(3, 10) and planner.deadline_target is None
    assert planner.regular_deadline == at(1, 7), "the normal ready-by time keeps the minimum level"
    assert planner.waiting is None
    await hass.services.async_call(DOMAIN, "clear_ready_by_once", {"entity_id": MODE_ENTITY}, blocking=True)
    assert planner.deadline == at(1, 7) and planner.deadline_source == "time"


async def test_bad_input_is_refused(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(0, 12))
    await setup(hass, request)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_ready_by_once", {
            "entity_id": MODE_ENTITY, "at": at(0, 11).isoformat()}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "set_ready_by_once", {
            "entity_id": MODE_ENTITY, "at": (at(0, 12) + timedelta(days=20)).isoformat()}, blocking=True)
    with pytest.raises(ServiceValidationError):
        await week(hass, {"mon": None, "tue": None})
    with pytest.raises(ServiceValidationError):
        await week(hass, {"mon": {"time": "25:00"}})
    with pytest.raises(ServiceValidationError):
        await week(hass, {"mon": {"time": "07:00", "target": 30}})
    with pytest.raises(ServiceValidationError):
        await week(hass, {"monday": {"time": "07:00"}})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "clear_ready_by_once", {"entity_id": "sensor.car_battery"},
                                       blocking=True)


async def test_week_plan_and_one_time_time_survive_a_restart(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(0, 12))
    mock_restore_cache(hass, (
        State("switch.bil_week_plan", "on", {"days": {"mon": None, "tue": {"time": "06:00", "target": 85}}}),
        State("datetime.bil_ready_by_once", at(2, 4, 30).isoformat(), {"target": 90}),
    ))
    entry, _ = await setup(hass, request)
    planner = entry.runtime_data
    assert planner.week_active and planner.week[1] == {"time": time(6), "target": 85}
    assert planner.once == {"at": at(2, 4, 30), "target": 90}
    assert planner.deadline == at(2, 4, 30)
    await hass.services.async_call(DOMAIN, "clear_ready_by_once", {"entity_id": MODE_ENTITY}, blocking=True)
    assert planner.deadline == at(1, 6) and planner.target == 85


async def test_a_day_of_its_own_target_sets_the_car_limit_and_back(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(0, 12))
    car_full_at(hass, 80, at(0, 18))
    entry, _ = await setup(hass, request, charger_state="connected_finished")
    limits = async_mock_service(hass, "number", "set_value")  # after the number platform registered its own
    planner = entry.runtime_data
    await week(hass, {"tue": {"time": "07:00", "target": 90}, "wed": {"time": "07:00"}})
    await hass.async_block_till_done()
    assert [call.data["value"] for call in limits] == [90.0], "the car would stop at 80 %"
    hass.states.async_set("number.car_charge_limit", "90", {"unit_of_measurement": "%"})
    await hass.async_block_till_done()
    assert planner.target == 90
    # Tuesday's time has passed: Wednesday with the normal target, the car gets 80 % back.
    freezer.move_to(at(1, 7, 1))
    await later(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert planner.deadline == at(2, 7) and planner.deadline_target is None
    assert [call.data["value"] for call in limits] == [90.0, 80.0]
    hass.states.async_set("number.car_charge_limit", "80", {"unit_of_measurement": "%"})
    await later(hass, freezer, 1)
    assert len(limits) == 2, "nothing more to set"


async def test_the_car_limit_waits_until_the_car_is_plugged_in(hass: HomeAssistant, request, freezer):
    freezer.move_to(at(0, 12))
    car_full_at(hass, 80, at(0, 18))
    entry, _ = await setup(hass, request, charger_state="disconnected")
    limits = async_mock_service(hass, "number", "set_value")
    await week(hass, {"tue": {"time": "07:00", "target": 90}})
    assert not limits, "no command to a car that is not on the charger"
    hass.states.async_set("sensor.charger_mode", "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [call.data["value"] for call in limits] == [90.0]
    assert entry.runtime_data.deadline_target == 90
