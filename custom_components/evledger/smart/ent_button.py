"""Buttons: clear the temporary trip plan, and confirm the cheapest plan (answers the phone question)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import EvSmartChargeEntity
from .plan import MODE_SMART


def build(planner) -> list:
    return list([ClearTrip(planner, "trip_clear"), ConfirmPlan(planner, "confirm_plan")])


class ClearTrip(EvSmartChargeEntity, ButtonEntity):
    _attr_icon = "mdi:map-marker-remove-outline"

    async def async_press(self) -> None:
        self.planner.async_clear_trip()


class ConfirmPlan(EvSmartChargeEntity, ButtonEntity):
    _attr_icon = "mdi:check-circle-outline"

    async def async_press(self) -> None:
        self.planner.async_answer(MODE_SMART)
