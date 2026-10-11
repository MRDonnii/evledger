"""Fetching the car's data again when it has none. After a restart of Home Assistant the car integrations (Tesla Fleet,
Tesla Custom, Teslemetry, Tessie) can leave the car's entities unknown, or a "last update" at time 0, until the car
wakes up on its own, which may take hours at night. EV Ledger then asks them for an update: every few minutes in the
first half hour after a start, and now and then while the car is on the charger. An update never wakes the car; an
integration only answers with what the car has when it is awake."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.const import STATE_UNKNOWN
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..device_resolve import all_devices

if TYPE_CHECKING:
    from .planner import ChargePlanner

# Car integrations whose entities are fetched again.
CAR_PLATFORMS = ("tesla_fleet", "tesla_custom", "teslemetry", "tessie")
AFTER_START = timedelta(minutes=30)
AFTER_START_EVERY = timedelta(minutes=3)
PLUGGED_EVERY = timedelta(minutes=15)
# Tries per plug-in while the car is on the charger (then the car may simply be asleep).
PLUGGED_TRIES = 8
# A "last update" this old while the car is online: the integration hangs.
HANGING = timedelta(minutes=30)
DOMAINS = ("sensor", "binary_sensor", "lock", "climate", "number", "switch", "cover", "select", "device_tracker")


class CarRefresh:
    def __init__(self, planner: ChargePlanner) -> None:
        self.planner = planner
        self.last: datetime | None = None
        self.tries = 0
        self.asked = 0  # updates asked for (for the switch's attributes / tests)
        self._ids: list[str] | None = None

    def entities(self) -> list[str]:
        """The car's own entities (the battery sensor's device and devices with the same name) from the car
        integrations, one per integration is enough to fetch them all, but every stale one is asked."""
        if self._ids is None:
            p = self.planner
            registry = er.async_get(p.hass)
            devices = dr.async_get(p.hass)
            battery = registry.async_get(p.battery_entity)
            car = devices.async_get(battery.device_id) if battery is not None and battery.device_id else None
            found: list[str] = []
            if car is not None:
                name = car.name_by_user or car.name
                same_car = [device.id for device in all_devices(devices)
                            if device.id == car.id or (name and (device.name_by_user or device.name) == name)]
                found = [entry.entity_id for device_id in same_car
                         for entry in er.async_entries_for_device(registry, device_id)
                         if entry.platform in CAR_PLATFORMS and entry.domain in DOMAINS and not entry.disabled_by]
            self._ids = found
        return self._ids

    def stale(self, now: datetime | None = None) -> list[str]:
        """The car's entities without data: unknown, or a time stamp of time 0 (1970). While the car says it is
        online, a "last update" more than half an hour old counts too (the integration hangs)."""
        hass = self.planner.hass
        now = now or dt_util.utcnow()
        ids = self.entities()
        online = any(entity_id.endswith("_online") and (state := hass.states.get(entity_id)) is not None
                     and state.state == "on" for entity_id in ids)
        result = []
        for entity_id in ids:
            state = hass.states.get(entity_id)
            if state is None:
                continue
            if state.state == STATE_UNKNOWN:
                result.append(entity_id)
            elif state.attributes.get("device_class") == "timestamp":
                moment = dt_util.parse_datetime(state.state)
                if moment is None:
                    continue
                if moment.year < 2000 or (online and "update" in entity_id and now - moment > HANGING):
                    result.append(entity_id)
        return result

    def check(self, now: datetime, started: datetime | None, plugged: bool) -> None:
        """Called every minute: ask for an update when the car has no data (or hangs) and it is time to."""
        if not plugged:
            self.tries = 0
        after_start = started is not None and now - started < AFTER_START
        every = AFTER_START_EVERY if after_start else PLUGGED_EVERY
        if self.last is not None and now - self.last < every:
            return
        if not after_start and self.tries >= PLUGGED_TRIES:
            return  # reset at the next plug-in (or, unplugged, it was only for a hanging integration)
        stale = self.stale(now)
        if not stale:
            return
        hanging = not any(self.planner.hass.states.get(e).state == STATE_UNKNOWN for e in stale)
        # Without a start or a plug-in only an integration that hangs while the car is online is asked (rarely).
        if not after_start and not plugged and not hanging:
            return
        self.last = now
        if not after_start:
            self.tries += 1
        self.asked += 1
        p = self.planner
        p.entry.async_create_background_task(
            p.hass, p.hass.services.async_call("homeassistant", "update_entity", {"entity_id": stale}),
            "evledger_car_refresh")
