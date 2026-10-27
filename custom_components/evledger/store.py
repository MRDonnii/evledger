"""Persistent storage for EV Ledger trip and charge records."""
from __future__ import annotations

import uuid

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY_TEMPLATE, STORAGE_VERSION

METER_SAVE_DELAY = 30
from .models import ChargeSession, Trip
from .tyres import TyreChange, TyreSet


class EvLedgerStore:
    """Wraps a Home Assistant Store with typed accessors for trips and charge sessions."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store = Store(
            hass, STORAGE_VERSION, STORAGE_KEY_TEMPLATE.format(entry_id=entry_id)
        )
        self._trips: dict[str, Trip] = {}
        self._charges: dict[str, ChargeSession] = {}
        # The charger's session counter when the open home charge began (Zaptec counts from plug-in to unplug,
        # through pauses), so a charge is not counted again in the next one.
        self._meter: dict = {}
        # Tyre sets, when each went on the car, and the season a change message was sent for.
        self._tyre_sets: dict[str, TyreSet] = {}
        self._tyre_changes: dict[str, TyreChange] = {}
        self.tyre_notified: str | None = None
        self.settings: dict = {}  # the ledger's own settings (public_default_price)

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if data:
            self._trips = {t["id"]: Trip.from_dict(t) for t in data.get("trips", [])}
            self._charges = {c["id"]: ChargeSession.from_dict(c) for c in data.get("charges", [])}
            self._meter = data.get("meter") or {}
            tyres = data.get("tyres") or {}
            self._tyre_sets = {t["id"]: TyreSet.from_dict(t) for t in tyres.get("sets", [])}
            self._tyre_changes = {c["id"]: TyreChange.from_dict(c) for c in tyres.get("changes", [])}
            self.tyre_notified = tyres.get("notified")
            self.settings = dict(data.get("settings") or {})

    def _data(self) -> dict:
        return {
            "trips": [t.to_dict() for t in self._trips.values()],
            "charges": [c.to_dict() for c in self._charges.values()],
            "meter": self._meter,
            "tyres": {"sets": [t.to_dict() for t in self._tyre_sets.values()],
                      "changes": [c.to_dict() for c in self._tyre_changes.values()],
                      "notified": self.tyre_notified},
            "settings": self.settings,
        }

    async def _async_save(self) -> None:
        await self._store.async_save(self._data())

    async def async_set_setting(self, key: str, value) -> None:
        self.settings[key] = value
        await self._async_save()

    def meter(self, charge_id: str) -> dict | None:
        """For the open charge: the charger's session counter when it began ("kwh"), at the last look ("last"), and
        the energy priced so far ("priced") with its price ("cost")."""
        return dict(self._meter) if self._meter.get("charge_id") == charge_id else None

    def meter_start(self, charge_id: str) -> float | None:
        """The charger's session counter when this charge began, if it was read."""
        value = (self.meter(charge_id) or {}).get("kwh")
        return float(value) if isinstance(value, (int, float)) else None

    async def async_set_meter_start(self, charge_id: str, kwh: float | None) -> None:
        self._meter = ({"charge_id": charge_id, "kwh": kwh, "last": kwh, "cost": 0.0, "priced": 0.0}
                       if kwh is not None else {})
        await self._async_save()

    def update_meter(self, charge_id: str, values: dict) -> None:
        """Saved a little later, not at every look while charging."""
        self._meter = {"charge_id": charge_id, **values}
        self._store.async_delay_save(self._data, METER_SAVE_DELAY)

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

    def get_latest_pending_review_charge(self, location_kind: str | None = None) -> ChargeSession | None:
        candidates = [
            c for c in self._charges.values() if c.needs_review and c.ended_at is not None
            and (location_kind is None or c.location_kind == location_kind)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.ended_at or "")

    def get_charge(self, charge_id: str) -> ChargeSession | None:
        """Return one charge session by its stable id."""
        return self._charges.get(charge_id)

    async def async_upsert_trip(self, trip: Trip) -> None:
        self._trips[trip.id] = trip
        await self._async_save()

    async def async_upsert_charge(self, charge: ChargeSession) -> None:
        self._charges[charge.id] = charge
        await self._async_save()

    async def async_delete_trip(self, trip_id: str) -> bool:
        if trip_id not in self._trips:
            return False
        del self._trips[trip_id]
        await self._async_save()
        return True

    async def async_delete_charge(self, charge_id: str) -> bool:
        if charge_id not in self._charges:
            return False
        del self._charges[charge_id]
        await self._async_save()
        return True

    # ------------------------------------------------------------------ tyres

    @property
    def tyre_sets(self) -> list[TyreSet]:
        """In the order they were added."""
        return list(self._tyre_sets.values())

    @property
    def tyre_changes(self) -> list[TyreChange]:
        """Newest first."""
        return sorted(self._tyre_changes.values(), key=lambda c: c.at, reverse=True)

    def get_tyre_set(self, set_id: str) -> TyreSet | None:
        return self._tyre_sets.get(set_id)

    async def async_upsert_tyre_set(self, tyre: TyreSet) -> None:
        self._tyre_sets[tyre.id] = tyre
        await self._async_save()

    async def async_delete_tyre_set(self, set_id: str) -> bool:
        """The set and its changes."""
        if self._tyre_sets.pop(set_id, None) is None:
            return False
        self._tyre_changes = {k: c for k, c in self._tyre_changes.items() if c.set_id != set_id}
        await self._async_save()
        return True

    async def async_upsert_tyre_change(self, change: TyreChange) -> None:
        self._tyre_changes[change.id] = change
        await self._async_save()

    async def async_delete_tyre_change(self, change_id: str) -> bool:
        if self._tyre_changes.pop(change_id, None) is None:
            return False
        await self._async_save()
        return True

    async def async_set_tyre_notified(self, key: str | None) -> None:
        self.tyre_notified = key
        await self._async_save()

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]
