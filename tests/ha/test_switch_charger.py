"""Charger control through a plain switch (OCPP, Monta, Easee, … – everything that is not a Zaptec): the plan starts
and stops the charger with the switch, the car's plug sensor tells whether it is plugged in, and the same lag guards
hold."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.evledger.const import DOMAIN

from .test_smart_charge import setup, state

CHARGE = "switch.ocpp_charge"
PLUG = "binary_sensor.car_plugged"


async def at(hass, freezer, moment):
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def switch_setup(hass, request, freezer, cheap_now=True, on=False, plugged=True):
    hass.states.async_set(CHARGE, "on" if on else "off")
    hass.states.async_set(PLUG, "on" if plugged else "off", {"device_class": "plug"})
    entry, calls = await setup(hass, request, charger=False, cheap_now=cheap_now)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        "enabled": True, "charger_type": "switch", "charge_switch": CHARGE, "car_plugged_entity": PLUG}})
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry, calls, hass.data[DOMAIN][entry.entry_id].smart


async def test_a_switch_charger_is_started_in_the_cheap_hour(hass: HomeAssistant, request, freezer):
    _, calls, planner = await switch_setup(hass, request, freezer)
    await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
    assert calls["switch.turn_on"] == [CHARGE]
    hass.states.async_set(CHARGE, "on")
    await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
    assert state(hass, "sensor.bil_charge_status") == "charging"


async def test_a_switch_charger_is_stopped_when_dear(hass: HomeAssistant, request, freezer):
    freezer.move_to(dt_util.now().replace(hour=12, minute=10, second=0, microsecond=0))
    _, calls, planner = await switch_setup(hass, request, freezer, cheap_now=False, on=True)
    await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
    assert calls["switch.turn_off"] and set(calls["switch.turn_off"]) == {CHARGE}, "stopped (again after 45 s while on)"
    assert not calls["switch.turn_on"]
    hass.states.async_set(CHARGE, "off")
    await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
    assert state(hass, "sensor.bil_charge_status") == "waiting"


async def test_an_old_on_after_our_stop_is_not_a_start_from_the_app(hass: HomeAssistant, request, freezer):
    freezer.move_to(dt_util.now().replace(hour=12, minute=10, second=0, microsecond=0))
    _, calls, planner = await switch_setup(hass, request, freezer, cheap_now=False, on=True)
    await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
    hass.states.async_set(CHARGE, "off")
    await at(hass, freezer, dt_util.now() + timedelta(seconds=10))
    hass.states.async_set(CHARGE, "on")  # the charger's cloud a little behind
    for minute in range(1, 8):
        await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
        assert state(hass, "select.bil_charge_mode") == "smart", f"{minute} min"
    assert len(calls["switch.turn_off"]) >= 2, "the stop is sent again"


async def test_the_car_unplugged_is_disconnected(hass: HomeAssistant, request, freezer):
    _, calls, planner = await switch_setup(hass, request, freezer, plugged=False)
    await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
    assert state(hass, "sensor.bil_charge_status") == "disconnected"
    assert not calls["switch.turn_on"], "nothing to start without the car"
    hass.states.async_set(PLUG, "on", {"device_class": "plug"})
    await at(hass, freezer, dt_util.now() + timedelta(minutes=1))
    assert calls["switch.turn_on"] == [CHARGE], "started when the car is plugged in (cheap now)"


async def test_a_switch_that_is_unavailable_is_unknown_and_gets_no_commands(hass: HomeAssistant, request, freezer):
    _, calls, planner = await switch_setup(hass, request, freezer)
    hass.states.async_set(CHARGE, "unavailable")
    await at(hass, freezer, dt_util.now() + timedelta(minutes=2))
    assert state(hass, "sensor.bil_charge_status") == "unknown"
    turned = len(calls["switch.turn_on"])
    await at(hass, freezer, dt_util.now() + timedelta(minutes=5))
    assert len(calls["switch.turn_on"]) == turned, "no commands to a switch that is gone"
