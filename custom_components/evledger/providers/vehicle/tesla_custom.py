"""Vehicle provider backed by the car integration's entities (Tesla Custom, Tesla Fleet, Teslemetry, Tessie or any
car with a battery, odometer, location and charging sensor)."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from ...car_sources import CarSources
from ...const import (
    CONF_BATTERY_ENTITY,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_OUTSIDE_TEMP_ENTITY,
)
from ...models import VehicleSnapshot
from ...smart.plan import finite
from .base import VehicleProvider


def _float_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    return finite(state.state)


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
        outside_temp_entity: str | None = None,
    ) -> None:
        self._battery_entity = battery_entity
        self._odometer_entity = odometer_entity
        self._device_tracker_entity = device_tracker_entity
        self._charging_binary_entity = charging_binary_entity
        self._locked_entity = locked_entity
        self._outside_temp_entity = outside_temp_entity
        # The same readings from the car's other integrations; the one that reported last is used (see car_sources)
        self.sources = CarSources({
            CONF_BATTERY_ENTITY: battery_entity, CONF_CHARGING_BINARY_ENTITY: charging_binary_entity,
            CONF_DEVICE_TRACKER_ENTITY: device_tracker_entity, CONF_LOCKED_ENTITY: locked_entity,
            CONF_OUTSIDE_TEMP_ENTITY: outside_temp_entity,
        })

    def get_snapshot(self, hass: HomeAssistant) -> VehicleSnapshot | None:
        fresh = self.sources.freshest
        battery_pct = _float_state(hass, fresh(hass, CONF_BATTERY_ENTITY, lambda st: finite(st.state) is not None))
        odometer_km = _float_state(hass, self._odometer_entity)
        outside_temp_c = _float_state(hass, fresh(hass, CONF_OUTSIDE_TEMP_ENTITY))

        latitude: float | None = None
        longitude: float | None = None
        tracker = fresh(hass, CONF_DEVICE_TRACKER_ENTITY, lambda st: finite(st.attributes.get("latitude")) is not None
                        and finite(st.attributes.get("longitude")) is not None)
        if tracker:
            tracker_state = hass.states.get(tracker)
            if tracker_state is not None:
                latitude = finite(tracker_state.attributes.get("latitude"))
                longitude = finite(tracker_state.attributes.get("longitude"))
                if latitude is None or longitude is None:
                    latitude = longitude = None

        # A binary sensor (Tesla Custom) or a charging-state sensor (Tesla Fleet, Teslemetry: "charging").
        is_charging = _bool_state(hass, fresh(hass, CONF_CHARGING_BINARY_ENTITY), ("on", "charging"))
        is_locked = _bool_state(hass, fresh(hass, CONF_LOCKED_ENTITY), ("locked", "on"))

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
            outside_temp_c=outside_temp_c,
        )
