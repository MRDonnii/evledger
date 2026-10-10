"""Quarters or whole hours, the car's own "full at" time, the saving, the ledger's price per interval, learning
the charging power and efficiency, and the monthly summary."""

from datetime import datetime, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_ledger_sessions import home_charges, step
from .test_routines import phones, session, with_options
from .test_smart_charge import DOMAIN, MODE, later, setup, state


def quarter_prices(hass: HomeAssistant, prices: dict[int, float], default: float = 3.0) -> datetime:
    """Quarter prices from the current hour on; prices maps the quarter's index to its price."""
    hour = dt_util.now().replace(minute=0, second=0, microsecond=0)
    items = [{"start": (hour + timedelta(minutes=15 * q)).isoformat(),
              "end": (hour + timedelta(minutes=15 * (q + 1))).isoformat(), "price": prices.get(q, default)}
             for q in range(-4, 30 * 4)]
    hass.states.async_set("sensor.price", str(prices.get(0, default)),
                          {"prices": items, "unit_of_measurement": "kr/kWh"})
    return hour


async def test_whole_hours_change_the_plan(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, soc="70", cheap_now=False)
    planner = entry.runtime_data
    # 22:00 + 2 h: two cheap quarters, then dear (mean 1.1); the hour after: 0.9 all through.
    hour = quarter_prices(hass, {8: 0.2, 9: 0.2, 10: 2.0, 11: 2.0, 12: 0.9, 13: 0.9, 14: 0.9, 15: 0.9})
    planner.async_set_setting("charge_power_kw", 12.0)
    planner.async_set_setting("efficiency", 1.0)
    planner.async_set_ready_by((hour + timedelta(hours=10)).time())
    await hass.async_block_till_done()
    # 10 % of 60 kWh = 6 kWh = half an hour.
    blocks = [(b.start, b.end) for b in planner.schedule.blocks]
    assert blocks == [(hour + timedelta(hours=2), hour + timedelta(hours=2, minutes=30))]

    await hass.services.async_call("select", "select_option", {"entity_id": "select.bil_price_resolution",
                                                               "option": "hour"}, blocking=True)
    blocks = [(b.start, b.end) for b in planner.schedule.blocks]
    assert blocks == [(hour + timedelta(hours=3), hour + timedelta(hours=3, minutes=30))]
    assert hass.states.get("select.bil_charge_mode").attributes["price_resolution"] == "hour"


def car_full_at(hass: HomeAssistant, limit: float, full: datetime) -> None:
    """Tesla Custom's battery, charge limit and "time charge complete" on the car's device."""
    tesla = MockConfigEntry(domain="tesla_custom")
    tesla.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=tesla.entry_id,
                                                    identifiers={("tesla_custom", "vin")})
    registry = er.async_get(hass)
    for domain, unique, object_id in (("sensor", "vin_battery", "car_battery"),
                                      ("number", "vin_charge_limit", "car_charge_limit"),
                                      ("sensor", "vin_time_charge_complete", "car_time_charge_complete")):
        registry.async_get_or_create(domain, "tesla_custom", unique, device_id=device.id, config_entry=tesla,
                                     suggested_object_id=object_id)
    hass.states.async_set("number.car_charge_limit", str(limit), {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.car_time_charge_complete", full.isoformat(), {"device_class": "timestamp"})


async def test_the_end_follows_the_car_when_it_charges_to_its_limit(hass: HomeAssistant, request):
    full = (dt_util.now() + timedelta(minutes=50)).replace(microsecond=0)
    car_full_at(hass, 100, full)
    entry, _ = await setup(hass, request, charger_state="connected_charging", soc="97")
    planner = entry.runtime_data
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "now"}, blocking=True)
    planner.async_set_setting("target_soc", 100.0)
    await hass.async_block_till_done()
    assert dt_util.parse_datetime(state(hass, "sensor.bil_next_charge_end")) == full, "the car knows it slows down"

    planner.async_set_setting("target_soc", 98.0)  # below the car's limit: the plan stops it, its own time counts
    await hass.async_block_till_done()
    assert dt_util.parse_datetime(state(hass, "sensor.bil_next_charge_end")) != full


async def test_the_done_message_tells_the_saving(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_charging", cheap_now=False, soc="70")
    planner = await with_options(hass, entry)
    assert planner.routines.run.now_price == pytest.approx(3.0), "charging right away costs 3 kr/kWh"
    planner.routines.charge_finished(session(6.0, 3.0, dt_util.now() - timedelta(hours=2), 30, (70, 80)))
    hass.states.async_set("sensor.car_battery", "80")
    hass.states.async_set(MODE, "connected_finished")
    await later(hass, freezer, 1)
    done = [m for m in sent if m["title"] == "Bil: opladning færdig"]
    assert len(done) == 1 and "Sparet 15,00 kr i forhold til Lad nu" in done[0]["message"], done


async def test_the_ledger_prices_energy_when_it_was_counted(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    quarter_prices(hass, {}, default=1.0)
    await step(hass, entry, 0, 0.0)
    await step(hass, entry, 11000, 0.0)
    await step(hass, entry, 11000, 5.0)
    quarter_prices(hass, {}, default=3.0)  # the price goes up while it charges
    await step(hass, entry, 11000, 8.0)
    await step(hass, entry, 0, 8.0)
    charge = home_charges(hass, entry)[-1]
    assert charge.kwh == pytest.approx(8.0) and charge.price == pytest.approx(5 * 1.0 + 3 * 3.0)


async def test_learning_the_power_and_efficiency(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    planner = entry.runtime_data
    start = dt_util.now() - timedelta(hours=5)
    # 1 hour, 10 kWh from the charger, 50 -> 65 % of 60 kWh: 10 kW and 0.9.
    planner.routines.charge_finished(session(10.0, 3.0, start, 60, (50, 65)))
    assert planner.settings["charge_power_kw"] != 10.0, "one charge is not enough"
    planner.routines.charge_finished(session(10.0, 3.0, start + timedelta(hours=2), 60, (65, 80)))
    assert planner.settings["charge_power_kw"] == 10.0
    assert planner.settings["efficiency"] == 0.9
    # A charge into the top, where the car slows down, does not lower the power.
    planner.routines.charge_finished(session(4.0, 1.0, start + timedelta(hours=4), 60, (90, 96)))
    assert planner.settings["charge_power_kw"] == 10.0
    learned = hass.states.get("switch.bil_learn_charging_power_and_efficiency").attributes["learned"]
    assert learned["power_samples"] == 2 and learned["efficiency_samples"] == 2


async def test_the_monthly_summary_on_the_first(hass: HomeAssistant, request, freezer):
    freezer.move_to(dt_util.as_local(datetime(2026, 11, 1, 8, 59, 30, tzinfo=dt_util.get_default_time_zone())))
    sent = phones(hass)
    entry, _ = await setup(hass, request)
    planner = await with_options(hass, entry)
    store = hass.data[DOMAIN][entry.entry_id].store
    october = dt_util.as_local(datetime(2026, 10, 10, 1, 0, tzinfo=dt_util.get_default_time_zone()))
    await store.async_upsert_charge(session(10.0, 4.0, october, 60, (50, 65)))
    await store.async_upsert_charge(session(5.0, 1.0, october + timedelta(days=1), 30, (60, 68)))
    planner.routines.saved["2026-10"] = 12.5
    await later(hass, freezer, 1)
    await later(hass, freezer, 1)
    month = [m for m in sent if "månedsoversigt" in m["title"]]
    assert len(month) == 1, sent
    assert month[0]["title"] == "Bil: månedsoversigt oktober 2026"
    assert month[0]["message"].startswith("Hjemme: 2 ladninger · 15,0 kWh · 5,00 kr (0,33 kr/kWh)")
    assert "Sparet med ladeplanerne: 12,50 kr" in month[0]["message"]
    assert hass.states.get("switch.bil_monthly_summary_on_the_phone").attributes["sent_for"] == "2026-10"


async def test_the_plan_has_its_prices_for_a_chart(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger=False, cheap_now=False)
    attributes = hass.states.get("sensor.bil_next_charge_start").attributes
    prices = attributes["prices"]
    assert attributes["slot_minutes"] == 15 and len(prices) > 4
    assert dt_util.parse_datetime(prices[0]["t"]) <= dt_util.now() < dt_util.parse_datetime(prices[1]["t"])
    assert {"t", "p"} <= set(prices[0])
