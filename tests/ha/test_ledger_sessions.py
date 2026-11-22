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


async def test_the_running_charge_counts_without_a_price(hass: HomeAssistant, request):
    # No price sensor state: the energy of the charge running still counts (the Energy dashboard gets it as it
    # charges, not all at the end).
    entry, _ = await setup(hass, request, prices=False)
    await step(hass, entry, 0, 0.0)
    await step(hass, entry, 11000, 0.0)
    await step(hass, entry, 11000, 3.0)
    assert float(hass.states.get("sensor.bil_home_charging_energy").state) == pytest.approx(3.0)
    assert hass.states.get("sensor.bil_home_charging_cost_today").attributes["kwh"] == pytest.approx(3.0)
    await step(hass, entry, 0, 3.0)
    assert float(hass.states.get("sensor.bil_home_charging_energy").state) == pytest.approx(3.0)


async def test_the_car_reporting_charging_first_is_no_public_charge(hass: HomeAssistant, request):
    # The car says it charges a poll before the home charger does: that is the home charge starting.
    entry, _ = await setup(hass, request)
    hass.states.async_set("sensor.car_battery", "41")
    hass.states.async_set("binary_sensor.car_charging", "on")
    await step(hass, entry, 0, 0.0)
    assert [c.location_kind for c in hass.data[DOMAIN][entry.entry_id].store.charges] == ["public"]
    hass.states.async_set("sensor.car_battery", "42")
    await step(hass, entry, 11000, 0.0)
    await step(hass, entry, 11000, 4.0)
    hass.states.async_set("binary_sensor.car_charging", "off")
    await step(hass, entry, 0, 4.0)
    charges = hass.data[DOMAIN][entry.entry_id].store.charges
    assert [(c.location_kind, c.kwh, c.start_battery_pct) for c in charges] == [("home", 4.0, 41.0)], charges
    assert not [c for c in charges if c.needs_review], "nothing waits for a price"


async def test_a_public_charge_before_coming_home_is_kept(hass: HomeAssistant, request, freezer):
    # Charged away, and the home charger starts long after: two charges.
    from datetime import timedelta

    entry, _ = await setup(hass, request)
    hass.states.async_set("binary_sensor.car_charging", "on")
    await step(hass, entry, 0, 0.0)
    hass.states.async_set("binary_sensor.car_charging", "off")
    await step(hass, entry, 0, 0.0)
    freezer.tick(timedelta(minutes=30))
    hass.states.async_set("binary_sensor.car_charging", "on")
    await step(hass, entry, 11000, 0.0)
    kinds = sorted(c.location_kind for c in hass.data[DOMAIN][entry.entry_id].store.charges)
    assert kinds == ["home", "public"]


def test_monta_does_not_give_an_earlier_charge_s_cost():
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from custom_components.evledger.providers.charger.monta import MontaChargerProvider

    class States:
        def __init__(self, state):
            self.state = state

        def get(self, _entity_id):
            return self.state

    class Hass:
        pass

    now = dt_util.utcnow()
    hass = Hass()
    hass.states = States(type("S", (), {"attributes": {"cost": 12.0, "consumedKwh": 6.0,
                                                       "stoppedAt": (now - timedelta(minutes=20)).isoformat()}})())
    provider = MontaChargerProvider("sensor.monta_last_charge")
    assert provider.get_recent_session_cost(hass, now, session_start=now - timedelta(minutes=10)) is None, \
        "stopped before this charge began"
    found = provider.get_recent_session_cost(hass, now, session_start=now - timedelta(hours=1))
    assert (found.kwh, found.price) == (6.0, 12.0)


def test_a_record_with_a_field_from_a_newer_version_loads():
    from custom_components.evledger.models import ChargeSession, Trip

    charge = ChargeSession.from_dict({"id": "c", "location_kind": "home", "provider": "zaptec", "started_at": "x",
                                      "ended_at": None, "kwh": 1.0, "price": None, "price_currency": "DKK",
                                      "location_name": None, "start_battery_pct": None, "end_battery_pct": None,
                                      "from_a_newer_version": 1})
    assert charge.kwh == 1.0
    trip = Trip.from_dict({"id": "t", "started_at": "x", "ended_at": None, "start_odometer_km": None,
                           "end_odometer_km": None, "distance_km": 2.0, "start_lat": None, "start_lon": None,
                           "end_lat": None, "end_lon": None, "start_battery_pct": None, "end_battery_pct": None,
                           "from_a_newer_version": 1})
    assert trip.distance_km == 2.0
