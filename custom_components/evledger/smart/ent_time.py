"""Clock times: ready by (also at the weekend), the start and end of the fixed charging window, and the evening
check."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import EvSmartChargeEntity

ICONS = {"ready_by_time": "mdi:clock-check-outline", "ready_by_weekend": "mdi:calendar-weekend-outline",
         "fixed_start": "mdi:clock-start", "fixed_end": "mdi:clock-end", "reminder_time": "mdi:bell-ring-outline"}


def build(planner) -> list:
    return list(PlanTime(planner, key) for key in ICONS)


class PlanTime(EvSmartChargeEntity, TimeEntity, RestoreEntity):
    _attr_should_poll = False

    def __init__(self, planner, key: str) -> None:
        super().__init__(planner, key)
        self._attr_icon = ICONS[key]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last:
            try:
                self.planner.async_set_time(self._attr_translation_key, time.fromisoformat(last.state))
            except ValueError:
                pass

    @property
    def native_value(self) -> time:
        return self.planner.times[self._attr_translation_key]

    async def async_set_value(self, value: time) -> None:
        self.planner.async_set_time(self._attr_translation_key, value.replace(second=0, microsecond=0))
        self.async_write_ha_state()
