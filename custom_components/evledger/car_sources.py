"""The car's other integrations as sources (Claude AI, 2026-10-27).

The ledger is set up with one integration's entities for the car. On 27 Oct 2026 Tesla Custom stopped updating at a
Supercharger: it kept saying "charging" (and the position there) for over an hour after the charge ended and the car
had driven off, while Tesla Fleet already said "disconnected". So for the charging state, the battery, the position,
the lock and the outside temperature the ledger reads every one of the car's integrations (devices with the car's
name from Tesla Custom, Tesla Fleet, Teslemetry or Tessie) and uses the one that reported last. Not the odometer:
two integrations' counters differ a little, and switching between them would make trips out of nothing.

Tesla BLE (30 Oct 2026) is one more: the car over local Bluetooth through an ESPHome proxy at home. It reports every
10–60 s while the car is within range, so at home it is the one that reported last; out of range its readings go
unavailable and the cloud integrations take over by themselves. Its device is found by the VIN, not the name."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_ENTITY,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_OUTSIDE_TEMP_ENTITY,
)
from .device_resolve import TESLA_INTEGRATIONS, resolve_tesla_vehicle_entities, same_car as same_car_devices

FRESHEST_ROLES = (CONF_BATTERY_ENTITY, CONF_CHARGING_BINARY_ENTITY, CONF_DEVICE_TRACKER_ENTITY, CONF_LOCKED_ENTITY,
                  CONF_OUTSIDE_TEMP_ENTITY)
REFRESH = timedelta(minutes=10)
NO_DATA = ("unknown", "unavailable", "")


def reported(state: State) -> datetime:
    """When the integration last reported this entity (also without a change), else when it last changed."""
    return getattr(state, "last_reported", None) or state.last_updated


class CarSources:
    """The configured entity per role, and the same role on the car's other integrations."""

    def __init__(self, configured: dict[str, str | None]) -> None:
        self.configured = configured
        self._others: dict[str, list[str]] = {}
        self._devices: list[str] = []
        self._found_at: datetime | None = None

    def _find(self, hass: HomeAssistant) -> None:
        now = dt_util.utcnow()
        if self._found_at is not None and now - self._found_at < REFRESH:
            return
        self._found_at = now
        registry, devices = er.async_get(hass), dr.async_get(hass)
        anchor = next((registry.async_get(entity_id) for entity_id in (self.configured.get(CONF_BATTERY_ENTITY),
                                                                        self.configured.get(CONF_CHARGING_BINARY_ENTITY))
                       if entity_id and registry.async_get(entity_id) is not None), None)
        car = devices.async_get(anchor.device_id) if anchor is not None and anchor.device_id else None
        if car is None:
            self._others, self._devices = {}, []
            return
        same_car = same_car_devices(devices, car)
        # Only the car integrations' devices (EV Ledger's own device has the car's name too)
        self._devices = [device_id for device_id in same_car
                         if any(entry.platform in TESLA_INTEGRATIONS
                                for entry in er.async_entries_for_device(registry, device_id))]
        others: dict[str, list[str]] = {role: [] for role in FRESHEST_ROLES}
        for device_id in self._devices:
            if device_id == car.id:
                continue
            resolved = resolve_tesla_vehicle_entities(hass, device_id)
            for role in FRESHEST_ROLES:
                entity_id = resolved.get(role)
                entry = registry.async_get(entity_id) if entity_id else None
                if entry is not None and entry.platform in TESLA_INTEGRATIONS and entity_id not in others[role]:
                    others[role].append(entity_id)
        self._others = others

    def freshest(self, hass: HomeAssistant, role: str, usable: Callable[[State], bool] | None = None) -> str | None:
        """The entity for the role that reported last, of those with data; the configured one when none has."""
        configured = self.configured.get(role)
        if role not in FRESHEST_ROLES:
            return configured
        self._find(hass)
        best: tuple[datetime, str] | None = None
        for entity_id in [configured, *self._others.get(role, [])]:
            state = hass.states.get(entity_id) if entity_id else None
            if state is None or state.state in NO_DATA or (usable is not None and not usable(state)):
                continue
            if best is None or reported(state) > best[0]:
                best = (reported(state), entity_id)
        return best[1] if best else configured

    def freshest_matching(self, hass: HomeAssistant, match: Callable[[er.RegistryEntry], bool]) -> str | None:
        """Of all the car integrations' entities that match, the one that reported last (with data)."""
        self._find(hass)
        registry = er.async_get(hass)
        best: tuple[datetime, str] | None = None
        for device_id in self._devices:
            for entry in er.async_entries_for_device(registry, device_id):
                if entry.platform not in TESLA_INTEGRATIONS or entry.disabled_by or not match(entry):
                    continue
                state = hass.states.get(entry.entity_id)
                if state is None or state.state in NO_DATA:
                    continue
                if best is None or reported(state) > best[0]:
                    best = (reported(state), entry.entity_id)
        return best[1] if best else None
