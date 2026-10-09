"""Departure time of a temporary trip plan."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import EvSmartChargeListenerEntity

MAX_AHEAD = timedelta(days=14)


def build(planner) -> list:
    return list([TripDeparture(planner, "trip_departure")])


class TripDeparture(EvSmartChargeListenerEntity, DateTimeEntity, RestoreEntity):
    _attr_icon = "mdi:calendar-arrow-right"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        # Calendar trips cleared by hand stay cleared after a restart (only those still ahead are kept).
        if last and isinstance(last.attributes.get("dismissed"), list):
            self.planner.routines.dismissed = {str(key) for key in last.attributes["dismissed"]}
        if last and (value := dt_util.parse_datetime(last.state)) and value > dt_util.now():
            self.planner.async_set_trip_departure(value, manual=False)
            if last.attributes.get("source") == "calendar":
                trip = self.planner.trip
                trip.source, trip.event_key = "calendar", str(last.attributes.get("event_key") or "")
                trip.event_start = dt_util.parse_datetime(str(last.attributes.get("event_start") or ""))

    @property
    def native_value(self) -> datetime | None:
        return self.planner.trip.departure

    @property
    def extra_state_attributes(self) -> dict:
        trip = self.planner.trip
        now = dt_util.now()
        # Only events still ahead matter; the key starts with the event's start time.
        dismissed = sorted(key for key in self.planner.routines.dismissed
                           if (start := dt_util.parse_datetime(key.split("|", 1)[0])) is None or start > now)
        return {"source": trip.source or None, "event_key": trip.event_key or None,
                "event_start": trip.event_start.isoformat() if trip.event_start else None,
                "dismissed": dismissed[-20:]}

    async def async_set_value(self, value: datetime) -> None:
        now = dt_util.now()
        if value <= now or value > now + MAX_AHEAD:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="departure_out_of_range")
        self.planner.async_set_trip_departure(value.replace(second=0, microsecond=0))
