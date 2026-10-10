"""Polls the configured providers and maintains the trip/charge ledger."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_PUBLIC,
    METER_RESET_KWH,
    TRIP_END_IDLE_MINUTES,
    TRIP_MIN_DISTANCE_KM,
)
from .models import ChargeSession, LiveChargeState, Trip, VehicleSnapshot
from .providers.charger.base import CAP_COST_LOOKUP, CAP_LIVE_POWER, ChargerProvider
from .smart.plan import SLOT_MINUTES
from .providers.vehicle.base import VehicleProvider
from .store import EvLedgerStore

_LOGGER = logging.getLogger(__name__)

# Grace period after a home session ends before a still-charging vehicle can be
# mistaken for a brand-new public session (covers polling-tick lag between the
# charger's own state and the vehicle's onboard charging flag).
HOME_TO_PUBLIC_COOLDOWN_MINUTES = 2


class EvLedgerCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the vehicle + charger providers and maintains the trip/charge ledger."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        vehicle_name: str,
        currency: str,
        vehicle_provider: VehicleProvider,
        charger_providers: dict[str, ChargerProvider],
        store: EvLedgerStore,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry_id}",
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL_SECONDS),
        )
        self.vehicle_name = vehicle_name
        self.currency = currency
        self._vehicle_provider = vehicle_provider
        self._charger_providers = charger_providers
        self.store = store

        self._last_odometer_km: float | None = None
        self._trip_idle_since: datetime | None = None
        self._last_known_session_kwh: float | None = None
        self._home_cooldown_until: datetime | None = None
        self._vehicle_was_charging = False

    async def _async_update_data(self) -> dict[str, Any]:
        snapshot = self._vehicle_provider.get_snapshot(self.hass)
        now = dt_util.utcnow()

        if snapshot is not None:
            await self._process_trip(snapshot, now)
            await self._process_charge(snapshot, now)

        return {
            "snapshot": snapshot,
            "open_trip": self.store.get_open_trip(),
            "open_charge_home": self.store.get_open_charge(LOCATION_HOME),
            "open_charge_public": self.store.get_open_charge(LOCATION_PUBLIC),
            "trips": self.store.trips,
            "charges": self.store.charges,
        }

    # ---------------------------------------------------------------- trips

    async def _process_trip(self, snapshot: VehicleSnapshot, now: datetime) -> None:
        open_trip = self.store.get_open_trip()

        if snapshot.is_charging:
            # Never run a trip while plugged in and charging.
            if open_trip is not None:
                await self._end_trip(open_trip, snapshot, now)
            self._trip_idle_since = None
            if snapshot.odometer_km is not None:
                self._last_odometer_km = snapshot.odometer_km
            return

        if snapshot.odometer_km is None:
            return

        if open_trip is None:
            if self._last_odometer_km is None:
                self._last_odometer_km = snapshot.odometer_km
                return
            moved = snapshot.odometer_km - self._last_odometer_km
            if moved >= TRIP_MIN_DISTANCE_KM:
                open_trip = Trip(
                    id=self.store.new_id(),
                    started_at=now.isoformat(),
                    ended_at=None,
                    start_odometer_km=self._last_odometer_km,
                    end_odometer_km=None,
                    distance_km=None,
                    start_lat=snapshot.latitude,
                    start_lon=snapshot.longitude,
                    end_lat=None,
                    end_lon=None,
                    start_battery_pct=snapshot.battery_pct,
                    end_battery_pct=None,
                    start_outside_temp_c=snapshot.outside_temp_c,
                )
                await self.store.async_upsert_trip(open_trip)
                self._trip_idle_since = None
                _LOGGER.info(
                    "%s: trip started at odometer %.1f km", self.vehicle_name, self._last_odometer_km
                )
            self._last_odometer_km = snapshot.odometer_km
            return

        moved = snapshot.odometer_km - (self._last_odometer_km or snapshot.odometer_km)
        if moved > 0.01:
            self._trip_idle_since = None
        else:
            if self._trip_idle_since is None:
                self._trip_idle_since = now
            elif (now - self._trip_idle_since).total_seconds() >= TRIP_END_IDLE_MINUTES * 60:
                await self._end_trip(open_trip, snapshot, now)
                self._trip_idle_since = None

        self._last_odometer_km = snapshot.odometer_km

    async def _end_trip(self, trip: Trip, snapshot: VehicleSnapshot, now: datetime) -> None:
        trip.ended_at = now.isoformat()
        trip.end_odometer_km = snapshot.odometer_km
        trip.end_lat = snapshot.latitude
        trip.end_lon = snapshot.longitude
        trip.end_battery_pct = snapshot.battery_pct
        if trip.start_odometer_km is not None and snapshot.odometer_km is not None:
            trip.distance_km = round(snapshot.odometer_km - trip.start_odometer_km, 1)
        await self.store.async_upsert_trip(trip)
        _LOGGER.info("%s: trip ended, %s km", self.vehicle_name, trip.distance_km)

    # -------------------------------------------------------------- charging

    async def _process_charge(self, snapshot: VehicleSnapshot, now: datetime) -> None:
        live_states = {}
        for provider_id, provider in self._charger_providers.items():
            if CAP_LIVE_POWER in provider.capabilities:
                state = provider.get_live_state(self.hass)
                if state is not None:
                    live_states[provider_id] = state

        home_charging = any(state.is_charging for state in live_states.values())
        home_provider_id = next(
            (pid for pid, state in live_states.items() if state.is_charging), None
        )
        if home_charging:
            for state in live_states.values():
                if state.session_energy_kwh:
                    self._last_known_session_kwh = state.session_energy_kwh

        open_home = self.store.get_open_charge(LOCATION_HOME)
        open_public = self.store.get_open_charge(LOCATION_PUBLIC)
        counter = next((s.session_energy_kwh for s in live_states.values() if s.session_energy_kwh is not None), None)

        if open_home is not None and counter is not None:
            self._meter_tick(open_home, counter, now)

        if home_charging and open_home is None:
            open_home = ChargeSession(
                id=self.store.new_id(),
                location_kind=LOCATION_HOME,
                provider=home_provider_id or "unknown",
                started_at=now.isoformat(),
                ended_at=None,
                kwh=None,
                price=None,
                price_currency=self.currency,
                location_name="Home",
                start_battery_pct=snapshot.battery_pct,
                end_battery_pct=None,
                needs_review=False,
            )
            self._last_known_session_kwh = None
            await self.store.async_upsert_charge(open_home)
            await self.store.async_set_meter_start(open_home.id, counter)
            _LOGGER.info("%s: home charging started (%s)", self.vehicle_name, home_provider_id)

        elif not home_charging and open_home is not None:
            await self._end_home_charge(open_home, snapshot, now, live_states)
            self._home_cooldown_until = now + timedelta(minutes=HOME_TO_PUBLIC_COOLDOWN_MINUTES)

        vehicle_charging = bool(snapshot.is_charging)
        in_cooldown = self._home_cooldown_until is not None and now < self._home_cooldown_until

        # Only a genuine off->on transition of the vehicle's own charging flag
        # counts as "a new session started" — a home session ending doesn't
        # always bring this flag down in the same poll tick (the vehicle can
        # keep reporting charging for a while after Zaptec says it's done),
        # so treating "still charging" as "new public session" invents a
        # phantom session out of the tail end of the home charge.
        vehicle_charge_edge = vehicle_charging and not self._vehicle_was_charging
        self._vehicle_was_charging = vehicle_charging

        if vehicle_charge_edge and not home_charging and not in_cooldown:
            if open_public is None:
                open_public = ChargeSession(
                    id=self.store.new_id(),
                    location_kind=LOCATION_PUBLIC,
                    provider="vehicle",
                    started_at=now.isoformat(),
                    ended_at=None,
                    kwh=None,
                    price=None,
                    price_currency=self.currency,
                    location_name=None,
                    start_battery_pct=snapshot.battery_pct,
                    end_battery_pct=None,
                    needs_review=True,
                )
                await self.store.async_upsert_charge(open_public)
                _LOGGER.info("%s: public charging started", self.vehicle_name)
        elif not vehicle_charging and open_public is not None:
            open_public.ended_at = now.isoformat()
            open_public.end_battery_pct = snapshot.battery_pct
            await self.store.async_upsert_charge(open_public)
            _LOGGER.info(
                "%s: public charging ended, waiting for price (log_public_charge service)",
                self.vehicle_name,
            )

    def _billed_cost(self, now: datetime, kwh: float | None, spot: bool):
        for provider in self._charger_providers.values():
            if CAP_COST_LOOKUP in provider.capabilities and (provider.provider_id == "spot_price") == spot:
                cost = provider.get_recent_session_cost(self.hass, now, known_kwh=kwh)
                if cost is not None:
                    return cost
        return None

    def _metered_cost(self, meter: dict, kwh: float | None, now: datetime) -> float | None:
        """The price of the energy as it was counted; the last bit after the last look at the price now."""
        priced, cost = float(meter.get("priced") or 0.0), float(meter.get("cost") or 0.0)
        if not kwh or priced <= 0:
            return None
        if priced >= kwh:
            return round(cost * kwh / priced, 2)
        price = self._price_now(now)
        if price is None:
            return None
        return round(cost + (kwh - priced) * price, 2)

    def _price_now(self, now: datetime) -> float | None:
        """The spot price now: the quarter, or the mean of the hour when smart charging plans in hours."""
        provider = next((p for p in self._charger_providers.values() if p.provider_id == "spot_price"), None)
        if provider is None:
            return None
        smart = getattr(self, "smart", None)
        return provider.price_at(self.hass, now, smart.slot_minutes if smart is not None else SLOT_MINUTES)

    def _meter_tick(self, charge: ChargeSession, counter: float, now: datetime) -> None:
        """Price what the charger counted since the last look at the price of this moment."""
        meter = self.store.meter(charge.id)
        if meter is None:
            return
        start = meter.get("kwh")
        last = meter.get("last", start)
        if last is not None and counter < last - METER_RESET_KWH:
            # The counter was reset (a new plug-in that still showed the last one's total): count from zero.
            start, last = 0.0, 0.0
        cost, priced = float(meter.get("cost") or 0.0), float(meter.get("priced") or 0.0)
        added = counter - last if last is not None and counter > last else 0.0
        if added > 0 and (price := self._price_now(now)) is not None:
            cost += added * price
            priced += added
        values = {"kwh": start, "last": counter, "cost": round(cost, 5), "priced": round(priced, 5)}
        if any(meter.get(key) != value for key, value in values.items()):
            self.store.update_meter(charge.id, values)

    async def _end_home_charge(
        self,
        charge: ChargeSession,
        snapshot: VehicleSnapshot,
        now: datetime,
        live_states: dict[str, LiveChargeState],
    ) -> None:
        charge.ended_at = now.isoformat()
        charge.end_battery_pct = snapshot.battery_pct

        # The counter read while this charge ran belongs to this plug-in for sure. Zaptec's completed-session
        # reading is only set some minutes after the unplug and shows the previous plug-in until then, so it is
        # only used when no reading was taken (charging stopped while Home Assistant was restarting).
        completed_kwh = next(
            (s.completed_session_kwh for s in live_states.values() if s.completed_session_kwh),
            None,
        )
        meter = self.store.meter(charge.id) or {}
        readings = [value for value in (self._last_known_session_kwh, meter.get("last")) if value]
        total = max(readings) if readings else completed_kwh
        # Zaptec's counter runs from plug-in to unplug, also through a pause: count only what this charge added.
        start = meter.get("kwh")
        if total is not None and start is not None and total >= start:
            total = round(total - start, 3)
        charge.kwh = total

        # A billed cost (Monta) first, then the energy priced while it was counted, then the price at the end.
        cost = self._billed_cost(now, charge.kwh, spot=False)
        metered = None if cost is not None else self._metered_cost(meter, charge.kwh, now)
        if cost is None and metered is None:
            cost = self._billed_cost(now, charge.kwh, spot=True)

        if cost is not None:
            charge.kwh = cost.kwh
            charge.price = cost.price
            charge.needs_review = False
        elif metered is not None:
            charge.price = metered
            charge.needs_review = False
        else:
            charge.needs_review = True

        await self.store.async_upsert_charge(charge)
        self._last_known_session_kwh = None
        self.hass.bus.async_fire(f"{DOMAIN}_charge_finished", {"vehicle": self.vehicle_name, **charge.to_dict()})
        if (smart := getattr(self, "smart", None)) is not None:
            smart.routines.charge_finished(charge)  # the message when the whole charge is done
        _LOGGER.info(
            "%s: home charging ended, %.2f kWh, %s %.2f",
            self.vehicle_name,
            charge.kwh or 0,
            charge.price_currency,
            charge.price or 0,
        )
