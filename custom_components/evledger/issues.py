"""Repair issues (Settings → Repairs): a car, charger or price entity, or a phone, EV Ledger reads or sends to is gone,
e.g. renamed by its integration. Without them the ledger silently stops recording trips or charges."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .const import (
    CONF_BATTERY_ENTITY,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_MONTA_LAST_CHARGE_ENTITY,
    CONF_MONTA_WALLET_ENTITY,
    CONF_ODOMETER_ENTITY,
    CONF_OUTSIDE_TEMP_ENTITY,
    CONF_SPOT_PRICE_ENTITY,
    CONF_ZAPTEC_CHARGING_ENTITY,
    CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
    CONF_ZAPTEC_POWER_ENTITY,
    CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
    DOMAIN,
)
from .smart.const import (
    CONF_CAR_CLIMATE,
    CONF_CAR_PLUGGED_ENTITY,
    CONF_CHARGE_SWITCH,
    CONF_NOTIFY_SERVICES,
    CONF_PRICE_ENTITIES,
    CONF_SMART_CHARGE,
    CONF_SMART_ENABLED,
    CONF_TRIP_CALENDAR,
    CONF_ZAPTEC_MODE_ENTITY,
)

# The car and charger integrations can take minutes to load after a restart: their entities have no state until then.
CHECK_AFTER_SECONDS = 300
CHECK_EVERY = timedelta(minutes=30)
LEDGER_SOURCES = (CONF_BATTERY_ENTITY, CONF_ODOMETER_ENTITY, CONF_DEVICE_TRACKER_ENTITY, CONF_CHARGING_BINARY_ENTITY,
                  CONF_LOCKED_ENTITY, CONF_OUTSIDE_TEMP_ENTITY, CONF_ZAPTEC_POWER_ENTITY,
                  CONF_ZAPTEC_SESSION_ENERGY_ENTITY, CONF_ZAPTEC_CHARGING_ENTITY, CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
                  CONF_MONTA_LAST_CHARGE_ENTITY, CONF_MONTA_WALLET_ENTITY, CONF_SPOT_PRICE_ENTITY)
SMART_SOURCES = (CONF_ZAPTEC_MODE_ENTITY, CONF_CHARGE_SWITCH, CONF_CAR_PLUGGED_ENTITY, CONF_CAR_CLIMATE,
                 CONF_TRIP_CALENDAR)
MISSING_ENTITIES = "missing_entities"
MISSING_PHONES = "missing_phones"


def configured_entities(entry: ConfigEntry) -> list[str]:
    """The entities chosen in EV Ledger's setup (with smart charging: also those chosen for it)."""
    data = entry.data
    found = [data.get(key) for key in LEDGER_SOURCES]
    smart = data.get(CONF_SMART_CHARGE) or {}
    if smart.get(CONF_SMART_ENABLED):
        found += [smart.get(key) for key in SMART_SOURCES]
        found += list(smart.get(CONF_PRICE_ENTITIES) or [])
    return list(dict.fromkeys(entity_id for entity_id in found if entity_id))


def configured_phones(entry: ConfigEntry) -> list[str]:
    smart = entry.data.get(CONF_SMART_CHARGE) or {}
    if not smart.get(CONF_SMART_ENABLED):
        return []
    return [service.removeprefix("notify.") for service in smart.get(CONF_NOTIFY_SERVICES) or []]


def _issue_id(key: str, entry: ConfigEntry) -> str:
    return f"{key}_{entry.entry_id}"


@callback
def async_check(hass: HomeAssistant, entry: ConfigEntry, create: bool = True) -> None:
    """Raise (or clear) the issues. Without create only clears: right after a start the other integrations may still
    be loading, but an issue that was fixed (e.g. under Configure) goes at once."""
    missing = [entity_id for entity_id in configured_entities(entry) if hass.states.get(entity_id) is None]
    phones = [name for name in configured_phones(entry) if not hass.services.has_service("notify", name)]
    for key, gone, placeholder in ((MISSING_ENTITIES, missing, "entities"), (MISSING_PHONES, phones, "phones")):
        if gone and create:
            ir.async_create_issue(
                hass, DOMAIN, _issue_id(key, entry), is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key=key, translation_placeholders={"name": entry.title, placeholder: ", ".join(gone)},
            )
        elif not gone:
            ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry))


@callback
def async_watch(hass: HomeAssistant, entry: ConfigEntry) -> list[CALLBACK_TYPE]:
    """Check now (clearing only), a while after the start and then now and then; returns the cancel callbacks."""
    async_check(hass, entry, create=False)

    @callback
    def check(_now) -> None:
        async_check(hass, entry)

    return [async_call_later(hass, CHECK_AFTER_SECONDS, check), async_track_time_interval(hass, check, CHECK_EVERY)]


@callback
def async_remove(hass: HomeAssistant, entry: ConfigEntry) -> None:
    for key in (MISSING_ENTITIES, MISSING_PHONES):
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry))
