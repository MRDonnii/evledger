"""Smart charging inside EV Ledger: settings from the vehicle and charger EV Ledger already knows."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ..const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_MODEL_LABEL,
    CONF_RATED_WH_PER_KM,
    CONF_SPOT_PRICE_ENTITY,
    CONF_TESLA_MODEL_KEY,
    CONF_ZAPTEC_POWER_ENTITY,
)
from . import vehicles
from .const import (
    CHARGER_NONE,
    CHARGER_ZAPTEC,
    CONF_CAPACITY,
    CONF_CAR_PLUGGED_ENTITY,
    CONF_CAR_TRACKER,
    CONF_CHARGER_TYPE,
    CONF_PRICE_ENTITIES,
    CONF_SMART_CHARGE,
    CONF_SMART_ENABLED,
    CONF_ZAPTEC_MODE_ENTITY,
)
from .plan import parse_price_attributes

# Unique id suffix of the Zaptec integration's "Charger mode" sensor.
ZAPTEC_MODE_SUFFIX = "_charger_operation_mode"

# EV Ledger's Tesla model keys → the card's body artwork.
BODIES = (("model3_2023", "model_3_highland"), ("model3_2024", "model_3_highland"), ("model3", "model_3"),
          ("modely_2025", "model_y_juniper"), ("modely", "model_y"), ("models", "model_s"), ("modelx", "model_x"),
          ("cybertruck", "cybertruck"))


def smart_settings(entry: ConfigEntry) -> dict:
    return dict(entry.data.get(CONF_SMART_CHARGE) or {})


def smart_enabled(entry: ConfigEntry) -> bool:
    return bool(smart_settings(entry).get(CONF_SMART_ENABLED))


def sibling(hass: HomeAssistant, entity_id: str | None, domain: str, *, suffix: str | None = None,
            device_class: str | None = None) -> str | None:
    """An entity on the same device as entity_id, by unique id suffix or device class."""
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id) if entity_id else None
    if entry is None or entry.device_id is None:
        return None
    for other in er.async_entries_for_device(registry, entry.device_id):
        if other.domain != domain or other.disabled_by:
            continue
        if suffix and other.unique_id.endswith(suffix):
            return other.entity_id
        if device_class and (other.device_class or other.original_device_class) == device_class:
            return other.entity_id
    return None


def planner_options(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    """The planner's settings: chosen in EV Ledger's smart charging step, the rest from the ledger."""
    return planner_options_from(hass, entry.data, smart_settings(entry))


def planner_options_from(hass: HomeAssistant, data, smart: dict) -> dict:
    options = {key: value for key, value in smart.items() if value not in (None, "", [])}
    options[CONF_BATTERY_ENTITY] = data[CONF_BATTERY_ENTITY]
    if data.get(CONF_DEVICE_TRACKER_ENTITY):
        options[CONF_CAR_TRACKER] = data[CONF_DEVICE_TRACKER_ENTITY]
    if data.get(CONF_BATTERY_CAPACITY_KWH):
        options[CONF_CAPACITY] = data[CONF_BATTERY_CAPACITY_KWH]
    if not options.get(CONF_PRICE_ENTITIES) and data.get(CONF_SPOT_PRICE_ENTITY):
        options[CONF_PRICE_ENTITIES] = price_entities(hass, data[CONF_SPOT_PRICE_ENTITY])
    zaptec_mode = sibling(hass, data.get(CONF_ZAPTEC_POWER_ENTITY), "sensor", suffix=ZAPTEC_MODE_SUFFIX)
    if not options.get(CONF_CHARGER_TYPE):
        options[CONF_CHARGER_TYPE] = CHARGER_ZAPTEC if zaptec_mode else CHARGER_NONE
    if options[CONF_CHARGER_TYPE] == CHARGER_ZAPTEC and not options.get(CONF_ZAPTEC_MODE_ENTITY) and zaptec_mode:
        options[CONF_ZAPTEC_MODE_ENTITY] = zaptec_mode
    if not options.get(CONF_CAR_PLUGGED_ENTITY):
        plug = sibling(hass, data[CONF_BATTERY_ENTITY], "binary_sensor", device_class="plug")
        if plug:
            options[CONF_CAR_PLUGGED_ENTITY] = plug
    return options


def price_entities(hass: HomeAssistant, spot_price: str) -> list[str]:
    """The ledger's spot price sensor plus the price lists next to it, e.g. Strømligning's
    separate "tomorrow" sensor, so the plan sees tomorrow's prices as soon as they are out."""
    found = [spot_price]
    registry = er.async_get(hass)
    entry = registry.async_get(spot_price)
    if entry is None or entry.device_id is None:
        return found
    for other in er.async_entries_for_device(registry, entry.device_id):
        if other.entity_id == spot_price or other.domain not in ("sensor", "binary_sensor") or other.disabled_by:
            continue
        state = hass.states.get(other.entity_id)
        if state and parse_price_attributes(dict(state.attributes)):
            found.append(other.entity_id)
    return found


def ledger_vehicle(entry: ConfigEntry) -> vehicles.Vehicle | None:
    """The model chosen in EV Ledger, if any."""
    data = entry.data
    key = data.get(CONF_TESLA_MODEL_KEY)
    if not key or not data.get(CONF_BATTERY_CAPACITY_KWH):
        return None
    body = next((art for prefix, art in BODIES if str(key).startswith(prefix)), "model_3")
    label = str(data.get(CONF_MODEL_LABEL) or key)
    return vehicles.Vehicle(
        key=str(key), name=label, family=vehicles.family_of(label) or "Tesla",
        capacity_kwh=float(data[CONF_BATTERY_CAPACITY_KWH]),
        consumption_wh_km=float(data.get(CONF_RATED_WH_PER_KM) or 0), range_km=0, body=body,
    )
