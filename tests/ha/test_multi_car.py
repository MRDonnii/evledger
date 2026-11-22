"""Several cars on one charger: the car that is plugged in and at home takes the charger with its own plan; the
others never send it a command, and only that car's ledger records the charge."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_smart_charge import DOMAIN, MODE, SWITCH, later, setup, state


async def two_cars(hass: HomeAssistant, request, **kw):
    """"Bil" and "Kia" on the same simulated Zaptec, each with its own plug sensor and location."""
    entry, calls = await setup(hass, request, **kw)
    for car in ("bil", "kia"):
        hass.states.async_set(f"binary_sensor.{car}_plugged", "off")
    hass.states.async_set("sensor.kia_battery", "40", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.kia_odometer", "500", {"unit_of_measurement": "km"})
    hass.states.async_set("device_tracker.kia", "home", {"latitude": 56.0, "longitude": 10.0})
    hass.states.async_set("binary_sensor.kia_charging", "off")
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "car_plugged_entity": "binary_sensor.bil_plugged"}})
    await hass.config_entries.async_reload(entry.entry_id)
    kia = MockConfigEntry(domain=DOMAIN, title="Kia", data={
        **entry.data, "vehicle_name": "Kia", "battery_entity": "sensor.kia_battery",
        "odometer_entity": "sensor.kia_odometer", "device_tracker_entity": "device_tracker.kia",
        "charging_binary_entity": "binary_sensor.kia_charging",
        "smart_charge": {**entry.data["smart_charge"], "car_plugged_entity": "binary_sensor.kia_plugged"}})
    kia.add_to_hass(hass)
    assert await hass.config_entries.async_setup(kia.entry_id)
    await hass.async_block_till_done()
    return entry, kia, calls


def planner(hass, entry):
    return hass.data[DOMAIN][entry.entry_id].smart


async def plug_in(hass, freezer, car: str | None):
    hass.states.async_set(MODE, "connected_requesting")
    if car:
        hass.states.async_set(f"binary_sensor.{car}_plugged", "on")
    await later(hass, freezer, 1)


async def test_the_plugged_in_car_takes_the_charger(hass: HomeAssistant, request, freezer):
    bil, kia, calls = await two_cars(hass, request, charger_state="disconnected", cheap_now=True)
    await plug_in(hass, freezer, "kia")
    assert planner(hass, kia).car_present and not planner(hass, bil).car_present
    assert state(hass, "sensor.bil_charge_status") == "other_car"
    assert state(hass, "sensor.kia_charge_status") in ("charging", "starting", "waiting")
    assert planner(hass, kia).vehicle is not None or planner(hass, kia).capacity  # its own model and settings


async def test_the_other_car_never_controls_the_charger(hass: HomeAssistant, request, freezer):
    bil, kia, calls = await two_cars(hass, request, charger_state="disconnected", cheap_now=False)
    await plug_in(hass, freezer, "bil")
    # "Charge now" left on the Kia must not start the charger for the Bil, whose plan waits for a cheaper hour.
    planner(hass, kia).mode = "now"
    planner(hass, kia).async_recalculate()
    await later(hass, freezer, 2)
    assert not calls["switch.turn_on"] and not calls["button.press"], calls
    assert state(hass, "sensor.kia_charge_status") == "other_car"


async def test_the_only_car_at_home_is_the_one(hass: HomeAssistant, request, freezer):
    bil, kia, calls = await two_cars(hass, request, charger_state="disconnected", cheap_now=True)
    hass.states.async_set("device_tracker.kia", "not_home", {"latitude": 55.0, "longitude": 9.0})
    await plug_in(hass, freezer, None)  # the car has not reported the cable yet
    assert planner(hass, bil).car_present and not planner(hass, kia).car_present


async def test_charge_now_chooses_the_car_when_none_reports(hass: HomeAssistant, request, freezer):
    bil, kia, calls = await two_cars(hass, request, charger_state="disconnected", cheap_now=False)
    await plug_in(hass, freezer, None)
    assert not planner(hass, bil).car_present and not planner(hass, kia).car_present
    assert not calls["switch.turn_on"] and not calls["button.press"]
    await hass.services.async_call("select", "select_option", {"entity_id": "select.kia_charge_mode",
                                                               "option": "now"}, blocking=True)
    await later(hass, freezer, 1)
    assert planner(hass, kia).car_present and not planner(hass, bil).car_present
    assert calls["button.press"] or calls["switch.turn_on"], "the Kia is charged"
    hass.states.async_set(MODE, "disconnected")
    hass.states.async_set(SWITCH, "off")
    await later(hass, freezer, 3)
    assert planner(hass, kia).share.forced is None, "chosen until the cable comes out"


async def test_only_the_car_on_the_charger_records_the_charge(hass: HomeAssistant, request, freezer):
    from .test_ledger_sessions import home_charges, step
    bil, kia, calls = await two_cars(hass, request, charger_state="disconnected", cheap_now=True)
    await plug_in(hass, freezer, "kia")
    hass.states.async_set(MODE, "connected_charging")
    hass.states.async_set(SWITCH, "on")
    for entry in (bil, kia):
        await step(hass, entry, 11000, 0.0)
        await step(hass, entry, 11000, 4.0)
    for entry in (bil, kia):
        await step(hass, entry, 0, 4.0)
    assert len(home_charges(hass, kia)) == 1 and not home_charges(hass, bil)


async def test_one_car_works_as_before(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    assert not planner(hass, entry).shared and planner(hass, entry).car_present


def test_all_devices_with_old_and_new_registries():
    """Home Assistant 2026.10 iterates the registry's devices as entries; before, as device ids."""
    from types import SimpleNamespace

    from custom_components.evledger.device_resolve import all_devices

    car, charger = SimpleNamespace(id="car"), SimpleNamespace(id="charger")
    by_id = {"car": car, "charger": charger}
    old = SimpleNamespace(devices=by_id, async_get=by_id.get)
    new = SimpleNamespace(devices=[car, charger], async_get=by_id.get)
    assert all_devices(old) == [car, charger]
    assert all_devices(new) == [car, charger]
