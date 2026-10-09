"""Charger backends: how a charger's state is read and how it is started and stopped."""

from __future__ import annotations

import logging

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import CHARGER_SWITCH, CHARGER_ZAPTEC, CONF_CHARGE_SWITCH, CONF_CHARGER_TYPE, CONF_ZAPTEC_MODE_ENTITY
from .control import Action, ChargerState

_LOGGER = logging.getLogger(__name__)

ZAPTEC_STATES = {
    "disconnected": ChargerState.DISCONNECTED,
    "connected_requesting": ChargerState.READY,
    "connected_charging": ChargerState.CHARGING,
    "connected_finished": ChargerState.PAUSED,
}
# Unique id suffixes the Zaptec integration (custom-components/zaptec) gives the charger's entities.
ZAPTEC_SWITCH_SUFFIX = "_charger_operation_mode"
ZAPTEC_AUTHORIZE_SUFFIX = "_authorize_charge"


class ChargerBackend:
    """Base class. entities lists everything whose state changes should trigger a new decision."""

    entities: list[str]

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def state(self) -> ChargerState:
        raise NotImplementedError

    async def async_command(self, action: Action, state: ChargerState) -> None:
        raise NotImplementedError

    async def _call(self, domain: str, service: str, entity_id: str) -> None:
        try:
            await self.hass.services.async_call(domain, service, {"entity_id": entity_id}, blocking=True)
        except HomeAssistantError as err:
            _LOGGER.warning("%s.%s on %s failed: %s", domain, service, entity_id, err)


class ZaptecBackend(ChargerBackend):
    """Zaptec Go/Pro via the Zaptec integration. Needs the charger's "Charger mode" sensor; the
    charging switch and the authorise button are found on the same device."""

    def __init__(self, hass: HomeAssistant, mode_entity: str, switch: str, authorize: str | None) -> None:
        super().__init__(hass)
        self.mode_entity, self.switch, self.authorize = mode_entity, switch, authorize
        self.entities = [mode_entity, switch]

    def state(self) -> ChargerState:
        state = self.hass.states.get(self.mode_entity)
        return ZAPTEC_STATES.get(state.state, ChargerState.UNKNOWN) if state else ChargerState.UNKNOWN

    async def async_command(self, action: Action, state: ChargerState) -> None:
        if action == Action.STOP:
            await self._call("switch", "turn_off", self.switch)
        elif state == ChargerState.READY and self.authorize:
            await self._call("button", "press", self.authorize)
        else:
            await self._call("switch", "turn_on", self.switch)


class SwitchBackend(ChargerBackend):
    """Any charger with a switch that starts and stops charging (OCPP, Monta, Easee, ...)."""

    def __init__(self, hass: HomeAssistant, switch: str, plugged: str | None) -> None:
        super().__init__(hass)
        self.switch, self.plugged = switch, plugged
        self.entities = [switch] + ([plugged] if plugged else [])

    def state(self) -> ChargerState:
        switch = self.hass.states.get(self.switch)
        if switch is None or switch.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return ChargerState.UNKNOWN
        if self.plugged and (plug := self.hass.states.get(self.plugged)) and plug.state == STATE_OFF:
            return ChargerState.DISCONNECTED
        return ChargerState.CHARGING if switch.state == STATE_ON else ChargerState.PAUSED

    async def async_command(self, action: Action, state: ChargerState) -> None:
        await self._call("switch", "turn_off" if action == Action.STOP else "turn_on", self.switch)


def zaptec_siblings(hass: HomeAssistant, mode_entity: str) -> tuple[str | None, str | None]:
    """The charging switch and authorise button on the same Zaptec device as the mode sensor."""
    registry = er.async_get(hass)
    entry = registry.async_get(mode_entity)
    if entry is None or entry.device_id is None:
        return None, None
    switch = authorize = None
    for sibling in er.async_entries_for_device(registry, entry.device_id):
        if sibling.domain == "switch" and sibling.unique_id.endswith(ZAPTEC_SWITCH_SUFFIX):
            switch = sibling.entity_id
        elif sibling.domain == "button" and sibling.unique_id.endswith(ZAPTEC_AUTHORIZE_SUFFIX):
            authorize = sibling.entity_id
    return switch, authorize


def create_backend(hass: HomeAssistant, options: dict, plugged: str | None) -> ChargerBackend | None:
    kind = options.get(CONF_CHARGER_TYPE)
    if kind == CHARGER_ZAPTEC and (mode_entity := options.get(CONF_ZAPTEC_MODE_ENTITY)):
        switch, authorize = zaptec_siblings(hass, mode_entity)
        if switch:
            return ZaptecBackend(hass, mode_entity, switch, authorize)
        _LOGGER.warning("No Zaptec charging switch found next to %s; charger control is off", mode_entity)
    elif kind == CHARGER_SWITCH and (switch := options.get(CONF_CHARGE_SWITCH)):
        return SwitchBackend(hass, switch, plugged)
    return None
