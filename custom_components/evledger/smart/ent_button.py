"""Buttons: clear the temporary trip plan, and confirm the cheapest plan (answers the phone question)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .entity import EvSmartChargeEntity


def build(planner) -> list:
    return [ClearTrip(planner, "trip_clear"), ConfirmPlan(planner, "confirm_plan"), SendPlan(planner, "send_plan")]


class ClearTrip(EvSmartChargeEntity, ButtonEntity):
    _attr_icon = "mdi:map-marker-remove-outline"

    async def async_press(self) -> None:
        self.planner.async_clear_trip()


class ConfirmPlan(EvSmartChargeEntity, ButtonEntity):
    _attr_icon = "mdi:check-circle-outline"

    async def async_press(self) -> None:
        self.planner.async_answer(self.planner.mode)  # the plan waiting for the answer, e.g. the default


class SendPlan(EvSmartChargeEntity, ButtonEntity):
    """Send the active plan (times, price, Charge now / Pause) to the chosen phones now."""

    _attr_icon = "mdi:cellphone-arrow-down"

    async def async_press(self) -> None:
        await self.planner.notify.async_send_info(self.planner)
