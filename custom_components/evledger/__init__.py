"""The EV Ledger integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_KWH,
    ATTR_LOCATION_NAME,
    ATTR_NOTE,
    ATTR_PRICE,
    ATTR_STARTED_AT,
    CONF_CURRENCY,
    CONF_VEHICLE_NAME,
    DEFAULT_CURRENCY,
    DOMAIN,
    LOCATION_PUBLIC,
    PLATFORMS,
    SERVICE_LOG_PUBLIC_CHARGE,
)
from .coordinator import EvLedgerCoordinator
from .models import ChargeSession
from .providers.registry import build_charger_providers, build_vehicle_provider
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
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an EV Ledger config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN) and hass.services.has_service(
            DOMAIN, SERVICE_LOG_PUBLIC_CHARGE
        ):
            hass.services.async_remove(DOMAIN, SERVICE_LOG_PUBLIC_CHARGE)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_LOG_PUBLIC_CHARGE):
        return

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

    hass.services.async_register(
        DOMAIN,
        SERVICE_LOG_PUBLIC_CHARGE,
        _handle_log_public_charge,
        schema=LOG_PUBLIC_CHARGE_SCHEMA,
    )
