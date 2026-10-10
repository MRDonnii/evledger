"""Buttons: clear the temporary trip plan, and confirm the cheapest plan (answers the phone question)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.util import dt as dt_util

from .entity import EvSmartChargeEntity


def build(planner) -> list:
    return [ClearTrip(planner, "trip_clear"), ConfirmPlan(planner, "confirm_plan"), SendPlan(planner, "send_plan"),
            SendMonth(planner, "send_monthly_summary")]


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


class SendMonth(EvSmartChargeEntity, ButtonEntity):
    """Send this month's charging so far (kWh, price, saving) to the chosen phones now."""

    _attr_icon = "mdi:calendar-month-outline"

    async def async_press(self) -> None:
        month = dt_util.now().strftime("%Y-%m")
        await self.planner.routines.async_send_month(month, so_far=True)
