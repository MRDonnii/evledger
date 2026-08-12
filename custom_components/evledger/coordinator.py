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
    TRIP_END_IDLE_MINUTES,
    TRIP_MIN_DISTANCE_KM,
)
from .models import ChargeSession, LiveChargeState, Trip, VehicleSnapshot
from .providers.charger.base import CAP_COST_LOOKUP, CAP_LIVE_POWER, ChargerProvider
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
            _LOGGER.info("%s: home charging started (%s)", self.vehicle_name, home_provider_id)

        elif not home_charging and open_home is not None:
            await self._end_home_charge(open_home, snapshot, now, live_states)
            self._home_cooldown_until = now + timedelta(minutes=HOME_TO_PUBLIC_COOLDOWN_MINUTES)

        vehicle_charging = bool(snapshot.is_charging)
        in_cooldown = self._home_cooldown_until is not None and now < self._home_cooldown_until

        if vehicle_charging and not home_charging and not in_cooldown:
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

    async def _end_home_charge(
        self,
        charge: ChargeSession,
        snapshot: VehicleSnapshot,
        now: datetime,
        live_states: dict[str, LiveChargeState],
    ) -> None:
        charge.ended_at = now.isoformat()
        charge.end_battery_pct = snapshot.battery_pct

        # A stable post-session reading (if any provider exposes one) beats a
        # value captured mid-session, which can go stale across a restart.
        completed_kwh = next(
            (s.completed_session_kwh for s in live_states.values() if s.completed_session_kwh),
            None,
        )
        charge.kwh = completed_kwh or self._last_known_session_kwh

        cost = None
        for provider in self._charger_providers.values():
            if CAP_COST_LOOKUP in provider.capabilities:
                cost = provider.get_recent_session_cost(self.hass, now, known_kwh=charge.kwh)
                if cost is not None:
                    break

        if cost is not None:
            charge.kwh = cost.kwh
            charge.price = cost.price
            charge.needs_review = False
        else:
            charge.needs_review = True

        await self.store.async_upsert_charge(charge)
        self._last_known_session_kwh = None
        _LOGGER.info(
            "%s: home charging ended, %.2f kWh, %s %.2f",
            self.vehicle_name,
            charge.kwh or 0,
            charge.price_currency,
            charge.price or 0,
        )
