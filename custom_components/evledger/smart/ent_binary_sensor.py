"""Plug-in reminder (on during the 15 minutes before the planned start) and "charge now" signal."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import EvSmartChargeListenerEntity


def build(planner) -> list:
    return [ChargeNow(planner, "charge_now")]


class ChargeNow(EvSmartChargeListenerEntity, BinarySensorEntity):
    """On while the plan wants the car to charge. Usable for own automations without charger control."""

    _attr_icon = "mdi:battery-charging"

    @property
    def is_on(self) -> bool:
        return self.planner.charge_desired
