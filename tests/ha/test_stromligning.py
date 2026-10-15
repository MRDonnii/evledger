"""Smart charging with Strømligning: today's prices on its price sensor, tomorrow's on a binary sensor of its own, and
next to them price lists that are only parts of the price (the spot price, the grid tariff)."""

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_smart_charge import DOMAIN

CURRENT = "sensor.stromligning_current_price_vat"
TOMORROW = "binary_sensor.stromligning_tomorrow_available_vat"
# Strømligning's price lists, enabled by default: (domain, key, price, day). Only the first two are the whole price.
ENTITIES = (("sensor", "current_price_vat", 2.5, 0), ("sensor", "spotprice_vat", 0.8, 0),
            ("sensor", "distribution_vat", 0.4, 0), ("binary_sensor", "tomorrow_available_vat", 2.2, 1),
            ("binary_sensor", "tomorrow_spotprice_vat", 0.6, 1))


def day(offset: int, price: float) -> list[dict]:
    """A day of quarter prices as Strømligning gives them (aware datetimes)."""
    start = dt_util.start_of_local_day() + timedelta(days=offset)
    return [{"price": price, "start": start + timedelta(minutes=15 * q), "end": start + timedelta(minutes=15 * (q + 1))}
            for q in range(96)]


def stromligning(hass: HomeAssistant) -> None:
    """Strømligning's device and entities in the registry (unique ids "<key>_<config entry id>")."""
    prices = MockConfigEntry(domain="stromligning", entry_id="01JSTROM")
    prices.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=prices.entry_id,
                                                    identifiers={("stromligning", prices.entry_id)})
    registry = er.async_get(hass)
    for domain, key, _price, _day in ENTITIES:
        registry.async_get_or_create(domain, "stromligning", f"{key}_01jstrom", device_id=device.id,
                                     config_entry=prices, suggested_object_id=f"stromligning_{key}")


def publish(hass: HomeAssistant, tomorrow_out: bool = True) -> None:
    for domain, key, price, offset in ENTITIES:
        state = ("on" if tomorrow_out else "off") if domain == "binary_sensor" else str(price)
        hass.states.async_set(f"{domain}.stromligning_{key}", state,
                              {"prices": day(offset, price), "unit_of_measurement": "kr/kWh"})


async def ledger(hass: HomeAssistant, **smart):
    hass.states.async_set("sensor.car_battery", "50")
    hass.states.async_set("sensor.car_odometer", "1000")
    hass.states.async_set("device_tracker.car", "home", {"latitude": 56.0, "longitude": 10.0})
    hass.states.async_set("binary_sensor.car_charging", "off")
    entry = MockConfigEntry(domain=DOMAIN, title="Bil", data={
        "vehicle_name": "Bil", "vehicle_provider": "tesla_custom", "currency": "DKK",
        "battery_entity": "sensor.car_battery", "odometer_entity": "sensor.car_odometer",
        "device_tracker_entity": "device_tracker.car", "charging_binary_entity": "binary_sensor.car_charging",
        "charger_providers": ["manual", "spot_price"], "spot_price_entity": CURRENT, "battery_capacity_kwh": 60,
        "smart_charge": {"enabled": True, "charger_type": "none", **smart}})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return hass.data[DOMAIN][entry.entry_id].smart


def tomorrow_prices(planner) -> set:
    tomorrow = (dt_util.now() + timedelta(days=1)).date()
    return {(slot.price, slot.estimated) for slot in planner.timeline
            if dt_util.as_local(slot.start).date() == tomorrow}


async def test_the_whole_price_today_and_tomorrow(hass: HomeAssistant):
    # Before: every price list on the device was used, the last one winning: the grid tariff alone today, the spot
    # price alone tomorrow.
    stromligning(hass)
    publish(hass)
    planner = await ledger(hass)
    assert planner.options["price_entities"] == [CURRENT, TOMORROW]
    assert planner.price_now(dt_util.now()) == 2.5
    assert tomorrow_prices(planner) == {(2.2, False)}


async def test_tomorrow_also_when_stromligning_loads_after_ev_ledger(hass: HomeAssistant):
    stromligning(hass)  # in the registry from the last run, but no states yet
    planner = await ledger(hass)
    assert planner.options["price_entities"] == [CURRENT, TOMORROW]
    publish(hass)
    await hass.async_block_till_done()
    assert planner.price_now(dt_util.now()) == 2.5 and tomorrow_prices(planner) == {(2.2, False)}


async def test_tomorrow_s_forecast_counts_as_estimated(hass: HomeAssistant):
    # With Strømligning's forecasts on, its tomorrow sensor is off before 13:00 but lists the forecast.
    stromligning(hass)
    publish(hass, tomorrow_out=False)
    planner = await ledger(hass)
    assert all(estimated for _price, estimated in tomorrow_prices(planner)), "estimated by EV Ledger itself"
    assert (2.2, False) not in tomorrow_prices(planner)


async def test_price_sensors_chosen_by_hand_the_first_counts(hass: HomeAssistant):
    stromligning(hass)
    publish(hass)
    planner = await ledger(hass, price_entities=[CURRENT, "sensor.stromligning_spotprice_vat", TOMORROW])
    assert planner.price_now(dt_util.now()) == 2.5, "the spot price alone only fills gaps"
    assert tomorrow_prices(planner) == {(2.2, False)}


async def test_a_hand_picked_price_sensor_brings_its_tomorrow_sensor(hass: HomeAssistant):
    # Only today's price sensor chosen by hand: without its tomorrow sensor the night was planned on yesterday's prices.
    stromligning(hass)
    publish(hass)
    planner = await ledger(hass, price_entities=[CURRENT])
    assert planner.options["price_entities"] == [CURRENT, TOMORROW]
    assert tomorrow_prices(planner) == {(2.2, False)}
