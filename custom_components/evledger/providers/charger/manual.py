"""Manual charger provider — a marker for public/away charging logged by hand.

Manual sessions are written directly to the store by the
`evledger.log_public_charge` service; this class exists only so "manual" is a
recognised, selectable charger provider in the config flow and provider
registry.
"""
from __future__ import annotations

from .base import CAP_COST_LOOKUP, ChargerProvider


class ManualChargerProvider(ChargerProvider):
    """No live entities; presence in the enabled-providers list just unlocks the service."""

    provider_id = "manual"
    capabilities = frozenset({CAP_COST_LOOKUP})
