"""Tyre sets in Home Assistant: add sets, change from the select or the service, the sensor, a reminder message."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.evledger.models import Trip

from .test_routines import phones, with_options
from .test_smart_charge import DOMAIN, setup, state

CAR = "sensor.bil_tyres"


async def add(hass, **fields):
    return await hass.services.async_call(DOMAIN, "save_tyre_set", {"entity_id": CAR, **fields}, blocking=True,
                                          return_response=True)


async def test_sets_changes_and_the_sensor(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger=False)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert hass.states.get("select.bil_tyre_set").state == "unavailable", "no sets yet"
    summer = await add(hass, name="Sommer", kind="summer", width_mm=235, aspect_pct=45, rim_inch=18,
                       load_speed="98W", rim_name="Aero 18", purchased="2025-04-01")
    winter = await add(hass, name="Vinter", kind="winter", width_mm=235, aspect_pct=45, rim_inch=18)
    with pytest.raises(ServiceValidationError):
        await add(hass, name="vinter")
    await hass.services.async_call(DOMAIN, "change_tyres", {
        "entity_id": CAR, "set_id": "Sommer", "at": (dt_util.utcnow() - timedelta(days=100)).isoformat(),
        "odometer_km": 500}, blocking=True)
    await hass.async_block_till_done()
    assert state(hass, CAR) == "Sommer"
    attrs = hass.states.get(CAR).attributes
    assert attrs["size"] == "235/45 R18 98W" and attrs["season_km"] == 500, "odometer 500 → 1000 now"
    await hass.services.async_call("select", "select_option", {"entity_id": "select.bil_tyre_set",
                                                               "option": "Vinter"}, blocking=True)
    await hass.async_block_till_done()
    assert state(hass, CAR) == "Vinter" and state(hass, "select.bil_tyre_set") == "Vinter"
    changes = coordinator.store.tyre_changes
    assert changes[0].set_id == winter["set_id"] and changes[0].odometer_km == 1000
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "change_tyres", {"entity_id": CAR, "set_id": "Vinter"}, blocking=True)
    seasons = hass.states.get(CAR).attributes["seasons"]
    assert [s["set"] for s in seasons] == ["Vinter", "Sommer"] and seasons[1]["km"] == 500
    reading = await hass.services.async_call(DOMAIN, "log_tread_depth", {
        "entity_id": CAR, "set_id": summer["set_id"], "each": [6.5, 6.1, 6.3, 6.4]}, blocking=True,
        return_response=True)
    assert reading["mm"] == 6.1
    sets = {s["name"]: s for s in hass.states.get(CAR).attributes["sets"]}
    assert sets["Sommer"]["tread_mm"] == 6.1 and sets["Sommer"]["purchased"] == "2025-04-01"
    await hass.services.async_call(DOMAIN, "delete_tyre_set", {"entity_id": CAR, "set_id": "Vinter"}, blocking=True)
    await hass.async_block_till_done()
    assert state(hass, CAR) == "Sommer"
    store = coordinator.store
    await store.async_load()  # what was saved comes back
    assert [t.name for t in store.tyre_sets] == ["Sommer"] and len(store.tyre_changes) == 1


async def test_reminder_once_a_season(hass: HomeAssistant, request, freezer):
    freezer.move_to(dt_util.now().replace(month=10, day=20, hour=12))
    entry, _ = await setup(hass, request, charger=False)
    sent = phones(hass)
    planner = await with_options(hass, entry)
    planner.flags["tyre_reminder"] = True
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await add(hass, name="Sommer", kind="summer")
    await add(hass, name="Vinter", kind="winter")
    await hass.services.async_call(DOMAIN, "change_tyres", {
        "entity_id": CAR, "set_id": "Sommer", "at": (dt_util.utcnow() - timedelta(days=150)).isoformat()},
        blocking=True)
    now = dt_util.utcnow()
    for day in range(4):
        start = now - timedelta(days=day, hours=2)
        await coordinator.store.async_upsert_trip(Trip(
            id=f"t{day}", started_at=start.isoformat(), ended_at=(start + timedelta(minutes=20)).isoformat(),
            start_odometer_km=900 + day, end_odometer_km=905 + day, distance_km=5, start_lat=None, start_lon=None,
            end_lat=None, end_lon=None, start_battery_pct=60, end_battery_pct=59, start_outside_temp_c=3.0))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(sent) == 1, sent
    assert sent[0]["data"]["notification_icon"] == "mdi:tire" and sent[0]["data"]["tag"].endswith("_tyres")
    assert "Vinter" in sent[0]["message"] and "vinterhjul" in sent[0]["title"]
    assert hass.states.get(CAR).attributes["suggestion"]["to"] == "winter"
