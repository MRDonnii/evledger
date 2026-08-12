"""Shared data models for EV Ledger."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class VehicleSnapshot:
    """A single point-in-time reading from a vehicle provider."""

    battery_pct: float | None
    odometer_km: float | None
    latitude: float | None
    longitude: float | None
    is_charging: bool | None
    is_locked: bool | None


@dataclass
class LiveChargeState:
    """A single point-in-time reading from a live-power charger provider."""

    is_charging: bool
    power_w: float | None
    session_energy_kwh: float | None
    # A stable post-session energy reading, if the provider exposes one (e.g.
    # Zaptec's "completed session energy" sensor, which holds its value after
    # the session ends rather than resetting immediately). Preferred over a
    # value captured mid-session when available, since it can't go stale.
    completed_session_kwh: float | None = None


@dataclass
class SessionCost:
    """A recent, completed session's cost as reported by a cost-lookup provider."""

    kwh: float
    price: float
    ended_at: str  # ISO 8601


@dataclass
class Trip:
    """One recorded drive."""

    id: str
    started_at: str  # ISO 8601
    ended_at: str | None
    start_odometer_km: float | None
    end_odometer_km: float | None
    distance_km: float | None
    start_lat: float | None
    start_lon: float | None
    end_lat: float | None
    end_lon: float | None
    start_battery_pct: float | None
    end_battery_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trip":
        return cls(**data)


@dataclass
class ChargeSession:
    """One recorded charge, at home or in public."""

    id: str
    location_kind: str  # "home" | "public"
    provider: str  # "zaptec" | "monta" | "manual" | "vehicle"
    started_at: str  # ISO 8601
    ended_at: str | None
    kwh: float | None
    price: float | None
    price_currency: str
    location_name: str | None
    start_battery_pct: float | None
    end_battery_pct: float | None
    needs_review: bool = False
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChargeSession":
        return cls(**data)
