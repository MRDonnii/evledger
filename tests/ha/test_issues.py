"""Repair issues when an entity or phone EV Ledger uses is gone, and EV Ledger's events in the logbook."""

from datetime import timedelta

from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.evledger.issues import CHECK_AFTER_SECONDS

from .test_routines import with_options
from .test_smart_charge import DOMAIN, setup


def issue(hass: HomeAssistant, key: str, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{key}_{entry.entry_id}")


async def later(hass: HomeAssistant, seconds: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


async def test_a_gone_entity_is_reported_after_the_start_and_cleared_when_back(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    hass.states.async_remove("sensor.car_odometer")
    await later(hass, 60)
    assert issue(hass, "missing_entities", entry) is None, "not while the other integrations may still be loading"
    await later(hass, CHECK_AFTER_SECONDS + 1)
    found = issue(hass, "missing_entities", entry)
    assert found is not None and found.translation_placeholders == {"name": "Bil", "entities": "sensor.car_odometer"}
    assert found.severity == ir.IssueSeverity.WARNING and not found.is_fixable

    hass.states.async_set("sensor.car_odometer", "1000")
    assert await hass.config_entries.async_reload(entry.entry_id), "e.g. after choosing it again under Configure"
    await hass.async_block_till_done()
    assert issue(hass, "missing_entities", entry) is None, "cleared at once"

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()


async def test_a_gone_phone_is_reported(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    await with_options(hass, entry)  # messages to notify.mobile_app_a, which does not exist
    await later(hass, CHECK_AFTER_SECONDS + 1)
    found = issue(hass, "missing_phones", entry)
    assert found is not None and found.translation_placeholders["phones"] == "mobile_app_a"
    assert issue(hass, "missing_entities", entry) is None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, "missing_phones", entry) is None, "a removed car leaves no issues"


async def test_charges_and_tyre_changes_in_the_logbook(hass: HomeAssistant):
    from custom_components.evledger import logbook

    described = {}
    logbook.async_describe_events(hass, lambda domain, event_type, describe: described.update(
        {event_type: (domain, describe)}))

    def text(event_type: str, data: dict) -> dict:
        domain, describe = described[event_type]
        assert domain == DOMAIN
        return describe(Event(event_type, data))

    assert text("evledger_charge_finished", {"vehicle": "Bil", "kwh": 7.53, "price": 12.3, "price_currency": "DKK"}) \
        == {"name": "Bil", "message": "finished charging at home: 7.5 kWh for 12.30 DKK"}
    assert text("evledger_charge_finished", {"vehicle": "Bil", "kwh": 2.0, "price": None})["message"] \
        == "finished charging at home: 2.0 kWh (price not known yet)"
    assert text("evledger_tyres_changed", {"vehicle": "Bil", "set": "Vinter", "odometer_km": 12345.6})["message"] \
        == "got the tyre set Vinter at 12346 km"

    hass.config.language = "da"
    logbook.async_describe_events(hass, lambda domain, event_type, describe: described.update(
        {event_type: (domain, describe)}))
    assert text("evledger_charge_finished", {"vehicle": "Bil", "kwh": 7.53, "price": 12.3, "price_currency": "kr"}) \
        == {"name": "Bil", "message": "ladede færdig hjemme: 7,5 kWh for 12,30 kr"}
