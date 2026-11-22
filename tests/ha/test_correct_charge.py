"""Correcting a charge's price per kWh (Claude AI, 2026-10-27; user: "så man selv kan rette kWh-prisen på en ladning
hvis den ikke er korrekt")."""

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .test_public_default_price import charge_away, public_charges, set_default_price, two_operators
from .test_smart_charge import DOMAIN, setup

CAR = "sensor.bil_last_charge"


async def test_the_price_per_kwh_is_corrected_by_the_cards_entity(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert charge.price_source == "default"
    await hass.services.async_call(DOMAIN, "update_charge", {"entity_id": CAR, "charge_id": charge.id,
                                                             "price_per_kwh": 3.95}, blocking=True)
    [charge] = public_charges(hass, entry)
    assert charge.price == pytest.approx(round(charge.kwh * 3.95, 2))
    assert (charge.price_source, charge.needs_review) == ("manual", False)
    assert hass.states.get(CAR).state == str(charge.price), "the card sees the new price"


async def test_a_typed_in_price_is_not_replaced_by_a_later_log(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    await hass.services.async_call(DOMAIN, "update_charge", {"entity_id": CAR, "charge_id": charge.id, "kwh": 30.0,
                                                             "price_per_kwh": 3.0}, blocking=True)
    await hass.services.async_call(DOMAIN, "log_public_charge", {"entry_id": entry.entry_id, "kwh": 10.0,
                                                                 "price": 40.0}, blocking=True)
    corrected = next(c for c in public_charges(hass, entry) if c.id == charge.id)
    assert (corrected.kwh, corrected.price) == (30.0, 90.0)
    assert len(public_charges(hass, entry)) == 2, "the logged charge is a charge of its own"


async def test_corrected_kwh_of_an_auto_priced_charge_keeps_its_price_per_kwh(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    await hass.services.async_call(DOMAIN, "update_charge", {"entry_id": entry.entry_id, "charge_id": charge.id,
                                                             "kwh": 50.0}, blocking=True)
    [charge] = public_charges(hass, entry)
    assert (charge.kwh, charge.price, charge.price_source) == (50.0, 130.0, "default")


async def test_the_price_is_remembered_for_the_place(hass: HomeAssistant, request, freezer):
    from datetime import timedelta
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.3, "longitude": 9.1})
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    await hass.services.async_call(DOMAIN, "update_charge", {"entity_id": CAR, "charge_id": charge.id,
                                                             "price_per_kwh": 3.95, "location_name": "Clever Bilka",
                                                             "remember": "place"}, blocking=True)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert [(p["name"], p["price"]) for p in coordinator.store.settings["places"]] == [("Clever Bilka", 3.95)]
    freezer.tick(timedelta(days=1))
    await charge_away(hass, entry, "30", "60")
    latest = max(public_charges(hass, entry), key=lambda c: c.started_at)
    assert (latest.location_name, latest.price_source) == ("Clever Bilka", "place")
    assert latest.price == pytest.approx(round(latest.kwh * 3.95, 2))


async def test_the_price_is_remembered_as_the_operators_fast_price(hass: HomeAssistant, request, aioclient_mock):
    two_operators(aioclient_mock)
    entry, _ = await setup(hass, request)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.ocm_api_key = "k"
    await hass.services.async_call(DOMAIN, "set_operator_price", {"entry_id": entry.entry_id, "operator": "Clever",
                                                                  "price": 4.04, "fast_price": 5.48}, blocking=True)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.13675, "longitude": 8.97065})
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    assert (charge.operator, charge.station_kw) == ("CLEVER", 150)
    await hass.services.async_call(DOMAIN, "update_charge", {"entity_id": CAR, "charge_id": charge.id,
                                                             "price_per_kwh": 5.2, "remember": "operator"},
                                   blocking=True)
    assert coordinator.store.settings["operator_prices"] == {"CLEVER": {"price": 4.04, "fast": 5.2}}
    assert coordinator.operator_price("Clever", 22) == pytest.approx(4.04)


async def test_what_cannot_be_corrected(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    await set_default_price(hass, entry, 2.6)
    await charge_away(hass, entry)
    [charge] = public_charges(hass, entry)
    with pytest.raises(ServiceValidationError):  # no operator to remember the price for
        await hass.services.async_call(DOMAIN, "update_charge", {"entity_id": CAR, "charge_id": charge.id,
                                                                 "price_per_kwh": 3.0, "remember": "operator"},
                                       blocking=True)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "update_charge", {"entity_id": CAR, "charge_id": "nope",
                                                                 "price_per_kwh": 3.0}, blocking=True)
    with pytest.raises(vol.Invalid):  # which car?
        await hass.services.async_call(DOMAIN, "update_charge", {"charge_id": charge.id, "price_per_kwh": 3.0},
                                       blocking=True)
    with pytest.raises(vol.Invalid):  # a total and a price per kWh
        await hass.services.async_call(DOMAIN, "update_charge", {"entity_id": CAR, "charge_id": charge.id,
                                                                 "price": 50.0, "price_per_kwh": 3.0}, blocking=True)
    [unchanged] = public_charges(hass, entry)
    assert (unchanged.price, unchanged.price_source) == (charge.price, "default")


async def test_the_phone_corrects_the_price_per_kwh(hass: HomeAssistant, request):
    from .test_failing_services import with_options
    from .test_routines import phones

    sent = phones(hass)
    entry, _ = await setup(hass, request)
    planner = await with_options(hass, entry, notify_only_home=True)
    planner.flags["ask_public_price"] = True
    await set_default_price(hass, entry, 2.6)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": 56.3, "longitude": 9.1})
    await charge_away(hass, entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    [note] = [m for m in sent if m.get("title", "").endswith("offentlig ladning")]
    assert "standardprisen 2,60 kr/kWh" in note["message"]
    fix = note["data"]["actions"][0]
    assert (fix["title"], fix["behavior"]) == ("Ret kr/kWh", "textInput")
    hass.bus.async_fire("mobile_app_notification_action", {"action": fix["action"], "reply_text": "Ionity 3,79"})
    await hass.async_block_till_done(wait_background_tasks=True)
    [charge] = public_charges(hass, entry)
    assert charge.price == pytest.approx(round(charge.kwh * 3.79, 2)) and charge.price_source == "manual"
    assert charge.location_name == "Ionity"
    assert any(m.get("title", "").endswith("offentlig ladning rettet") and "3,79 kr/kWh" in m["message"]
               for m in sent)
