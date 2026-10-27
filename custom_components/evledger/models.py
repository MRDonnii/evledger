"""Shared data models for EV Ledger."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any


def _known(cls, data: dict[str, Any]) -> dict[str, Any]:
    """The saved fields this version knows: a field added by a newer version must not stop the ledger loading."""
    names = {field.name for field in fields(cls)}
    return {key: value for key, value in data.items() if key in names}


@dataclass
class VehicleSnapshot:
    """A single point-in-time reading from a vehicle provider."""

    battery_pct: float | None
    odometer_km: float | None
    latitude: float | None
    longitude: float | None
    is_charging: bool | None
    is_locked: bool | None
    outside_temp_c: float | None = None


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
    start_outside_temp_c: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Trip":
        return cls(**_known(cls, data))


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
    # Where the price came from: "place" = a place named before, "zone" = the zone's price, "operator" = the operator's
    # price, "default" = the public default price per kWh (all replaced by any price given later), "manual" = typed in;
    # None for the rest (home charges, older records)
    price_source: str | None = None
    # The Home Assistant zone a public charge happened in (its own price per kWh may apply), if any
    zone: str | None = None
    # Where a public charge happened (to remember the place when it is named), and the station's operator if known
    latitude: float | None = None
    longitude: float | None = None
    operator: str | None = None
    station_kw: float | None = None  # the station's highest power: a fast charger (FAST_KW and up) may cost more

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChargeSession":
        return cls(**_known(cls, data))
