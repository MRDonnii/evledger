"""Base class for charger providers."""
from __future__ import annotations

from abc import ABC
from datetime import datetime

from homeassistant.core import HomeAssistant

from ...models import LiveChargeState, SessionCost

CAP_LIVE_POWER = "live_power"
CAP_COST_LOOKUP = "cost_lookup"


class ChargerProvider(ABC):
    """A source of charging information for one charger.

    A provider may support either or both capabilities:
      - live_power: can answer "is it charging right now, at what power, how
        many kWh this session so far" (e.g. Zaptec).
      - cost_lookup: can, shortly after a session ends, answer "what did that
        session actually cost" (e.g. Monta, or a manually logged entry).

    Like vehicle providers, a charger provider only reads entities already
    exposed by another integration — it never calls a vendor API directly.
    """

    provider_id: str
    capabilities: frozenset[str] = frozenset()

    def get_live_state(self, hass: HomeAssistant) -> LiveChargeState | None:
        """Return current charging power/session-energy, if this provider supports it."""
        return None

    def get_recent_session_cost(
        self, hass: HomeAssistant, session_end: datetime, max_age_minutes: int = 30
    ) -> SessionCost | None:
        """Return the actual cost of a session that ended near `session_end`, if known."""
        return None
