"""Switches: round trip for the temporary trip, and confirming new plans on the phone."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .entity import EvSmartChargeListenerEntity


def build(planner) -> list:
    return list([TripRoundTrip(planner, "trip_round_trip"),
                        ConfirmOnPhone(planner, "confirm_on_phone"),
                        NotifyPlan(planner, "notify_plan")])


class TripRoundTrip(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    _attr_icon = "mdi:swap-horizontal"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self.planner.trip.round_trip = last.state == "on"

    @property
    def is_on(self) -> bool:
        return self.planner.trip.round_trip

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_trip_round_trip(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_trip_round_trip(False)


class ConfirmOnPhone(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    """When on, a plugged-in car waits for an answer on the chosen phones before charging."""

    _attr_icon = "mdi:cellphone-check"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state == "on":
            self.planner.confirm_enabled = True
            # A question still open before a restart stays open.
            if since := dt_util.parse_datetime(str(last.attributes.get("awaiting_since") or "")):
                self.planner.awaiting_since = since

    @property
    def is_on(self) -> bool:
        return self.planner.confirm_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "awaiting_since": self.planner.awaiting_since.isoformat() if self.planner.awaiting_since else None,
            "phones": self.planner.notify.targets,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_confirm(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_confirm(False)


class NotifyPlan(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    """When on, the phones are told which plan is active, with times and price (and Charge now / Pause)."""

    _attr_icon = "mdi:cellphone-message"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self.planner.info_enabled = last.state == "on"

    @property
    def is_on(self) -> bool:
        return self.planner.info_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_info(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_info(False)
