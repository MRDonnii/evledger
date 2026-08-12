"""Zaptec charger provider — live power and session energy, no cost data.

Zaptec's native integration has no concept of price; it only ever answers
"is it charging, at what power, how much energy this session." Actual cost
for a Zaptec-only setup has to come from a cost-lookup provider (Monta) or a
manual entry.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from ...models import LiveChargeState
from .base import CAP_LIVE_POWER, ChargerProvider

CHARGING_POWER_THRESHOLD_W = 300


def _numeric_state_in_watts(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = str(state.attributes.get("unit_of_measurement", "")).lower()
    return value * 1000 if unit == "kw" else value


def _numeric_state(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        return float(state.state)
    except ValueError:
        return None


class ZaptecChargerProvider(ChargerProvider):
    """Live power/session-energy provider backed by the Zaptec integration."""

    provider_id = "zaptec"
    capabilities = frozenset({CAP_LIVE_POWER})

    def __init__(
        self,
        power_entity: str | None,
        session_energy_entity: str | None,
        charging_entity: str | None,
        completed_energy_entity: str | None = None,
    ) -> None:
        self._power_entity = power_entity
        self._session_energy_entity = session_energy_entity
        self._charging_entity = charging_entity
        self._completed_energy_entity = completed_energy_entity

    def get_live_state(self, hass: HomeAssistant) -> LiveChargeState | None:
        power_w = _numeric_state_in_watts(hass, self._power_entity)
        session_energy_kwh = _numeric_state(hass, self._session_energy_entity)
        completed_session_kwh = _numeric_state(hass, self._completed_energy_entity)

        if self._charging_entity:
            state = hass.states.get(self._charging_entity)
            if state is None or state.state in ("unknown", "unavailable", ""):
                return None
            is_charging = state.state == "on"
        elif power_w is not None:
            is_charging = power_w > CHARGING_POWER_THRESHOLD_W
        else:
            return None

        return LiveChargeState(
            is_charging=is_charging,
            power_w=power_w,
            session_energy_kwh=session_energy_kwh,
            completed_session_kwh=completed_session_kwh,
        )
