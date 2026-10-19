"""The "charge now" signal and whether the car is plugged in."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import EvSmartChargeListenerEntity


def build(planner) -> list:
    return [ChargeNow(planner, "charge_now"), PluggedIn(planner, "plugged_in")]


class ChargeNow(EvSmartChargeListenerEntity, BinarySensorEntity):
    """On while the plan wants the car to charge. Usable for own automations without charger control."""

    _attr_icon = "mdi:battery-charging"

    @property
    def is_on(self) -> bool:
        return self.planner.charge_desired


class PluggedIn(EvSmartChargeListenerEntity, BinarySensorEntity):
    """Whether this car is plugged in, the way the plan sees it: the charger (and that it is this car on it) where EV
    Ledger has one, else the car's own plug sensors – any of them on. One plug state to show on dashboards and use in
    automations (19 Oct 2026: Tesla Custom's charger sensor stayed "off" for a day while the car was plugged in, and the
    cards that read it said "Ikke sat til")."""

    _attr_device_class = BinarySensorDeviceClass.PLUG

    @property
    def is_on(self) -> bool | None:
        return self.planner.car_plugged()

    @property
    def extra_state_attributes(self) -> dict:
        car, since = self.planner.car_plug_state()
        return {
            "charger": self.planner.charger_state.value if self.planner.backend is not None else None,
            "car_says_plugged": car,
            "car_plugged_since": since.isoformat() if since else None,
            "car_sensors": list(dict.fromkeys([*([self.planner.plugged_entity] if self.planner.plugged_entity else []),
                                              *self.planner._plug_entities()])),
        }
