"""The EV Ledger integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_CHARGE_ID,
    ATTR_KWH,
    ATTR_LOCATION_NAME,
    ATTR_NOTE,
    ATTR_PRICE,
    ATTR_STARTED_AT,
    ATTR_TRIP_ID,
    CONF_CURRENCY,
    CONF_VEHICLE_NAME,
    DEFAULT_CURRENCY,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_PUBLIC,
    PLATFORMS,
    SMART_PLATFORMS,
    SERVICE_DELETE_CHARGE,
    SERVICE_DELETE_TRIP,
    SERVICE_LOG_PUBLIC_CHARGE,
    SERVICE_UPDATE_CHARGE,
)
from .coordinator import EvLedgerCoordinator
from .models import ChargeSession
from .providers.registry import build_charger_providers, build_vehicle_provider
from .smart.planner import ChargePlanner
from .smart.setup import ledger_vehicle, planner_options, smart_enabled
from .store import EvLedgerStore

_LOGGER = logging.getLogger(__name__)

LOG_PUBLIC_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_KWH): vol.Coerce(float),
        vol.Required(ATTR_PRICE): vol.Coerce(float),
        vol.Optional(ATTR_LOCATION_NAME): cv.string,
        vol.Optional(ATTR_STARTED_AT): cv.datetime,
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

DELETE_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_CHARGE_ID): cv.string,
    }
)

UPDATE_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_CHARGE_ID): cv.string,
        vol.Optional(ATTR_KWH): vol.Coerce(float),
        vol.Optional(ATTR_PRICE): vol.Coerce(float),
        vol.Optional(ATTR_LOCATION_NAME): cv.string,
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

DELETE_TRIP_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_TRIP_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Ledger from a config entry."""
    store = EvLedgerStore(hass, entry.entry_id)
    await store.async_load()

    vehicle_provider = build_vehicle_provider(entry.data)
    charger_providers = build_charger_providers(entry.data)

    coordinator = EvLedgerCoordinator(
        hass,
        entry.entry_id,
        vehicle_name=entry.data[CONF_VEHICLE_NAME],
        currency=entry.data.get(CONF_CURRENCY, DEFAULT_CURRENCY),
        vehicle_provider=vehicle_provider,
        charger_providers=charger_providers,
        store=store,
    )
    # A failing first ledger update (e.g. the car's cloud is down right after a restart) must not hold
    # back the charger control; the ledger sensors catch up on the next update.
    await coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Smart charging (charge plans and charger control) is optional and off by default.
    coordinator.smart = None
    coordinator.platforms = list(PLATFORMS)
    if smart_enabled(entry):
        options = planner_options(hass, entry)
        coordinator.smart = ChargePlanner(
            hass, entry, options=lambda: options, vehicle=lambda: ledger_vehicle(entry),
            open_charge=lambda: coordinator.store.get_open_charge(LOCATION_HOME) is not None)
        coordinator.platforms += SMART_PLATFORMS

    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)
    if coordinator.smart:
        coordinator.smart.async_start()
        entry.async_on_unload(coordinator.smart.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an EV Ledger config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    platforms = getattr(coordinator, "platforms", PLATFORMS)
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            for service in (
                SERVICE_LOG_PUBLIC_CHARGE,
                SERVICE_UPDATE_CHARGE,
                SERVICE_DELETE_CHARGE,
                SERVICE_DELETE_TRIP,
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    async def _handle_log_public_charge(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ValueError(f"Unknown EV Ledger entry_id: {entry_id}")

        now = dt_util.utcnow()
        started_at = call.data.get(ATTR_STARTED_AT) or now

        # If there's an open/pending public session waiting for review, fill it in
        # rather than creating a duplicate — this is the normal case: the car
        # charged away from home, EV Ledger recorded the window but not the
        # price, and the user is now supplying it after the fact.
        pending = coordinator.store.get_latest_pending_review_charge()
        if pending is not None and pending.location_kind == LOCATION_PUBLIC:
            pending.kwh = call.data[ATTR_KWH]
            pending.price = call.data[ATTR_PRICE]
            pending.location_name = call.data.get(ATTR_LOCATION_NAME, pending.location_name)
            pending.note = call.data.get(ATTR_NOTE, pending.note)
            pending.needs_review = False
            await coordinator.store.async_upsert_charge(pending)
        else:
            session = ChargeSession(
                id=coordinator.store.new_id(),
                location_kind=LOCATION_PUBLIC,
                provider="manual",
                started_at=started_at.isoformat(),
                ended_at=now.isoformat(),
                kwh=call.data[ATTR_KWH],
                price=call.data[ATTR_PRICE],
                price_currency=coordinator.currency,
                location_name=call.data.get(ATTR_LOCATION_NAME),
                start_battery_pct=None,
                end_battery_pct=None,
                needs_review=False,
                note=call.data.get(ATTR_NOTE),
            )
            await coordinator.store.async_upsert_charge(session)

        await coordinator.async_request_refresh()

    async def _handle_delete_charge(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ValueError(f"Unknown EV Ledger entry_id: {entry_id}")

        deleted = await coordinator.store.async_delete_charge(call.data[ATTR_CHARGE_ID])
        if not deleted:
            raise ValueError(f"No charge with id {call.data[ATTR_CHARGE_ID]!r}")

        await coordinator.async_request_refresh()

    async def _handle_update_charge(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ValueError(f"Unknown EV Ledger entry_id: {entry_id}")

        charge = coordinator.store.get_charge(call.data[ATTR_CHARGE_ID])
        if charge is None:
            raise ValueError(f"No charge with id {call.data[ATTR_CHARGE_ID]!r}")
        if charge.location_kind != LOCATION_PUBLIC:
            raise ValueError("Only public charge sessions can be edited manually")

        if ATTR_KWH in call.data:
            charge.kwh = call.data[ATTR_KWH]
        if ATTR_PRICE in call.data:
            charge.price = call.data[ATTR_PRICE]
        if ATTR_LOCATION_NAME in call.data:
            charge.location_name = call.data[ATTR_LOCATION_NAME] or None
        if ATTR_NOTE in call.data:
            charge.note = call.data[ATTR_NOTE] or None
        charge.needs_review = charge.kwh is None or charge.price is None
        await coordinator.store.async_upsert_charge(charge)
        await coordinator.async_request_refresh()

    async def _handle_delete_trip(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ValueError(f"Unknown EV Ledger entry_id: {entry_id}")

        deleted = await coordinator.store.async_delete_trip(call.data[ATTR_TRIP_ID])
        if not deleted:
            raise ValueError(f"No trip with id {call.data[ATTR_TRIP_ID]!r}")

        await coordinator.async_request_refresh()

    services = (
        (SERVICE_LOG_PUBLIC_CHARGE, _handle_log_public_charge, LOG_PUBLIC_CHARGE_SCHEMA),
        (SERVICE_UPDATE_CHARGE, _handle_update_charge, UPDATE_CHARGE_SCHEMA),
        (SERVICE_DELETE_CHARGE, _handle_delete_charge, DELETE_CHARGE_SCHEMA),
        (SERVICE_DELETE_TRIP, _handle_delete_trip, DELETE_TRIP_SCHEMA),
    )
    for service, handler, schema in services:
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema)
