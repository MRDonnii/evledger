"""Persistent storage for EV Ledger trip and charge records."""
from __future__ import annotations

import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_TEMPLATE, STORAGE_VERSION
from .models import ChargeSession, Trip


class EvLedgerStore:
    """Wraps a Home Assistant Store with typed accessors for trips and charge sessions."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_TEMPLATE.format(entry_id=entry_id)
        )
        self._trips: dict[str, Trip] = {}
        self._charges: dict[str, ChargeSession] = {}

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._trips = {t["id"]: Trip.from_dict(t) for t in data.get("trips", [])}
            self._charges = {c["id"]: ChargeSession.from_dict(c) for c in data.get("charges", [])}

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "trips": [t.to_dict() for t in self._trips.values()],
                "charges": [c.to_dict() for c in self._charges.values()],
            }
        )

    @property
    def trips(self) -> list[Trip]:
        return sorted(self._trips.values(), key=lambda t: t.started_at, reverse=True)

    @property
    def charges(self) -> list[ChargeSession]:
        return sorted(self._charges.values(), key=lambda c: c.started_at, reverse=True)

    def get_open_trip(self) -> Trip | None:
        for trip in self._trips.values():
            if trip.ended_at is None:
                return trip
        return None

    def get_open_charge(self, location_kind: str | None = None) -> ChargeSession | None:
        for charge in self._charges.values():
            if charge.ended_at is None and (
                location_kind is None or charge.location_kind == location_kind
            ):
                return charge
        return None

    def get_latest_pending_review_charge(self) -> ChargeSession | None:
        candidates = [
            c for c in self._charges.values() if c.needs_review and c.ended_at is not None
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.ended_at or "")

    async def async_upsert_trip(self, trip: Trip) -> None:
        self._trips[trip.id] = trip
        await self._async_save()

    async def async_upsert_charge(self, charge: ChargeSession) -> None:
        self._charges[charge.id] = charge
        await self._async_save()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]
