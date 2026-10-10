"""The ledger's own dashboard pieces: a public charge typed in and saved with a button, and the kilometres driven
today."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.evledger.models import Trip

from .test_smart_charge import DOMAIN, setup, state


async def test_a_public_charge_from_the_dashboard(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger=False)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("button", "press", {"entity_id": "button.bil_log_public_charge"}, blocking=True)
    for entity_id, value in (("number.bil_public_charge_energy", 23.5), ("number.bil_public_charge_price", 82.25)):
        await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)
    await hass.services.async_call("text", "set_value", {"entity_id": "text.bil_public_charge_location",
                                                         "value": "Clever Aarhus"}, blocking=True)
    assert hass.states.get("number.bil_public_charge_price").attributes["unit_of_measurement"] == "DKK"
    await hass.services.async_call("button", "press", {"entity_id": "button.bil_log_public_charge"}, blocking=True)
    await hass.async_block_till_done()
    public = [c for c in hass.data[DOMAIN][entry.entry_id].store.charges if c.location_kind == "public"]
    assert [(c.kwh, c.price, c.location_name) for c in public] == [(23.5, 82.25, "Clever Aarhus")]
    assert float(state(hass, "number.bil_public_charge_energy")) == 0, "the form is empty again"
    assert state(hass, "text.bil_public_charge_location") == ""


def trip(ended_hours_ago: float | None, km: float | None, start_km: float | None = None) -> Trip:
    now = dt_util.utcnow()
    ended = None if ended_hours_ago is None else (now - timedelta(hours=ended_hours_ago)).isoformat()
    return Trip(id=f"t{ended_hours_ago}", started_at=(now - timedelta(hours=(ended_hours_ago or 0) + 1)).isoformat(),
                ended_at=ended, start_odometer_km=start_km, end_odometer_km=None, distance_km=km, start_lat=None,
                start_lon=None, end_lat=None, end_lon=None, start_battery_pct=None, end_battery_pct=None)


async def test_distance_today(hass: HomeAssistant, request, freezer):
    freezer.move_to(dt_util.now().replace(hour=14, minute=0))
    entry, _ = await setup(hass, request, charger=False)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    for item in (trip(2, 12.3), trip(30, 50.0), trip(None, None, start_km=990.0)):
        await coordinator.store.async_upsert_trip(item)
    coordinator._last_odometer_km = 1000.0  # under way: 10 km so far
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(state(hass, "sensor.bil_distance_today")) == pytest.approx(22.3), "yesterday's trip is left out"


async def test_home_charging_power_and_energy(hass: HomeAssistant, request):
    from .test_ledger_sessions import step
    entry, _ = await setup(hass, request)
    await step(hass, entry, 0, 0.0)
    hass.states.async_set("sensor.charger_power", "11000", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(state(hass, "sensor.bil_home_charging_power")) == pytest.approx(11.0), "live with the charger, in kW"
    await step(hass, entry, 11000, 0.0)
    await step(hass, entry, 11000, 5.0)
    assert float(state(hass, "sensor.bil_home_charging_energy_today")) == pytest.approx(5.0), "the running charge"
    await step(hass, entry, 0, 5.0)
    for entity_id in ("sensor.bil_home_charging_energy", "sensor.bil_home_charging_energy_today",
                      "sensor.bil_home_charging_energy_this_month"):
        assert float(state(hass, entity_id)) == pytest.approx(5.0), entity_id
    assert hass.states.get("sensor.bil_home_charging_energy_today").attributes["state_class"] == "total"


async def test_the_other_car_shows_no_power(hass: HomeAssistant, request, freezer):
    from .test_multi_car import plug_in, two_cars
    bil, kia, _ = await two_cars(hass, request, charger_state="disconnected", cheap_now=True)
    await plug_in(hass, freezer, "kia")
    hass.states.async_set("sensor.charger_power", "7400", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(state(hass, "sensor.kia_home_charging_power")) == pytest.approx(7.4)
    assert float(state(hass, "sensor.bil_home_charging_power")) == 0


async def test_power_while_the_chargers_switch_is_unavailable(hass: HomeAssistant, request):
    # Zaptec's charging switch is unavailable while no car is connected; its power sensor still says 0 W.
    entry, _ = await setup(hass, request, charger_state="disconnected")
    hass.config_entries.async_update_entry(entry, data={**entry.data,
                                                        "zaptec_charging_entity": "switch.charger_charging"})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("switch.charger_charging", "unavailable")
    hass.states.async_set("sensor.charger_power", "0.0", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    assert float(state(hass, "sensor.bil_home_charging_power")) == 0
