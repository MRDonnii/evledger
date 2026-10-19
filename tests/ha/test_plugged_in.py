"""binary_sensor.<car>_plugged_in: one plug state for dashboards and automations, the way the plan sees it – the
charger (and that it is this car on it) where EV Ledger has one, else the car's own plug sensors, any of them on.
19 Oct 2026: Tesla Custom's charger sensor stayed "off" for a day while the car was plugged in ("Ikke sat til")."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_smart_charge import MODE, later, setup, state, two_integrations_car

PLUGGED = "binary_sensor.bil_plugged_in"


async def test_plugged_in_follows_the_charger(hass: HomeAssistant, request, freezer):
    await setup(hass, request, charger_state="disconnected", cheap_now=False)
    assert state(hass, PLUGGED) == "off"
    assert hass.states.get(PLUGGED).attributes["device_class"] == "plug"
    for mode, expected in (("connected_requesting", "on"), ("connected_charging", "on"), ("connected_finished", "on"),
                           ("disconnected", "off")):
        hass.states.async_set(MODE, mode)
        await hass.async_block_till_done(wait_background_tasks=True)
        await later(hass, freezer, 1)
        assert state(hass, PLUGGED) == expected, mode


async def test_one_car_sensor_saying_plugged_in_is_enough(hass: HomeAssistant, request, freezer):
    """Tesla Custom stays "Disconnected", Tesla Fleet says the cable is in: plugged in."""
    await two_integrations_car(hass, request, freezer)
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, PLUGGED) == "off", "the charger has a car, but none of this car's sensors says so yet"
    hass.states.async_set("binary_sensor.car_cable", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, PLUGGED) == "on"
    attrs = hass.states.get(PLUGGED).attributes
    assert attrs["car_says_plugged"] is True and attrs["charger"] == "ready"
    assert set(attrs["car_sensors"]) == {"binary_sensor.car_plug", "binary_sensor.car_cable"}
    hass.states.async_set(MODE, "disconnected")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, PLUGGED) == "off", "the charger says no car, whatever the car's sensors still say"


async def test_without_a_charger_the_car_sensors_decide(hass: HomeAssistant, request, freezer):
    registry, devices = er.async_get(hass), dr.async_get(hass)
    for domain, ident, unique, object_id in (("tesla_custom", "1", "vin_charger", "car_plug"),
                                             ("tesla_fleet", "2", "vin-charge_state_conn_charge_cable", "car_cable")):
        integration = MockConfigEntry(domain=domain)
        integration.add_to_hass(hass)
        device = devices.async_get_or_create(config_entry_id=integration.entry_id, identifiers={(domain, ident)},
                                             name="Bil")
        if domain == "tesla_custom":
            registry.async_get_or_create("sensor", domain, "vin_battery", device_id=device.id, config_entry=integration,
                                         suggested_object_id="car_battery")
        registry.async_get_or_create("binary_sensor", domain, unique, device_id=device.id, config_entry=integration,
                                     suggested_object_id=object_id)
    hass.states.async_set("binary_sensor.car_plug", "unknown")
    hass.states.async_set("binary_sensor.car_cable", "unavailable")
    await setup(hass, request, charger=False, cheap_now=False)
    assert state(hass, PLUGGED) == "unknown", "nothing known"
    hass.states.async_set("binary_sensor.car_plug", "off")
    hass.states.async_set("binary_sensor.car_cable", "on")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, PLUGGED) == "on", "one sensor saying plugged in is enough"
    hass.states.async_set("binary_sensor.car_cable", "off")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, PLUGGED) == "off"
