"""Base class for vehicle providers."""
from __future__ import annotations

from abc import ABC, abstractmethod

from homeassistant.core import HomeAssistant

from ...models import VehicleSnapshot


class VehicleProvider(ABC):
    """Reads a vehicle's current state from entities already present in Home Assistant.

    A vehicle provider never talks to a manufacturer API itself — it only reads
    entities exposed by another, already-installed integration (e.g. tesla_custom).
    This keeps EV Ledger dependency-free and lets it work with whatever vehicle
    integration a user already has.
    """

    provider_id: str

    @abstractmethod
    def get_snapshot(self, hass: HomeAssistant) -> VehicleSnapshot | None:
        """Return the vehicle's current state, or None if its entities are unavailable."""
