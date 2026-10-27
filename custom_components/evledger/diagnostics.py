"""Diagnostics download: what EV Ledger sees and has recorded, for a support request (positions and phones hidden)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BATTERY_ENTITY,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_MONTA_LAST_CHARGE_ENTITY,
    CONF_ODOMETER_ENTITY,
    CONF_OUTSIDE_TEMP_ENTITY,
    CONF_SPOT_PRICE_ENTITY,
    CONF_ZAPTEC_CHARGING_ENTITY,
    CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
    CONF_ZAPTEC_POWER_ENTITY,
    CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
    DOMAIN,
)
from .smart.const import CONF_NOTIFY_SERVICES, CONF_NOTIFY_URL

# Where the car has been, and who gets the messages.
TO_REDACT = {"ocm_api_key", "latitude", "longitude", "start_lat", "start_lon", "end_lat", "end_lon", "gps_accuracy",
             CONF_NOTIFY_SERVICES, CONF_NOTIFY_URL}
SOURCES = (CONF_BATTERY_ENTITY, CONF_ODOMETER_ENTITY, CONF_DEVICE_TRACKER_ENTITY, CONF_CHARGING_BINARY_ENTITY,
           CONF_LOCKED_ENTITY, CONF_OUTSIDE_TEMP_ENTITY, CONF_ZAPTEC_POWER_ENTITY, CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
           CONF_ZAPTEC_CHARGING_ENTITY, CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY, CONF_MONTA_LAST_CHARGE_ENTITY,
           CONF_SPOT_PRICE_ENTITY)
RECENT = 10


def _state(hass: HomeAssistant, entity_id: str | None) -> dict[str, Any] | None:
    """An entity's state; a price list is only counted (it is long and the same for everyone)."""
    state = hass.states.get(entity_id) if entity_id else None
    if state is None:
        return None
    attributes = {key: (f"<{len(value)} items>" if isinstance(value, list) and len(value) > 5 else value)
                  for key, value in state.attributes.items()}
    return {"state": state.state, "attributes": attributes, "last_updated": state.last_updated.isoformat()}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    result: dict[str, Any] = {"config": async_redact_data(dict(entry.data), TO_REDACT)}
    if coordinator is None:
        return result
    store = coordinator.store
    trips, charges = store.trips, store.charges
    open_home = store.get_open_charge("home")
    result["ledger"] = async_redact_data({
        "last_update_success": coordinator.last_update_success,
        "odometer_km": coordinator.odometer_now(),
        "trips": len(trips),
        "charges": len(charges),
        "pending_review": sum(1 for charge in charges if charge.needs_review),
        "open_trip": (trip.to_dict() if (trip := store.get_open_trip()) else None),
        "open_charge_home": open_home.to_dict() if open_home else None,
        "open_charge_public": (charge.to_dict() if (charge := store.get_open_charge("public")) else None),
        "meter": store.meter(open_home.id) if open_home else None,
        "recent_trips": [trip.to_dict() for trip in trips[:RECENT]],
        "recent_charges": [charge.to_dict() for charge in charges[:RECENT]],
        "tyre_sets": len(store.tyre_sets),
    }, TO_REDACT)
    sources = {entry.data.get(key) for key in SOURCES if entry.data.get(key)}
    result["sources"] = async_redact_data({entity_id: _state(hass, entity_id) for entity_id in sorted(sources)},
                                          TO_REDACT)
    planner = getattr(coordinator, "smart", None)
    if planner is not None:
        schedule = planner.schedule
        result["smart_charging"] = async_redact_data({
            "options": planner.options,
            "mode": planner.mode,
            "default_mode": planner.default_mode,
            "status": planner.status,
            "charger_state": planner.charger_state,
            "controls_charger": planner.controls_charger,
            "car_present": planner.car_present,
            "shared_charger": planner.shared,
            "charge_desired": planner.charge_desired,
            "battery_level_assumed": planner.soc_assumed,
            "last_soc": planner.last_soc,
            "car_limit": planner.car_limit(),
            "price_slots": planner.slot_count,
            "price_unit": planner.price_unit,
            "deadline": planner.deadline.isoformat() if planner.deadline else None,
            "deadline_source": planner.deadline_source,
            "target": planner.target,
            "settings": planner.settings,
            "times": {key: value.isoformat() for key, value in planner.times.items()},
            "flags": planner.flags,
            "learned": planner.routines.learned,
            "schedule": {
                "blocks": [{"start": block.start.isoformat(), "end": block.end.isoformat(), "kwh": round(block.kwh, 2),
                            "cost": round(block.cost, 2), "estimated": block.estimated} for block in schedule.blocks],
                "energy_kwh": schedule.energy_kwh,
                "cost": schedule.cost,
                "shortfall_kwh": schedule.shortfall_kwh,
            },
            "controller": {
                "last_command": planner.controller.last_command,
                "last_command_at": (planner.controller.last_command_at.isoformat()
                                    if planner.controller.last_command_at else None),
                "start_attempts": planner.controller.start_attempts,
                "blocked": planner.controller.blocked,
                "external_stops": planner.controller.external_stops,
            },
        }, TO_REDACT)
    return result
