"""Base entity for EV Smart Charge."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .planner import ChargePlanner


class EvSmartChargeEntity(Entity):
    _attr_has_entity_name = True

    def __init__(self, planner: ChargePlanner, key: str) -> None:
        self.planner = planner
        self._attr_translation_key = key
        self._attr_unique_id = f"{planner.entry.entry_id}_smart_{key}"
        # The vehicle device EV Ledger already created for this car.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, planner.entry.entry_id)})


class EvSmartChargeListenerEntity(EvSmartChargeEntity):
    """Entity that is refreshed whenever the plan is recalculated."""

    _attr_should_poll = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.planner.async_add_listener(self.async_write_ha_state))
