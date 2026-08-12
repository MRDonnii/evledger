"""Resolve a device to its individual EV Ledger role entities.

Lets the config flow offer "pick your car" / "pick your charger" instead of
hunting down individual sensors one at a time. Matching is by entity_id
suffix, since these integrations don't tag entities with a stable "role" —
this is inherently a little fragile against upstream naming changes, which is
why every resolved value still lands in an editable form afterwards rather
than being applied blind.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

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
)


def _entities_for_device(hass: HomeAssistant, device_id: str) -> list[er.RegistryEntry]:
    registry = er.async_get(hass)
    return er.async_entries_for_device(registry, device_id, include_disabled_entities=False)


def _first_matching(
    entries: list[er.RegistryEntry], domain: str, suffix: str, exclude: str | None = None
) -> str | None:
    for entry in entries:
        if entry.domain != domain:
            continue
        if not entry.entity_id.endswith(suffix):
            continue
        if exclude and exclude in entry.entity_id:
            continue
        return entry.entity_id
    return None


def resolve_tesla_vehicle_entities(hass: HomeAssistant, device_id: str) -> dict[str, str | None]:
    """Guess battery/odometer/tracker/charging/lock/temperature entities for a Tesla device."""
    entries = _entities_for_device(hass, device_id)
    return {
        CONF_BATTERY_ENTITY: _first_matching(entries, "sensor", "_battery"),
        CONF_ODOMETER_ENTITY: _first_matching(entries, "sensor", "_odometer"),
        CONF_DEVICE_TRACKER_ENTITY: _first_matching(
            entries, "device_tracker", "_location_tracker", exclude="destination"
        ),
        CONF_CHARGING_BINARY_ENTITY: _first_matching(entries, "binary_sensor", "_charging"),
        CONF_LOCKED_ENTITY: _first_matching(entries, "lock", "_doors"),
        CONF_OUTSIDE_TEMP_ENTITY: _first_matching(entries, "sensor", "_temperature_outside"),
    }


def resolve_zaptec_charger_entities(hass: HomeAssistant, device_id: str) -> dict[str, str | None]:
    """Guess power/session-energy/charging/completed-energy entities for a Zaptec device."""
    entries = _entities_for_device(hass, device_id)
    return {
        CONF_ZAPTEC_POWER_ENTITY: _first_matching(entries, "sensor", "_charge_power"),
        CONF_ZAPTEC_SESSION_ENERGY_ENTITY: _first_matching(
            entries, "sensor", "_session_total_charge"
        ),
        CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY: _first_matching(
            entries, "sensor", "_completed_session_energy"
        ),
        CONF_ZAPTEC_CHARGING_ENTITY: (
            _first_matching(entries, "switch", "_charging")
            or _first_matching(entries, "binary_sensor", "_charging")
        ),
    }


def resolve_monta_charger_entities(hass: HomeAssistant, device_id: str) -> dict[str, str | None]:
    """Guess the last-charge entity for a Monta charger device, plus its account's wallet."""
    entries = _entities_for_device(hass, device_id)
    result: dict[str, str | None] = {
        CONF_MONTA_LAST_CHARGE_ENTITY: _first_matching(entries, "sensor", "_last_charge"),
        CONF_MONTA_WALLET_ENTITY: None,
    }

    device_registry = dr.async_get(hass)
    charger_device = device_registry.async_get(device_id)
    if charger_device is None:
        return result

    # The wallet balance lives on a separate sibling "Monta account" device
    # under the same config entry, not on the charger device itself.
    entity_registry = er.async_get(hass)
    for sibling in device_registry.devices.values():
        if sibling.id == device_id:
            continue
        if not (set(sibling.config_entries) & set(charger_device.config_entries)):
            continue
        sibling_entries = er.async_entries_for_device(
            entity_registry, sibling.id, include_disabled_entities=False
        )
        wallet_entity = _first_matching(sibling_entries, "sensor", "_wallet")
        if wallet_entity:
            result[CONF_MONTA_WALLET_ENTITY] = wallet_entity
            break

    return result


def resolve_spot_price_entities(hass: HomeAssistant, device_id: str) -> dict[str, str | None]:
    """Guess the electricity-price entity for a price-sensor device.

    Strømligning devices carry an unambiguous suffix for the VAT-inclusive
    current price. Other price integrations (Nordpool, Energi Data Service,
    ...) don't share a stable naming convention, so this falls through a
    series of looser heuristics, ending with a live-state check for a
    ".../kWh" unit — which catches Nordpool-style entities regardless of
    their entity_id.
    """
    entries = _entities_for_device(hass, device_id)

    stromligning_match = _first_matching(entries, "sensor", "_current_price_vat")
    if stromligning_match:
        return {CONF_SPOT_PRICE_ENTITY: stromligning_match}

    for needle in ("current_price", "spot_price", "spotprice"):
        for entry in entries:
            if entry.domain != "sensor":
                continue
            if needle in entry.entity_id and "ex_vat" not in entry.entity_id:
                return {CONF_SPOT_PRICE_ENTITY: entry.entity_id}

    for entry in entries:
        if entry.domain != "sensor":
            continue
        state = hass.states.get(entry.entity_id)
        unit = (state.attributes.get("unit_of_measurement") if state else None) or ""
        if "/kwh" in unit.lower():
            return {CONF_SPOT_PRICE_ENTITY: entry.entity_id}

    return {CONF_SPOT_PRICE_ENTITY: None}
