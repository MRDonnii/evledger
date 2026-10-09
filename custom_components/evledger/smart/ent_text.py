"""Destination of a temporary trip plan: an address, "lat,lon" or a zone entity id."""

from __future__ import annotations

from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import EvSmartChargeListenerEntity
from .trip import Route

ROUTE_FIELDS = ("distance_km", "duration_min", "name", "latitude", "longitude", "method")


def build(planner) -> list:
    return list([TripDestination(planner, "trip_destination")])


class TripDestination(EvSmartChargeListenerEntity, TextEntity, RestoreEntity):
    _attr_icon = "mdi:map-marker-path"
    _attr_native_min = 0
    _attr_native_max = 255

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable", ""):
            # Reuse the route found before the restart; a lookup now may fail while the network starts.
            route = None
            if last.attributes.get("distance_km") is not None:
                try:
                    route = Route(**{field: last.attributes.get(field) for field in ROUTE_FIELDS})
                except TypeError:
                    route = None
            self.planner.async_set_trip_destination(last.state, route)

    @property
    def native_value(self) -> str:
        return self.planner.trip.destination

    async def async_set_value(self, value: str) -> None:
        self.planner.async_set_trip_destination(value)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        route = self.planner.trip.route
        return {field: getattr(route, field) if route else None for field in ROUTE_FIELDS}
