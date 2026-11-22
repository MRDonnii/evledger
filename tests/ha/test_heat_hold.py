"""Keeping the battery at the target while the car heats on the charger, up to a price limit."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_smart_charge import later, setup


def car_with_climate(hass: HomeAssistant) -> None:
    tesla = MockConfigEntry(domain="tesla_fleet")
    tesla.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=tesla.entry_id, identifiers={("tesla_fleet", "v")})
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "tesla_fleet", "v-battery", device_id=device.id, config_entry=tesla,
                                 suggested_object_id="car_battery")
    registry.async_get_or_create("climate", "tesla_fleet", "v-climate", device_id=device.id, config_entry=tesla,
                                 suggested_object_id="car_climate")
    hass.states.async_set("climate.car_climate", "off")


async def test_heating_keeps_the_battery_at_the_target(hass: HomeAssistant, request, freezer):
    car_with_climate(hass)
    entry, calls = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="78")
    planner = entry.runtime_data
    planner.async_set_setting("heat_hold_price", 3.5)
    hass.states.async_set("climate.car_climate", "heat_cool")
    await later(hass, freezer, 1)
    assert not planner.heat_holding and planner.heat_hold_reason == "off", "only when switched on"

    switch = "switch.bil_keep_the_battery_at_the_target_while_heating"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await later(hass, freezer, 1)
    assert planner.heat_holding and planner.charge_desired
    attributes = hass.states.get(switch).attributes
    assert attributes["active"] is True and attributes["price_limit"] == 3.5
    assert attributes["climate_entity"] == "climate.car_climate"

    hass.states.async_set("sensor.car_battery", "79.6")  # at the target: stop
    await later(hass, freezer, 1)
    assert not planner.heat_holding and planner.heat_hold_reason == "at_target"
    hass.states.async_set("sensor.car_battery", "79.3")  # less than 1 % below: wait
    await later(hass, freezer, 1)
    assert not planner.heat_holding
    hass.states.async_set("sensor.car_battery", "78.9")  # 1 % below: again
    await later(hass, freezer, 1)
    assert planner.heat_holding

    planner.async_set_setting("heat_hold_price", 2.0)  # the price (3.0) is above the limit
    await later(hass, freezer, 1)
    assert not planner.heat_holding and planner.heat_hold_reason == "too_expensive"
    planner.async_set_setting("heat_hold_price", 3.5)
    hass.states.async_set("climate.car_climate", "off")
    await later(hass, freezer, 1)
    assert not planner.heat_holding and planner.heat_hold_reason == "climate_off"
    hass.states.async_set("climate.car_climate", "heat_cool")
    hass.states.async_set("sensor.charger_mode", "disconnected")
    await later(hass, freezer, 1)
    assert not planner.heat_holding and planner.heat_hold_reason == "not_plugged_in"


async def test_pause_wins(hass: HomeAssistant, request, freezer):
    car_with_climate(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="70")
    planner = entry.runtime_data
    planner.async_set_flag("heat_hold", True)
    hass.states.async_set("climate.car_climate", "heat_cool")
    await hass.services.async_call("select", "select_option", {"entity_id": "select.bil_charge_mode", "option": "off"},
                                   blocking=True)
    await later(hass, freezer, 1)
    assert not planner.heat_holding and planner.heat_hold_reason == "paused" and not planner.charge_desired


async def test_climate_still_on_from_the_drive_waits(hass: HomeAssistant, request, freezer):
    """Plugged in with the climate still on from the drive: no charging for it until it has stayed on a while
    (the driver leaving turns it off); a climate switched on while plugged in counts at once."""
    car_with_climate(hass)
    entry, _ = await setup(hass, request, charger_state="disconnected", cheap_now=False, soc="70")
    planner = entry.runtime_data
    planner.async_set_flag("heat_hold", True)
    planner.async_set_setting("heat_hold_price", 3.5)
    hass.states.async_set("climate.car_climate", "heat_cool")  # on during the drive home
    await later(hass, freezer, 1)
    hass.states.async_set("sensor.charger_mode", "connected_finished")  # plugged in
    await later(hass, freezer, 1)
    assert not planner.heat_holding and planner.heat_hold_reason == "just_arrived" and not planner.charge_desired
    await later(hass, freezer, 5)  # left on (on purpose): now it is heating on the charger
    assert planner.heat_holding and planner.charge_desired

    hass.states.async_set("climate.car_climate", "off")
    await later(hass, freezer, 1)
    assert not planner.heat_holding
    hass.states.async_set("climate.car_climate", "heat_cool")  # switched on while plugged in
    await later(hass, freezer, 1)
    assert planner.heat_holding
