"""The car's integrations as sources: the one that reported last wins (Claude AI, 2026-10-27: Tesla Custom kept saying
"charging" at a Supercharger for an hour after the charge, while Tesla Fleet said "disconnected")."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_smart_charge import DOMAIN, setup


def two_integrations(hass: HomeAssistant) -> None:
    """The car "Bil" in Tesla Custom (the ledger's entities) and in Tesla Fleet (API field unique ids)."""
    registry, devices = er.async_get(hass), dr.async_get(hass)
    custom = MockConfigEntry(domain="tesla_custom")
    custom.add_to_hass(hass)
    fleet = MockConfigEntry(domain="tesla_fleet")
    fleet.add_to_hass(hass)
    car_custom = devices.async_get_or_create(config_entry_id=custom.entry_id, identifiers={("tesla_custom", "vin")},
                                             name="Bil")
    car_fleet = devices.async_get_or_create(config_entry_id=fleet.entry_id, identifiers={("tesla_fleet", "vin")},
                                            name="Bil")
    for domain, unique, object_id in (("sensor", "vin_battery", "car_battery"),
                                      ("binary_sensor", "vin_charging", "car_charging"),
                                      ("device_tracker", "vin_location_tracker", "car")):
        registry.async_get_or_create(domain, "tesla_custom", unique, device_id=car_custom.id, config_entry=custom,
                                     suggested_object_id=object_id)
    for domain, unique, object_id in (("sensor", "vin-charge_state_battery_level", "bil_battery_level"),
                                      ("sensor", "vin-charge_state_charging_state", "bil_charging"),
                                      ("device_tracker", "vin-location", "bil_location"),
                                      ("sensor", "vin-charge_state_charge_energy_added", "bil_charge_energy_added")):
        registry.async_get_or_create(domain, "tesla_fleet", unique, device_id=car_fleet.id, config_entry=fleet,
                                     suggested_object_id=object_id)


async def snapshot(hass: HomeAssistant, entry):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    return coordinator._vehicle_provider.get_snapshot(hass)


async def test_the_integration_that_reported_last_wins(hass: HomeAssistant, request, freezer):
    two_integrations(hass)
    entry, _ = await setup(hass, request)
    hass.states.async_set("binary_sensor.car_charging", "on")
    hass.states.async_set("sensor.car_battery", "99", {"unit_of_measurement": "%"})
    freezer.tick(timedelta(minutes=20))  # Tesla Custom hangs; Tesla Fleet goes on
    hass.states.async_set("sensor.bil_charging", "disconnected")
    hass.states.async_set("sensor.bil_battery_level", "100", {"unit_of_measurement": "%"})
    hass.states.async_set("device_tracker.bil_location", "not_home", {"latitude": 56.2, "longitude": 8.2})
    snap = await snapshot(hass, entry)
    assert (snap.is_charging, snap.battery_pct, snap.latitude) == (False, 100.0, 56.2)


async def test_the_configured_one_wins_when_it_is_newer(hass: HomeAssistant, request, freezer):
    two_integrations(hass)
    entry, _ = await setup(hass, request)
    hass.states.async_set("sensor.bil_charging", "disconnected")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("binary_sensor.car_charging", "on")
    assert (await snapshot(hass, entry)).is_charging is True


async def test_a_source_without_data_is_skipped(hass: HomeAssistant, request, freezer):
    two_integrations(hass)
    entry, _ = await setup(hass, request)
    hass.states.async_set("sensor.car_battery", "55", {"unit_of_measurement": "%"})
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("sensor.bil_battery_level", "unavailable")
    assert (await snapshot(hass, entry)).battery_pct == 55.0


async def test_a_hanging_integration_ends_the_public_charge(hass: HomeAssistant, request, freezer):
    two_integrations(hass)
    entry, _ = await setup(hass, request)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    hass.states.async_set("binary_sensor.car_charging", "on")
    hass.states.async_set("sensor.bil_charging", "charging")
    await coordinator.async_refresh()
    freezer.tick(timedelta(minutes=30))
    hass.states.async_set("sensor.bil_charging", "disconnected")  # only Tesla Fleet sees the end
    hass.states.async_set("sensor.bil_charge_energy_added", "20.0", {"unit_of_measurement": "kWh"})
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    [charge] = [c for c in coordinator.store.charges if c.location_kind == "public"]
    assert charge.ended_at is not None, "the charge ended although Tesla Custom still says charging"
    assert coordinator._energy_added_entity() == "sensor.bil_charge_energy_added"
