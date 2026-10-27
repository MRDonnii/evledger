"""The public default price per kWh (Claude AI, 2026-10-27; user: Tesla's chargers have one price around the clock, "so
when I charge away it should use that unless I put in something else")."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_smart_charge import DOMAIN, setup


async def poll(hass: HomeAssistant, entry) -> None:
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()


async def charge_away(hass: HomeAssistant, entry, start: str = "40", end: str = "70", counter: str | None = None,
                      freezer=None) -> None:
    """The car charges with the home charger idle: a public charge from start to end %."""
    hass.states.async_set("sensor.car_battery", start, {"unit_of_measurement": "%"})
    hass.states.async_set("binary_sensor.car_charging", "on")
    await poll(hass, entry)
    if freezer is not None:
        freezer.tick(timedelta(minutes=25))
    hass.states.async_set("sensor.car_battery", end, {"unit_of_measurement": "%"})
    if counter is not None:
        hass.states.async_set("sensor.car_energy_added", counter, {"unit_of_measurement": "kWh"})
    hass.states.async_set("binary_sensor.car_charging", "off")
    await poll(hass, entry)


def public_charges(hass: HomeAssistant, entry) -> list:
    return [c for c in hass.data[DOMAIN][entry.entry_id].store.charges if c.location_kind == "public"]


async def set_default_price(hass: HomeAssistant, entry, value: float) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("number", DOMAIN, f"{entry.entry_id}_public_default_price")
    assert entity_id, "the number entity exists"
    await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True)
    await hass.async_block_till_done()
    return entity_id


async def test_without_a_default_price_the_charge_waits_for_its_price(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert charge.price is None and charge.needs_review and charge.price_source is None


async def test_the_default_price_prices_a_public_charge(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    entity_id = await set_default_price(hass, entry, 2.6)
    assert float(hass.states.get(entity_id).state) == pytest.approx(2.6)
    await charge_away(hass, entry, "40", "70")
    [charge] = public_charges(hass, entry)
    capacity = hass.data[DOMAIN][entry.entry_id]._capacity_kwh()
    kwh = round(0.30 * capacity / 0.95, 1)
    assert charge.kwh == pytest.approx(kwh) and charge.price == pytest.approx(round(kwh * 2.6, 2))
    assert not charge.needs_review and charge.price_source == "default"


async def test_the_cars_own_counter_is_used(hass: HomeAssistant, request, freezer):
    # Tesla's charge_energy_added on the battery sensor's device, updated during this charge
    car = MockConfigEntry(domain="tesla_custom")
    car.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=car.entry_id, identifiers={("tesla_custom", "vin")})
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "tesla_custom", "vin_battery", device_id=device.id, config_entry=car,
                                 suggested_object_id="car_battery")
    registry.async_get_or_create("sensor", "tesla_custom", "vin_energy_added", device_id=device.id, config_entry=car,
                                 suggested_object_id="car_energy_added")
    hass.states.async_set("sensor.car_energy_added", "12.0", {"unit_of_measurement": "kWh"})  # the last charge's
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    await charge_away(hass, entry, "40", "70", counter="17.5", freezer=freezer)
    [charge] = public_charges(hass, entry)
    assert charge.kwh == pytest.approx(17.5) and charge.price == pytest.approx(45.5)


async def test_a_stale_counter_is_not_used(hass: HomeAssistant, request, freezer):
    # The counter did not move during the charge (the car slept): the battery's gain instead
    car = MockConfigEntry(domain="tesla_custom")
    car.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=car.entry_id, identifiers={("tesla_custom", "vin")})
    registry = er.async_get(hass)
    for unique, object_id in (("vin_battery", "car_battery"), ("vin_energy_added", "car_energy_added")):
        registry.async_get_or_create("sensor", "tesla_custom", unique, device_id=device.id, config_entry=car,
                                     suggested_object_id=object_id)
    hass.states.async_set("sensor.car_energy_added", "12.0", {"unit_of_measurement": "kWh"})
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    freezer.tick(timedelta(hours=3))  # the counter is from a charge long before
    await charge_away(hass, entry, "40", "70", freezer=freezer)
    [charge] = public_charges(hass, entry)
    capacity = hass.data[DOMAIN][entry.entry_id]._capacity_kwh()
    assert charge.kwh == pytest.approx(round(0.30 * capacity / 0.95, 1))


async def test_a_price_logged_later_replaces_the_default(hass: HomeAssistant, request, freezer):
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    await charge_away(hass, entry)
    freezer.tick(timedelta(hours=2))
    await hass.services.async_call(DOMAIN, "log_public_charge",
                                   {"entry_id": entry.entry_id, "kwh": 20.0, "price": 61.0}, blocking=True)
    [charge] = public_charges(hass, entry)
    assert (charge.kwh, charge.price, charge.price_source) == (20.0, 61.0, "manual"), "no second charge"


async def test_a_later_charge_is_not_taken_for_the_default_priced_one(hass: HomeAssistant, request, freezer):
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    await charge_away(hass, entry)
    freezer.tick(timedelta(hours=13))
    await hass.services.async_call(DOMAIN, "log_public_charge",
                                   {"entry_id": entry.entry_id, "kwh": 10.0, "price": 30.0}, blocking=True)
    charges = public_charges(hass, entry)
    assert sorted(c.price_source for c in charges) == ["default", "manual"]


async def test_the_default_price_survives_a_restart(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    entity_id = await set_default_price(hass, entry, 2.6)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_id).state) == pytest.approx(2.6)


def stub_superchargers(hass: HomeAssistant, sites: list) -> None:
    from homeassistant.util import dt as dt_util

    from custom_components.evledger.public_place import superchargers
    cache = superchargers(hass)
    cache.sites, cache.fetched, cache._loaded = sites, dt_util.utcnow(), True


async def test_a_zone_with_free_charging(hass: HomeAssistant, request):
    # Free at family: the zone's price (0) instead of the default price, and the zone's name as the place
    hass.states.async_set("zone.familie", "0", {"latitude": 56.5, "longitude": 9.5, "radius": 50,
                                                "friendly_name": "Frank & Birgit"})
    entry, _ = await setup(hass, request)
    entity_id = await set_default_price(hass, entry, 2.6)
    await hass.services.async_call(DOMAIN, "set_zone_price", {"entry_id": entry.entry_id, "zone": "zone.familie",
                                                              "price": 0}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["zone_prices"] == {"Frank & Birgit": 0.0}
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.5001, "longitude": 9.5001})
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert (charge.price, charge.price_source, charge.location_name, charge.zone) == (
        0.0, "zone", "Frank & Birgit", "zone.familie")
    assert not charge.needs_review and charge.kwh


async def test_a_zone_price_can_be_removed(hass: HomeAssistant, request):
    hass.states.async_set("zone.familie", "0", {"latitude": 56.5, "longitude": 9.5, "radius": 50,
                                                "friendly_name": "Frank & Birgit"})
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    for data in ({"price": 0}, {}):
        await hass.services.async_call(DOMAIN, "set_zone_price", {"entry_id": entry.entry_id, "zone": "zone.familie",
                                                                  **data}, blocking=True)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.5001, "longitude": 9.5001})
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert charge.price_source == "default" and charge.price > 0 and charge.location_name == "Frank & Birgit"


async def test_a_supercharger_gets_its_name(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    stub_superchargers(hass, [[56.120494, 8.12219, "Søndervig"], [56.4, 8.6, "Struer"]])
    await set_default_price(hass, entry, 2.6)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.12052, "longitude": 8.12215})
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert (charge.location_name, charge.price_source) == ("Supercharger Søndervig", "default")


async def test_far_from_a_supercharger_there_is_no_name(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    stub_superchargers(hass, [[56.120494, 8.12219, "Søndervig"]])
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.13, "longitude": 8.13})  # ~1.2 km away
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert charge.location_name is None and charge.needs_review, "no default price: it waits for its price"


async def test_the_supercharger_list_is_fetched_once_and_kept(hass: HomeAssistant, aioclient_mock):
    from custom_components.evledger.public_place import SITES_URL, superchargers
    aioclient_mock.get(SITES_URL, json=[
        {"name": "Søndervig, Denmark", "status": "OPEN", "address": {"country": "Denmark"},
         "gps": {"latitude": 56.120494, "longitude": 8.12219}},
        {"name": "Somewhere new, Denmark", "status": "CONSTRUCTION", "address": {"country": "Denmark"},
         "gps": {"latitude": 56.1205, "longitude": 8.1222}},
    ])
    cache = superchargers(hass)
    assert await cache.near(56.12052, 8.12215) == "Supercharger Søndervig"
    assert await cache.near(55.0, 9.0) is None
    assert aioclient_mock.call_count == 1, "fetched once, then kept for a week"
    assert cache.sites == [[56.120494, 8.12219, "Søndervig"]], "a site being built does not charge yet"


async def test_a_named_place_is_remembered(hass: HomeAssistant, request, freezer):
    # Named with its price the first time; the next charge there gets both by itself
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.3, "longitude": 9.1})
    await charge_away(hass, entry)
    await hass.services.async_call(DOMAIN, "log_public_charge", {"entry_id": entry.entry_id, "kwh": 20.0,
                                                                 "price": 79.0, "location_name": "Clever Bilka"},
                                   blocking=True)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.store.settings["places"][0]["name"] == "Clever Bilka"
    assert coordinator.store.settings["places"][0]["price"] == pytest.approx(3.95)
    freezer.tick(timedelta(days=2))
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.3004, "longitude": 9.1003})  # ~45 m
    await charge_away(hass, entry, "30", "60")
    latest = max(public_charges(hass, entry), key=lambda c: c.started_at)
    assert (latest.location_name, latest.price_source) == ("Clever Bilka", "place")
    assert latest.price == pytest.approx(round(latest.kwh * 3.95, 2))
    await hass.services.async_call(DOMAIN, "forget_place", {"entry_id": entry.entry_id, "name": "clever bilka"},
                                   blocking=True)
    assert coordinator.store.settings["places"] == []


async def test_an_operator_price_from_open_charge_map(hass: HomeAssistant, request, aioclient_mock):
    from custom_components.evledger.public_place import OCM_URL
    aioclient_mock.get(OCM_URL, json=[
        {"AddressInfo": {"Title": "Herning Rådhus", "Distance": 0.2}, "OperatorInfo": None},
        {"AddressInfo": {"Title": "Bilka Herning", "Distance": 0.04}, "OperatorInfo": {"Title": "CLEVER"}},
    ])
    entry, _ = await setup(hass, request)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.ocm_api_key = "test-key"
    await set_default_price(hass, entry, 2.6)
    await hass.services.async_call(DOMAIN, "set_operator_price", {"entry_id": entry.entry_id, "operator": "Clever",
                                                                  "price": 3.5}, blocking=True)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.1367, "longitude": 8.9706})
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert (charge.location_name, charge.operator, charge.price_source) == ("CLEVER – Bilka Herning", "CLEVER",
                                                                             "operator")
    assert charge.price == pytest.approx(round(charge.kwh * 3.5, 2))
    assert aioclient_mock.mock_calls[0][3]["X-API-Key"] == "test-key"


async def test_without_a_key_open_charge_map_is_not_asked(hass: HomeAssistant, request, aioclient_mock):
    from custom_components.evledger.public_place import OCM_URL
    aioclient_mock.get(OCM_URL, json=[])
    entry, _ = await setup(hass, request)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.1367, "longitude": 8.9706})
    await charge_away(hass, entry)
    assert not [call for call in aioclient_mock.mock_calls if "openchargemap" in str(call[1])]


@pytest.mark.parametrize(("reply", "name", "per_kwh"), [("Clever 3,95", "Clever", 3.95),
                                                         ("3,95 kr/kWh", None, 3.95),
                                                         ("Ionity Vejle 120", "Ionity Vejle", None)])
async def test_a_phone_answer_with_a_name(hass: HomeAssistant, request, reply, name, per_kwh):
    entry, _ = await setup(hass, request)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.4, "longitude": 9.4})
    await charge_away(hass, entry)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    [charge] = public_charges(hass, entry)
    coordinator.smart.routines.answer_public(charge.id, reply)
    await hass.async_block_till_done()
    [charge] = public_charges(hass, entry)
    estimate = coordinator.public_kwh_estimate(charge)[0]
    assert charge.kwh == pytest.approx(estimate)
    assert charge.price == pytest.approx(round(estimate * per_kwh, 2) if per_kwh else 120.0)
    assert charge.location_name == name
    assert [place["name"] for place in coordinator.store.settings.get("places") or []] == ([name] if name else [])
