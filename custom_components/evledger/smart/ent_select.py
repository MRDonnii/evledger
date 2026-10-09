"""Charge mode (how the charger is controlled now) and the default plan."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .entity import EvSmartChargeListenerEntity
from .plan import DEFAULT_MODES, MODES


def build(planner) -> list:
    return [ChargeModeSelect(planner, "charge_mode"), DefaultModeSelect(planner, "default_charge_mode")]


class ChargeModeSelect(EvSmartChargeListenerEntity, SelectEntity, RestoreEntity):
    _attr_icon = "mdi:ev-station"
    _attr_options = list(MODES)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in MODES:
            before = last.attributes.get("mode_before_now")
            if before in MODES:
                self.planner.mode_before_now = before
            self.planner.now_seen_connected = bool(last.attributes.get("now_seen_connected"))
            blocks = []
            for item in last.attributes.get("planned") or []:
                try:
                    start, end = (dt_util.parse_datetime(str(value)) for value in item)
                except (TypeError, ValueError):
                    continue
                if start and end:
                    blocks.append((start, end))
            self.planner.restored_blocks = blocks
            warned = last.attributes.get("warned")
            if isinstance(warned, list):
                self.planner.warned = {str(item) for item in warned}
            last_soc = last.attributes.get("last_soc")
            if isinstance(last_soc, (int, float)):
                self.planner.last_soc = float(last_soc)
            self.planner.async_set_mode(last.state, restore=True)

    @property
    def current_option(self) -> str:
        return self.planner.mode

    @property
    def extra_state_attributes(self) -> dict:
        return {"default_mode": self.planner.default_mode,
                "mode_before_now": self.planner.mode_before_now,
                "now_seen_connected": self.planner.now_seen_connected,
                "last_soc": self.planner.last_soc,
                "car_limit": self.planner.car_limit(),
                "warned": sorted(self.planner.warned),
                # The plan's charging periods, followed after a restart until the prices are back.
                "planned": self._planned()}

    def _planned(self) -> list[list[str]]:
        planner = self.planner
        blocks = ([(block.start, block.end) for block in planner.schedule.blocks] if planner.slot_count
                  else planner.restored_blocks)
        return [[start.isoformat(), end.isoformat()] for start, end in blocks]

    async def async_select_option(self, option: str) -> None:
        self.planner.async_set_mode(option)


class DefaultModeSelect(EvSmartChargeListenerEntity, SelectEntity, RestoreEntity):
    """The plan used when a car is plugged in, and returned to after another plan has run."""

    _attr_icon = "mdi:calendar-star"
    _attr_options = list(DEFAULT_MODES)
    _attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in DEFAULT_MODES:
            self.planner.async_set_default_mode(last.state, restore=True)

    @property
    def current_option(self) -> str:
        return self.planner.default_mode

    async def async_select_option(self, option: str) -> None:
        self.planner.async_set_default_mode(option)
