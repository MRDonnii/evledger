"""After a restart the car integrations can leave the car without data until it wakes: EV Ledger asks for an update."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from .test_smart_charge import later, setup


def ours(calls):
    """The car refresh's calls (the plug-in refresh asks for the battery only)."""
    return [call for call in calls if "lock.car_lock" in call.data["entity_id"]
            or "sensor.car_last_update" in call.data["entity_id"]]


def fleet_twin(hass: HomeAssistant) -> None:
    """The car in Tesla Custom (its battery sensor, which EV Ledger reads) and in Tesla Fleet (same name)."""
    for domain, ident, entities in (
        ("tesla_custom", "vin", (("sensor", "vin_battery", "car_battery", "50", {}),
                                 ("sensor", "vin_last_update", "car_last_update", "1970-01-01T00:00:00+00:00",
                                  {"device_class": "timestamp"}))),
        ("tesla_fleet", "VIN", (("lock", "VIN-vehicle_state_locked", "car_lock", "unknown", {}),
                                ("number", "VIN-charge_state_charge_limit_soc", "car_limit", "unknown", {}))),
    ):
        integration = MockConfigEntry(domain=domain)
        integration.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(config_entry_id=integration.entry_id,
                                                        identifiers={(domain, ident)}, name="Car")
        for entity_domain, unique, object_id, state, attributes in entities:
            er.async_get(hass).async_get_or_create(entity_domain, domain, unique, device_id=device.id,
                                                   config_entry=integration, suggested_object_id=object_id)
            hass.states.async_set(f"{entity_domain}.{object_id}", state, attributes)


async def test_the_car_is_fetched_again_after_a_start(hass: HomeAssistant, request, freezer):
    fleet_twin(hass)
    calls = async_mock_service(hass, "homeassistant", "update_entity")
    entry, _ = await setup(hass, request, charger_state="disconnected")
    await later(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ours(calls)) == 1
    assert set(ours(calls)[0].data["entity_id"]) == {"lock.car_lock", "number.car_limit", "sensor.car_last_update"}
    await later(hass, freezer, 1)
    assert len(ours(calls)) == 1, "not more often than every 3 minutes"
    await later(hass, freezer, 3)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ours(calls)) == 2
    # The car answered: nothing more to fetch.
    hass.states.async_set("lock.car_lock", "locked")
    hass.states.async_set("number.car_limit", "80")
    hass.states.async_set("sensor.car_last_update", "2026-10-11T21:00:00+00:00", {"device_class": "timestamp"})
    await later(hass, freezer, 4)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ours(calls)) == 2


async def test_only_now_and_then_while_plugged_in_later(hass: HomeAssistant, request, freezer):
    fleet_twin(hass)
    calls = async_mock_service(hass, "homeassistant", "update_entity")
    entry, _ = await setup(hass, request, charger_state="disconnected")
    freezer.tick(timedelta(minutes=40))
    await later(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not ours(calls), "half an hour after the start and not plugged in: the car may just be asleep"
    hass.states.async_set("sensor.charger_mode", "connected_requesting")
    await later(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ours(calls)) == 1
    for _ in range(12):
        await later(hass, freezer, 15)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ours(calls)) == 8, "a few tries per plug-in"


async def test_a_hanging_integration_is_asked_while_the_car_is_online(hass: HomeAssistant, request, freezer):
    fleet_twin(hass)
    hass.states.async_set("lock.car_lock", "locked")
    hass.states.async_set("number.car_limit", "80")
    calls = async_mock_service(hass, "homeassistant", "update_entity")
    entry, _ = await setup(hass, request, charger_state="disconnected")
    online = er.async_get(hass).async_get_or_create(
        "binary_sensor", "tesla_custom", "vin_online", suggested_object_id="car_online",
        device_id=er.async_get(hass).async_get("sensor.car_battery").device_id)
    hass.states.async_set(online.entity_id, "on")
    entry.runtime_data.car_refresh._ids = None
    freezer.tick(timedelta(minutes=40))
    from homeassistant.util import dt as dt_util
    hass.states.async_set("sensor.car_last_update", (dt_util.utcnow() - timedelta(hours=2)).isoformat(),
                          {"device_class": "timestamp"})
    await later(hass, freezer, 1)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ours(calls)) == 1, "online but its data is two hours old"
    hass.states.async_set(online.entity_id, "off")  # asleep: old data is fine
    await later(hass, freezer, 20)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(ours(calls)) == 1
