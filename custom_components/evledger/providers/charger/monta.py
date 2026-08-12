"""Monta charger provider — cost lookup for a recently completed session.

Monta only ever exposes its *latest* charge session's cost; it cannot report
whether the charger is charging right now in a way EV Ledger can rely on for
session-boundary detection. Pair it with a live_power provider (Zaptec) so EV
Ledger knows when a home session starts and ends; Monta then supplies the
actual price for that session, matched by timestamp.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...models import SessionCost
from .base import CAP_COST_LOOKUP, ChargerProvider


class MontaChargerProvider(ChargerProvider):
    """Cost-lookup provider backed by the Monta integration's last-charge sensor."""

    provider_id = "monta"
    capabilities = frozenset({CAP_COST_LOOKUP})

    def __init__(self, last_charge_entity: str | None, wallet_entity: str | None = None) -> None:
        self._last_charge_entity = last_charge_entity
        self._wallet_entity = wallet_entity

    def get_recent_session_cost(
        self, hass: HomeAssistant, session_end: datetime, max_age_minutes: int = 30
    ) -> SessionCost | None:
        if not self._last_charge_entity:
            return None
        state = hass.states.get(self._last_charge_entity)
        if state is None:
            return None

        cost = state.attributes.get("cost")
        kwh = state.attributes.get("consumedKwh") or state.attributes.get("consumed_kwh")
        stopped_at_raw = state.attributes.get("stoppedAt") or state.attributes.get("stopped_at")
        if cost is None or kwh is None or not stopped_at_raw:
            return None

        stopped_at = dt_util.parse_datetime(str(stopped_at_raw))
        if stopped_at is None:
            return None

        delta_minutes = abs(
            (dt_util.as_utc(session_end) - dt_util.as_utc(stopped_at)).total_seconds()
        ) / 60
        if delta_minutes > max_age_minutes:
            return None

        try:
            kwh_f = float(kwh)
            cost_f = float(cost)
        except (TypeError, ValueError):
            return None

        if kwh_f <= 0 and cost_f <= 0:
            return None

        return SessionCost(
            kwh=round(kwh_f, 2),
            price=round(cost_f, 2),
            ended_at=dt_util.as_utc(stopped_at).isoformat(),
        )
