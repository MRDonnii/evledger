"""Plan settings: target SOC, charging power, efficiency, price factor, price cap, trip settings, the plug-in
reminder's battery level and how long before the ready-by time the car's climate starts."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberEntityDescription,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.exceptions import ServiceValidationError

from .entity import EvSmartChargeEntity
from .plan import finite
from .planner import ChargePlanner

NUMBERS: tuple[NumberEntityDescription, ...] = (
    NumberEntityDescription(key="target_soc", icon="mdi:battery-charging-80",
                            native_min_value=10, native_max_value=100, native_step=1,
                            native_unit_of_measurement=PERCENTAGE, mode=NumberMode.SLIDER),
    NumberEntityDescription(key="charge_power_kw", icon="mdi:ev-station",
                            native_min_value=1, native_max_value=22, native_step=0.1,
                            native_unit_of_measurement=UnitOfPower.KILO_WATT,
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="efficiency", icon="mdi:percent-circle-outline",
                            native_min_value=0.5, native_max_value=1, native_step=0.01,
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="price_factor", icon="mdi:multiplication",
                            native_min_value=0, native_max_value=5, native_step=0.01,
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="price_cap", icon="mdi:cash-lock",
                            native_min_value=-5, native_max_value=20, native_step=0.01,
                            mode=NumberMode.BOX),
    NumberEntityDescription(key="smart_max_price", icon="mdi:cash-lock",
                            native_min_value=-5, native_max_value=20, native_step=0.05,
                            mode=NumberMode.BOX),
    NumberEntityDescription(key="ask_min_saving", icon="mdi:piggy-bank-outline",
                            native_min_value=0, native_max_value=1000, native_step=1, mode=NumberMode.BOX),
    NumberEntityDescription(key="ready_margin", icon="mdi:timer-check-outline",
                            native_min_value=0, native_max_value=120, native_step=5,
                            native_unit_of_measurement=UnitOfTime.MINUTES, mode=NumberMode.BOX,
                            entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="smart_floor_soc", icon="mdi:battery-arrow-up-outline",
                            native_min_value=0, native_max_value=100, native_step=5,
                            native_unit_of_measurement=PERCENTAGE, mode=NumberMode.SLIDER),
    NumberEntityDescription(key="min_soc", icon="mdi:battery-alert-variant-outline",
                            native_min_value=0, native_max_value=100, native_step=1,
                            native_unit_of_measurement=PERCENTAGE, mode=NumberMode.SLIDER),
    NumberEntityDescription(key="consumption", icon="mdi:road-variant",
                            native_min_value=50, native_max_value=500, native_step=1,
                            native_unit_of_measurement="Wh/km",
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="trip_margin", icon="mdi:shield-plus-outline",
                            native_min_value=0, native_max_value=100, native_step=1,
                            native_unit_of_measurement=PERCENTAGE,
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="trip_reserve", icon="mdi:battery-lock",
                            native_min_value=0, native_max_value=50, native_step=1,
                            native_unit_of_measurement=PERCENTAGE,
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="reminder_soc", icon="mdi:battery-alert",
                            native_min_value=0, native_max_value=100, native_step=5,
                            native_unit_of_measurement=PERCENTAGE,
                            mode=NumberMode.SLIDER, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="low_price", icon="mdi:cash-check",
                            native_min_value=-5, native_max_value=20, native_step=0.05,
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
    NumberEntityDescription(key="heat_hold_price", icon="mdi:cash-lock",
                            native_min_value=-5, native_max_value=20, native_step=0.05, mode=NumberMode.BOX),
    NumberEntityDescription(key="precondition_minutes", icon="mdi:timer-cog-outline",
                            native_min_value=5, native_max_value=60, native_step=5,
                            native_unit_of_measurement=UnitOfTime.MINUTES,
                            mode=NumberMode.BOX, entity_category=EntityCategory.CONFIG),
)


def build(planner) -> list:
    return list(PlanNumber(planner, description) for description in NUMBERS)


class PlanNumber(EvSmartChargeEntity, RestoreNumber):
    _attr_should_poll = False

    def __init__(self, planner: ChargePlanner, description: NumberEntityDescription) -> None:
        super().__init__(planner, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self.planner.async_set_setting(self.entity_description.key, float(last.native_value))

    @property
    def native_value(self) -> float:
        return self.planner.settings[self.entity_description.key]

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.key in ("price_cap", "low_price", "heat_hold_price", "smart_max_price"):
            return f"{self.planner.price_unit or 'kr'}/kWh"
        if self.entity_description.key == "ask_min_saving":
            return self.planner.price_unit or "kr"
        return self.entity_description.native_unit_of_measurement

    async def async_set_native_value(self, value: float) -> None:
        if (value := finite(value)) is None:
            raise ServiceValidationError("The value is not a number")
        self.planner.async_set_setting(self.entity_description.key, value)
        self.async_write_ha_state()
        if self.entity_description.key == "target_soc" and self.planner.deadline_target is None:
            # The car's own charge limit follows the target (in the background: a sleeping car can take a while).
            self.hass.async_create_background_task(self.planner.async_sync_car_limit(float(value)),
                                                   "evledger_sync_car_limit")
