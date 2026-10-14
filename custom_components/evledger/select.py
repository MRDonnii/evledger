"""Select entities: the tyre set on the car, and smart charging's selects (only when smart charging is on)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EvLedgerCoordinator
from .smart.ent_select import build


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: EvLedgerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SelectEntity] = [TyreSetSelect(coordinator, entry)]
    planner = getattr(coordinator, "smart", None)
    if planner is not None:
        entities.extend(build(planner))
    async_add_entities(entities)


class TyreSetSelect(CoordinatorEntity[EvLedgerCoordinator], SelectEntity):
    """The tyre set on the car. Choosing another one records a change now, at the odometer now."""

    _attr_has_entity_name = True
    _attr_translation_key = "tyre_set"
    _attr_icon = "mdi:tire"

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_tyre_set"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def available(self) -> bool:
        return super().available and bool(self.coordinator.store.tyre_sets)

    @property
    def options(self) -> list[str]:
        active = self.coordinator.active_tyre_set()
        return [t.name for t in self.coordinator.store.tyre_sets if not t.retired or t is active]

    @property
    def current_option(self) -> str | None:
        active = self.coordinator.active_tyre_set()
        return active.name if active else None

    async def async_select_option(self, option: str) -> None:
        tyre = self.coordinator.find_tyre_set(option)
        if tyre is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_tyre_set",
                                         translation_placeholders={"set": option})
        if tyre is self.coordinator.active_tyre_set():
            return
        await self.coordinator.async_change_tyres(tyre.id)
