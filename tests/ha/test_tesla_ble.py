"""Tesla BLE as one more of the car's sources (Claude AI, 2026-10-30): the car over local Bluetooth through an ESPHome
proxy at home. Its device is called "Tesla <the VIN's last 6>", so it is found by the VIN; at home it reports most
often and wins, out of range its readings are unavailable and the cloud integrations take over. Its tracker has no
position, and its "charge energy added" is a running total over all charges, so neither of those is used."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evledger.const import (
    CONF_BATTERY_ENTITY,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_OUTSIDE_TEMP_ENTITY,
)
from custom_components.evledger.device_resolve import resolve_tesla_vehicle_entities, same_car

from .test_smart_charge import DOMAIN, MODE, later, setup, state

VIN = "TESTVIN0000000001"  # an example, not a real car
OTHER_VIN = "TESTVIN0000000002"


def register(hass: HomeAssistant, platform: str, identifier: str, name: str, entities) -> dr.DeviceEntry:
    entry = MockConfigEntry(domain=platform)
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id,
                                                    identifiers={(platform, identifier)}, name=name)
    for domain, unique, object_id, *device_class in entities:
        er.async_get(hass).async_get_or_create(domain, platform, unique, device_id=device.id, config_entry=entry,
                                               suggested_object_id=object_id,
                                               original_device_class=device_class[0] if device_class else None)
    return device


def ble(hass: HomeAssistant, vin: str = VIN, prefix: str = "ble") -> dr.DeviceEntry:
    """The car in Tesla BLE: unique ids "<vin>_<platform>_<key>", its own name."""
    return register(hass, "tesla_ble", vin, f"Tesla {vin[-6:]}", (
        ("sensor", f"{vin}_sensor_battery_level", f"{prefix}_battery_level"),
        ("binary_sensor", f"{vin}_binary_charging", f"{prefix}_charging"),
        ("binary_sensor", f"{vin}_binary_charger_connected", f"{prefix}_charger_connected"),
        ("lock", f"{vin}_lock_doors", f"{prefix}_doors"),
        ("sensor", f"{vin}_sensor_outside_temp", f"{prefix}_outside_temp"),
        ("sensor", f"{vin}_sensor_odometer", f"{prefix}_odometer"),
        ("sensor", f"{vin}_sensor_charge_energy_added", f"{prefix}_charge_energy_added"),
        ("device_tracker", f"{vin}_tracker", f"{prefix}_vehicle"),
        ("number", f"{vin}_number_charge_limit", f"{prefix}_charge_limit"),
    ))


def cloud(hass: HomeAssistant) -> None:
    """The car "Bil" in Tesla Custom (the ledger's entities) and Tesla Fleet, both with the VIN as identifier."""
    register(hass, "tesla_custom", VIN, "Bil", (
        ("sensor", "vin_battery", "car_battery"), ("binary_sensor", "vin_charging", "car_charging"),
        ("device_tracker", "vin_location_tracker", "car"), ("binary_sensor", "vin_charger", "car_plug", "plug"),
        ("number", "vin_charge_limit", "car_charge_limit")))
    register(hass, "tesla_fleet", VIN, "Bil", (
        ("sensor", "vin-charge_state_battery_level", "bil_battery_level"),
        ("sensor", "vin-charge_state_charging_state", "bil_charging"),
        ("device_tracker", "vin-location", "bil_location"),
        ("sensor", "vin-charge_state_charge_energy_added", "bil_charge_energy_added"),
        ("number", "vin-charge_state_charge_limit_soc", "bil_charge_limit")))


async def snapshot(hass: HomeAssistant, entry):
    return hass.data[DOMAIN][entry.entry_id]._vehicle_provider.get_snapshot(hass)


def test_the_ble_device_is_found_by_the_vin(hass: HomeAssistant):
    cloud(hass)
    car_ble = ble(hass)
    other = ble(hass, OTHER_VIN, "other")  # the brother's car next to it: never the same car
    devices = dr.async_get(hass)
    car = devices.async_get(er.async_get(hass).async_get("sensor.car_battery").device_id)
    ids = same_car(devices, car)
    assert car_ble.id in ids and other.id not in ids
    assert len(ids) == 3


def test_ble_roles(hass: HomeAssistant):
    """Its own entities by unique id; its tracker is presence only, so no position source."""
    device = ble(hass)
    assert resolve_tesla_vehicle_entities(hass, device.id) == {
        CONF_BATTERY_ENTITY: "sensor.ble_battery_level", "odometer_entity": "sensor.ble_odometer",
        CONF_DEVICE_TRACKER_ENTITY: None, CONF_CHARGING_BINARY_ENTITY: "binary_sensor.ble_charging",
        CONF_LOCKED_ENTITY: "lock.ble_doors", CONF_OUTSIDE_TEMP_ENTITY: "sensor.ble_outside_temp"}


async def test_ble_wins_at_home_and_the_cloud_takes_over_out_of_range(hass: HomeAssistant, request, freezer):
    cloud(hass)
    ble(hass)
    entry, _ = await setup(hass, request)
    hass.states.async_set("sensor.bil_battery_level", "61", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.bil_charging", "disconnected")
    hass.states.async_set("device_tracker.bil_location", "home", {"latitude": 56.1, "longitude": 10.1})
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("sensor.ble_battery_level", "63", {"unit_of_measurement": "%"})
    hass.states.async_set("binary_sensor.ble_charging", "on")
    hass.states.async_set("lock.ble_doors", "unlocked")
    hass.states.async_set("device_tracker.ble_vehicle", "home")  # within range, no position
    snap = await snapshot(hass, entry)
    assert (snap.battery_pct, snap.is_charging, snap.is_locked) == (63.0, True, False)
    assert snap.latitude == 56.0, "the position still comes from a tracker with a position (not Tesla BLE's)"
    # The car drives off: Tesla BLE's readings go unavailable, Tesla Fleet goes on
    freezer.tick(timedelta(minutes=5))
    for entity_id in ("sensor.ble_battery_level", "binary_sensor.ble_charging", "lock.ble_doors"):
        hass.states.async_set(entity_id, "unavailable")
    hass.states.async_set("sensor.bil_battery_level", "62", {"unit_of_measurement": "%"})
    snap = await snapshot(hass, entry)
    assert (snap.battery_pct, snap.is_charging) == (62.0, False)


async def test_another_cars_ble_is_not_used(hass: HomeAssistant, request, freezer):
    cloud(hass)
    ble(hass, OTHER_VIN, "other")
    entry, _ = await setup(hass, request, soc="55")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("sensor.other_battery_level", "99", {"unit_of_measurement": "%"})
    assert (await snapshot(hass, entry)).battery_pct == 55.0


async def test_ble_plug_sensor_counts_as_the_cars(hass: HomeAssistant, request, freezer):
    """Tesla Custom still says "Disconnected", Tesla BLE says the charger is connected: plugged in."""
    cloud(hass)
    ble(hass)
    hass.states.async_set("binary_sensor.car_plug", "off")
    hass.states.async_set("binary_sensor.ble_charger_connected", "off")
    await setup(hass, request, charger_state="connected_requesting", cheap_now=False)
    await later(hass, freezer, 1)
    assert state(hass, "binary_sensor.bil_plugged_in") == "off"
    hass.states.async_set("binary_sensor.ble_charger_connected", "on")
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, "binary_sensor.bil_plugged_in") == "on"
    assert "binary_sensor.ble_charger_connected" in hass.states.get("binary_sensor.bil_plugged_in").attributes[
        "car_sensors"]


async def test_the_limit_goes_over_bluetooth_first_then_the_cloud(hass: HomeAssistant, request):
    cloud(hass)
    ble(hass)
    for entity_id in ("number.car_charge_limit", "number.bil_charge_limit", "number.ble_charge_limit"):
        hass.states.async_set(entity_id, "70", {"min": 50, "max": 100})
    calls, failing = [], set()

    async def fake_set(call):
        calls.append(call.data["entity_id"])
        if call.data["entity_id"] in failing:
            raise HomeAssistantError("not reachable over Bluetooth")

    await setup(hass, request, cheap_now=False)
    hass.services.async_register("number", "set_value", fake_set)
    target = hass.data["entity_components"]["number"].get_entity("number.bil_target_soc")
    await target.async_set_native_value(90)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == ["number.ble_charge_limit"], "at home the car gets the limit over Bluetooth only"
    # The Bluetooth command fails: Tesla Fleet gets it, Tesla Custom is not needed
    calls.clear()
    failing.add("number.ble_charge_limit")
    await target.async_set_native_value(85)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == ["number.ble_charge_limit", "number.bil_charge_limit"]
    # Out of range: Tesla BLE's limit is unavailable and not tried
    calls.clear()
    hass.states.async_set("number.ble_charge_limit", "unavailable")
    await target.async_set_native_value(80)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == ["number.bil_charge_limit"]


async def test_the_ble_running_total_is_not_a_charges_energy(hass: HomeAssistant, request, freezer):
    cloud(hass)
    ble(hass)
    entry, _ = await setup(hass, request)
    hass.states.async_set("sensor.bil_charge_energy_added", "12.0", {"unit_of_measurement": "kWh"})
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("sensor.ble_charge_energy_added", "1834.2", {"unit_of_measurement": "kWh"})
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator._energy_added_entity() == "sensor.bil_charge_energy_added"
