"""The ledger's home charges with Zaptec: its session counter runs from plug-in to unplug, through pauses."""

import pytest
from homeassistant.core import HomeAssistant

from .test_smart_charge import DOMAIN, setup


async def step(hass: HomeAssistant, entry, power_w: float, session_kwh: float) -> None:
    hass.states.async_set("sensor.charger_power", str(power_w), {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.charger_session", str(session_kwh), {"unit_of_measurement": "kWh"})
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()


def home_charges(hass: HomeAssistant, entry) -> list:
    return [c for c in hass.data[DOMAIN][entry.entry_id].store.charges if c.location_kind == "home"]


async def test_a_paused_charge_is_not_counted_twice(hass: HomeAssistant, request):
    # The night of 9-10 October 2026: 0.537 kWh from a test earlier in the plug-in, a period to 8.502, a pause
    # and a period to 15.6 – the counter is never reset in between.
    entry, _ = await setup(hass, request)
    await step(hass, entry, 0, 0.537)
    await step(hass, entry, 11000, 0.537)
    await step(hass, entry, 11000, 8.502)
    await step(hass, entry, 0, 8.502)
    await step(hass, entry, 11000, 8.502)
    await step(hass, entry, 11000, 12.0)
    assert await hass.config_entries.async_reload(entry.entry_id), "a restart in the middle of a period"
    await hass.async_block_till_done()
    await step(hass, entry, 11000, 15.6)
    await step(hass, entry, 0, 15.6)
    charges = home_charges(hass, entry)
    assert [c.kwh for c in charges] == [pytest.approx(7.965, abs=0.01), pytest.approx(7.1, abs=0.01)], charges


async def test_a_new_plug_in_counts_from_zero(hass: HomeAssistant, request):
    # The counter still shows the last plug-in's total when charging starts, then drops to zero.
    entry, _ = await setup(hass, request)
    await step(hass, entry, 0, 4.0)
    await step(hass, entry, 11000, 4.0)
    await step(hass, entry, 11000, 0.2)
    await step(hass, entry, 11000, 9.0)
    await step(hass, entry, 0, 9.0)
    assert [c.kwh for c in home_charges(hass, entry)] == [pytest.approx(9.0, abs=0.01)]


async def test_home_charging_cost_today(hass: HomeAssistant, request):
    from .test_new_features import quarter_prices
    entry, _ = await setup(hass, request)
    quarter_prices(hass, {}, default=2.0)
    await step(hass, entry, 0, 0.0)
    await step(hass, entry, 11000, 0.0)
    await step(hass, entry, 11000, 3.0)
    running = float(hass.states.get("sensor.bil_home_charging_cost_today").state)
    assert running == pytest.approx(6.0), "the running charge"
    await step(hass, entry, 0, 3.0)
    state = hass.states.get("sensor.bil_home_charging_cost_today")
    assert float(state.state) == pytest.approx(6.0) and state.attributes["kwh"] == pytest.approx(3.0)
