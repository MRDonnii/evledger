"""Sensor platform for EV Ledger."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_MODEL_LABEL,
    CONF_RATED_WH_PER_KM,
    DOMAIN,
    TEMP_BUCKET_COLD_MAX_C,
    TEMP_BUCKET_MILD_MAX_C,
)
from .coordinator import EvLedgerCoordinator
from .models import ChargeSession, Trip

MAX_LIST_ITEMS = 20
MAX_MONTHS = 24


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up EV Ledger sensors for one vehicle entry."""
    coordinator: EvLedgerCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        EvLedgerTripsSensor(coordinator, entry),
        EvLedgerChargesSensor(coordinator, entry),
        EvLedgerChargingStatusSensor(coordinator, entry),
        EvLedgerCostPerKmSensor(coordinator, entry),
        EvLedgerPendingReviewSensor(coordinator, entry),
        EvLedgerTotalCostSensor(coordinator, entry),
        EvLedgerTotalDistanceSensor(coordinator, entry),
        EvLedgerLastTripSensor(coordinator, entry),
        EvLedgerLastChargeSensor(coordinator, entry),
    ]
    if entry.data.get(CONF_RATED_WH_PER_KM) and entry.data.get(CONF_BATTERY_CAPACITY_KWH):
        entities.append(EvLedgerEfficiencySensor(coordinator, entry))
        entities.append(EvLedgerMonthlyPerformanceSensor(coordinator, entry))
    async_add_entities(entities)


class _EvLedgerBaseSensor(CoordinatorEntity[EvLedgerCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EvLedgerCoordinator, entry: ConfigEntry, key: str, name: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=coordinator.vehicle_name,
            manufacturer="EV Ledger",
            model="Vehicle ledger",
        )


class EvLedgerTripsSensor(_EvLedgerBaseSensor):
    """Total trip count, with recent trips and total distance as attributes."""

    _attr_icon = "mdi:map-marker-distance"

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "trips", "Trips")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.get("trips", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        trips: list[Trip] = self.coordinator.data.get("trips", [])
        total_km = sum(t.distance_km or 0 for t in trips)
        return {
            "total_distance_km": round(total_km, 1),
            "trips": [t.to_dict() for t in trips[:MAX_LIST_ITEMS]],
        }


class EvLedgerChargesSensor(_EvLedgerBaseSensor):
    """Total charge count, with home/public cost rollups and recent sessions as attributes."""

    _attr_icon = "mdi:battery-charging-100"

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "charges", "Charges")

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.get("charges", []))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        charges: list[ChargeSession] = self.coordinator.data.get("charges", [])
        finished = [c for c in charges if c.ended_at is not None]
        home = [c for c in finished if c.location_kind == "home"]
        public = [c for c in finished if c.location_kind == "public"]

        def _sum(items: list[ChargeSession], field: str) -> float:
            return round(sum(getattr(c, field) or 0 for c in items), 2)

        return {
            "total_kwh": _sum(finished, "kwh"),
            "total_price": _sum(finished, "price"),
            "home_kwh": _sum(home, "kwh"),
            "home_price": _sum(home, "price"),
            "public_kwh": _sum(public, "kwh"),
            "public_price": _sum(public, "price"),
            "currency": self.coordinator.currency,
            "charges": [c.to_dict() for c in charges[:MAX_LIST_ITEMS]],
        }


class EvLedgerChargingStatusSensor(_EvLedgerBaseSensor):
    """Whether the vehicle is currently charging, and where."""

    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "charging_status", "Charging status")

    @property
    def native_value(self) -> str:
        if self.coordinator.data.get("open_charge_home") is not None:
            return "home"
        if self.coordinator.data.get("open_charge_public") is not None:
            return "public"
        return "idle"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        open_charge: ChargeSession | None = self.coordinator.data.get(
            "open_charge_home"
        ) or self.coordinator.data.get("open_charge_public")
        if open_charge is None:
            return {}
        return open_charge.to_dict()


class EvLedgerCostPerKmSensor(_EvLedgerBaseSensor):
    """All-time average charging cost per kilometre driven."""

    _attr_icon = "mdi:cash-multiple"
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "cost_per_km", "Cost per km")

    @property
    def native_value(self) -> float | None:
        trips: list[Trip] = self.coordinator.data.get("trips", [])
        charges: list[ChargeSession] = self.coordinator.data.get("charges", [])
        total_km = sum(t.distance_km or 0 for t in trips)
        total_price = sum(c.price or 0 for c in charges if c.ended_at is not None)
        if total_km <= 0:
            return None
        return round(total_price / total_km, 2)

    @property
    def native_unit_of_measurement(self) -> str:
        return f"{self.coordinator.currency}/km"


class EvLedgerPendingReviewSensor(_EvLedgerBaseSensor):
    """Charge sessions still missing a price — usually public charges awaiting manual entry."""

    _attr_icon = "mdi:comment-question-outline"

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "pending_review", "Charges needing price")

    @property
    def native_value(self) -> int:
        charges: list[ChargeSession] = self.coordinator.data.get("charges", [])
        return sum(1 for c in charges if c.needs_review)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        charges: list[ChargeSession] = self.coordinator.data.get("charges", [])
        pending = [c for c in charges if c.needs_review]
        return {"pending": [c.to_dict() for c in pending[:MAX_LIST_ITEMS]]}


class EvLedgerTotalCostSensor(_EvLedgerBaseSensor):
    """Running total spent on charging — a proper monetary sensor with `state_class: total`,
    so it shows up natively in HA's own Statistics graphs and month-over-month views."""

    _attr_icon = "mdi:cash-multiple"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "total_cost", "Total charging cost")

    @property
    def native_value(self) -> float:
        charges: list[ChargeSession] = self.coordinator.data.get("charges", [])
        return round(sum(c.price or 0 for c in charges if c.ended_at is not None), 2)

    @property
    def native_unit_of_measurement(self) -> str:
        return self.coordinator.currency


class EvLedgerTotalDistanceSensor(_EvLedgerBaseSensor):
    """Running total distance driven — `state_class: total_increasing` for native HA
    Statistics graphs (distance only ever goes up)."""

    _attr_icon = "mdi:map-marker-distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "km"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "total_distance", "Total distance")

    @property
    def native_value(self) -> float:
        trips: list[Trip] = self.coordinator.data.get("trips", [])
        return round(sum(t.distance_km or 0 for t in trips), 1)


class EvLedgerLastTripSensor(_EvLedgerBaseSensor):
    """The most recent completed trip's distance, for a simple glanceable tile."""

    _attr_icon = "mdi:map-marker-path"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = "km"
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_trip", "Last trip")

    def _last_trip(self) -> Trip | None:
        trips: list[Trip] = self.coordinator.data.get("trips", [])
        finished = [t for t in trips if t.ended_at is not None]
        return finished[0] if finished else None

    @property
    def native_value(self) -> float | None:
        trip = self._last_trip()
        return trip.distance_km if trip else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        trip = self._last_trip()
        return trip.to_dict() if trip else {}


class EvLedgerLastChargeSensor(_EvLedgerBaseSensor):
    """The most recent completed charge session's cost, for a simple glanceable tile."""

    _attr_icon = "mdi:battery-charging-100"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "last_charge", "Last charge")

    def _last_charge(self) -> ChargeSession | None:
        charges: list[ChargeSession] = self.coordinator.data.get("charges", [])
        finished = [c for c in charges if c.ended_at is not None]
        return finished[0] if finished else None

    @property
    def native_value(self) -> float | None:
        charge = self._last_charge()
        return charge.price if charge else None

    @property
    def native_unit_of_measurement(self) -> str:
        return self.coordinator.currency

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        charge = self._last_charge()
        return charge.to_dict() if charge else {}


def _trip_energy_kwh(trip: Trip, battery_capacity_kwh: float) -> float | None:
    """Estimate a trip's energy use from its battery-percent drop.

    Only valid for trips that didn't charge mid-drive (battery % should only
    fall); returns None if the data needed isn't there or looks wrong.
    """
    if trip.start_battery_pct is None or trip.end_battery_pct is None:
        return None
    if not trip.distance_km or trip.distance_km <= 0:
        return None
    delta_pct = trip.start_battery_pct - trip.end_battery_pct
    if delta_pct <= 0:
        return None
    return (delta_pct / 100) * battery_capacity_kwh


def _bucket_for_temp(temp_c: float) -> str:
    if temp_c < TEMP_BUCKET_COLD_MAX_C:
        return "cold"
    if temp_c < TEMP_BUCKET_MILD_MAX_C:
        return "mild"
    return "warm"


class EvLedgerEfficiencySensor(_EvLedgerBaseSensor):
    """Real-world Wh/km, bucketed by outside temperature at trip start, vs. the
    configured rated (WLTP) consumption for this vehicle's model/trim."""

    _attr_icon = "mdi:thermometer-lines"
    _attr_native_unit_of_measurement = "Wh/km"
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "efficiency", "Efficiency vs. rated")
        self._battery_capacity_kwh: float = entry.data[CONF_BATTERY_CAPACITY_KWH]
        self._rated_wh_per_km: float = entry.data[CONF_RATED_WH_PER_KM]
        self._model_label: str = entry.data.get(CONF_MODEL_LABEL, "Custom")

    def _buckets(self) -> dict[str, dict[str, float]]:
        trips: list[Trip] = self.coordinator.data.get("trips", [])
        buckets: dict[str, dict[str, float]] = {
            "cold": {"kwh": 0.0, "km": 0.0, "trips": 0},
            "mild": {"kwh": 0.0, "km": 0.0, "trips": 0},
            "warm": {"kwh": 0.0, "km": 0.0, "trips": 0},
        }
        for trip in trips:
            energy_kwh = _trip_energy_kwh(trip, self._battery_capacity_kwh)
            if energy_kwh is None or trip.start_outside_temp_c is None:
                continue
            bucket = buckets[_bucket_for_temp(trip.start_outside_temp_c)]
            bucket["kwh"] += energy_kwh
            bucket["km"] += trip.distance_km
            bucket["trips"] += 1
        return buckets

    @staticmethod
    def _wh_per_km(bucket: dict[str, float]) -> float | None:
        if bucket["km"] <= 0:
            return None
        return round((bucket["kwh"] * 1000) / bucket["km"], 0)

    @property
    def native_value(self) -> float | None:
        overall_kwh = 0.0
        overall_km = 0.0
        for bucket in self._buckets().values():
            overall_kwh += bucket["kwh"]
            overall_km += bucket["km"]
        if overall_km <= 0:
            return None
        return round((overall_kwh * 1000) / overall_km, 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        buckets = self._buckets()
        result: dict[str, Any] = {
            "model": self._model_label,
            "rated_wh_per_km": self._rated_wh_per_km,
            "battery_capacity_kwh": self._battery_capacity_kwh,
        }
        for name, bucket in buckets.items():
            wh_per_km = self._wh_per_km(bucket)
            result[f"{name}_wh_per_km"] = wh_per_km
            result[f"{name}_trip_count"] = int(bucket["trips"])
            result[f"{name}_deviation_pct"] = (
                round(((wh_per_km - self._rated_wh_per_km) / self._rated_wh_per_km) * 100, 1)
                if wh_per_km is not None
                else None
            )
        return result


class EvLedgerMonthlyPerformanceSensor(_EvLedgerBaseSensor):
    """Month-by-month breakdown: distance driven, cost, and a performance score.

    Score = rated Wh/km ÷ actual Wh/km × 100 — 100 means driving exactly as
    efficiently as the official rated consumption, above 100 means beating
    it, below 100 means using more energy than rated. Cost is booked to the
    month a charge happened in (not the month the km were driven), matching
    how a real electricity bill works.

    The full per-month breakdown lives in the `months` attribute (newest
    first, up to MAX_MONTHS back) — that's the "flip back in time" view.
    The state itself is `state_class: measurement`, so HA's own History
    graph also shows the score's trend over time for free.
    """

    _attr_icon = "mdi:calendar-month"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: EvLedgerCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry, "monthly_performance", "Monthly performance")
        self._battery_capacity_kwh: float = entry.data[CONF_BATTERY_CAPACITY_KWH]
        self._rated_wh_per_km: float = entry.data[CONF_RATED_WH_PER_KM]

    def _months(self) -> list[dict[str, Any]]:
        trips: list[Trip] = self.coordinator.data.get("trips", [])
        charges: list[ChargeSession] = self.coordinator.data.get("charges", [])
        buckets: dict[str, dict[str, float]] = {}

        def _bucket(month_key: str) -> dict[str, float]:
            return buckets.setdefault(
                month_key, {"km": 0.0, "kwh": 0.0, "trip_count": 0, "cost": 0.0}
            )

        for trip in trips:
            if not trip.started_at or not trip.distance_km or trip.distance_km <= 0:
                continue
            bucket = _bucket(trip.started_at[:7])
            bucket["km"] += trip.distance_km
            bucket["trip_count"] += 1
            energy_kwh = _trip_energy_kwh(trip, self._battery_capacity_kwh)
            if energy_kwh is not None:
                bucket["kwh"] += energy_kwh

        for charge in charges:
            if charge.ended_at is None or charge.price is None or not charge.started_at:
                continue
            _bucket(charge.started_at[:7])["cost"] += charge.price

        months: list[dict[str, Any]] = []
        for month_key in sorted(buckets, reverse=True)[:MAX_MONTHS]:
            b = buckets[month_key]
            wh_per_km = round((b["kwh"] * 1000) / b["km"], 0) if b["km"] > 0 else None
            score = (
                round((self._rated_wh_per_km / wh_per_km) * 100, 0)
                if wh_per_km
                else None
            )
            months.append(
                {
                    "month": month_key,
                    "distance_km": round(b["km"], 1),
                    "trip_count": int(b["trip_count"]),
                    "wh_per_km": wh_per_km,
                    "score": score,
                    "cost": round(b["cost"], 2),
                    "cost_per_km": round(b["cost"] / b["km"], 2) if b["km"] > 0 else None,
                }
            )
        return months

    @property
    def native_value(self) -> float | None:
        months = self._months()
        return months[0]["score"] if months else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "rated_wh_per_km": self._rated_wh_per_km,
            "currency": self.coordinator.currency,
            "months": self._months(),
        }
