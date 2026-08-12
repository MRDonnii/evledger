"""Vehicle provider backed by the tesla_custom (or official Tesla) integration's entities."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from ...models import VehicleSnapshot
from .base import VehicleProvider


def _float_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


def _bool_state(
    hass: HomeAssistant, entity_id: str | None, true_states: tuple[str, ...]
) -> bool | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    return state.state in true_states


class TeslaCustomVehicleProvider(VehicleProvider):
    """Reads battery/odometer/location/charging/lock straight from tesla_custom entities."""

    provider_id = "tesla_custom"

    def __init__(
        self,
        battery_entity: str | None,
        odometer_entity: str | None,
        device_tracker_entity: str | None,
        charging_binary_entity: str | None,
        locked_entity: str | None,
    ) -> None:
        self._battery_entity = battery_entity
        self._odometer_entity = odometer_entity
        self._device_tracker_entity = device_tracker_entity
        self._charging_binary_entity = charging_binary_entity
        self._locked_entity = locked_entity

    def get_snapshot(self, hass: HomeAssistant) -> VehicleSnapshot | None:
        battery_pct = _float_state(hass, self._battery_entity)
        odometer_km = _float_state(hass, self._odometer_entity)

        latitude: float | None = None
        longitude: float | None = None
        if self._device_tracker_entity:
            tracker_state = hass.states.get(self._device_tracker_entity)
            if tracker_state is not None:
                latitude = tracker_state.attributes.get("latitude")
                longitude = tracker_state.attributes.get("longitude")

        is_charging = _bool_state(hass, self._charging_binary_entity, ("on",))
        is_locked = _bool_state(hass, self._locked_entity, ("locked", "on"))

        if battery_pct is None and odometer_km is None and latitude is None:
            # Nothing usable this cycle (e.g. car asleep and entities unavailable).
            return None

        return VehicleSnapshot(
            battery_pct=battery_pct,
            odometer_km=odometer_km,
            latitude=latitude,
            longitude=longitude,
            is_charging=is_charging,
            is_locked=is_locked,
        )
