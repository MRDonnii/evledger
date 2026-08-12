"""Spot-price charger provider — estimates home charging cost from kWh × price.

For anyone without a billing platform like Monta, this is the fallback that
makes home charging cost tracking possible at all: take the session's energy
(from a live_power provider such as Zaptec) and multiply by whatever your
electricity-price sensor reports *right now*. Works the same way regardless
of which price integration you use — Nordpool, Energi Data Service,
Strømligning, or anything else that exposes a plain "current price" sensor.

This is an estimate, not a bill: it uses one price point (at session end)
rather than a time-weighted average across the whole session, and it assumes
your price sensor's unit is already your configured currency per kWh (if
yours reports in øre or cents, its state needs converting — e.g. with a
template sensor — before pointing EV Ledger at it). When both Monta and spot
price are configured, Monta's actual billed cost is always preferred.
"""
from __future__ import annotations

from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from ...models import SessionCost
from .base import CAP_COST_LOOKUP, ChargerProvider


class SpotPriceChargerProvider(ChargerProvider):
    """Cost-lookup provider that estimates price from kWh × a price sensor's current state."""

    provider_id = "spot_price"
    capabilities = frozenset({CAP_COST_LOOKUP})

    def __init__(self, price_entity: str | None) -> None:
        self._price_entity = price_entity

    def get_recent_session_cost(
        self,
        hass: HomeAssistant,
        session_end: datetime,
        max_age_minutes: int = 30,
        known_kwh: float | None = None,
    ) -> SessionCost | None:
        if not self._price_entity or not known_kwh or known_kwh <= 0:
            return None

        state = hass.states.get(self._price_entity)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None

        try:
            price_per_kwh = float(state.state)
        except ValueError:
            return None

        return SessionCost(
            kwh=round(known_kwh, 2),
            price=round(known_kwh * price_per_kwh, 2),
            ended_at=dt_util.as_utc(session_end).isoformat(),
        )
