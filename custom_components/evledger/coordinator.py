"""Polls the configured providers and maintains the trip/charge ledger."""
from __future__ import annotations

import logging
import math
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
from .public_place import PLACE_RADIUS_M, async_place, distance_m, nearest_place
from .providers.charger.base import CAP_COST_LOOKUP, CAP_LIVE_POWER, ChargerProvider
from .smart.plan import SLOT_MINUTES
from .providers.vehicle.base import VehicleProvider
from .store import EvLedgerStore
from . import tyres as tyre_logic
from .tyres import TyreChange, TyreSet

_LOGGER = logging.getLogger(__name__)

# Grace period after a home session ends before a still-charging vehicle can be
# mistaken for a brand-new public session (covers polling-tick lag between the
# charger's own state and the vehicle's onboard charging flag).
HOME_TO_PUBLIC_COOLDOWN_MINUTES = 2
# The other way round: the car can report charging a poll or two before the home charger does. A public session it
# opened this shortly before the home charger began was the start of that home charge, not a charge away from home.
PUBLIC_BEFORE_HOME_MINUTES = 10
# A public charge priced with the default price per kWh is replaced by a price logged by hand this long after it
PUBLIC_DEFAULT_REPLACE_HOURS = 12
# A fast charger loses a few % on the way into the battery: the battery's gain is that much less than was billed
PUBLIC_CHARGE_EFFICIENCY = 0.95


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
        self.battery_capacity_kwh: float | None = None  # set from the entry; trips' energy for the tyre use
        self.ocm_api_key: str | None = None  # set from the entry: Open Charge Map for public charging stations

    async def _async_update_data(self) -> dict[str, Any]:
        snapshot = self._vehicle_provider.get_snapshot(self.hass)
        now = dt_util.utcnow()

        if snapshot is not None:
            await self._process_trip(snapshot, now)
            await self._process_charge(snapshot, now)
            if snapshot.odometer_km is not None and self._last_odometer_km is None:
                self._last_odometer_km = snapshot.odometer_km
        if (smart := getattr(self, "smart", None)) is not None:
            # A reminder is an extra: a fault in it must not make the ledger's entities unavailable.
            smart._optional("tyre reminder", lambda: self._tyre_reminder(now))

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

    # ---------------------------------------------------------- public charge

    async def async_log_public_charge(self, kwh: float, price: float, location_name: str | None = None,
                                      note: str | None = None, started_at: datetime | None = None,
                                      charge_id: str | None = None) -> None:
        """A public charge with its price: fills in the given public session (the one a phone was asked about), else
        the public session waiting for its price, else a new one. A home charge waiting for its price is left alone."""
        now = dt_util.utcnow()
        pending = self.store.get_charge(charge_id) if charge_id else None
        if pending is None or pending.location_kind != LOCATION_PUBLIC:
            pending = (self.store.get_latest_pending_review_charge(LOCATION_PUBLIC)
                       or self._recent_auto_priced(now))
        if pending is not None:
            pending.kwh = kwh
            pending.price = price
            pending.location_name = location_name or pending.location_name
            pending.note = note or pending.note
            pending.needs_review = False
            pending.price_source = "manual"
            await self.store.async_upsert_charge(pending)
            if location_name and pending.latitude is not None and pending.longitude is not None:
                # Named here: the next charge at this place gets its name and price per kWh by itself
                await self.async_remember_place(location_name.strip(), pending.latitude, pending.longitude,
                                                price / kwh if kwh else None, pending.operator)
        else:
            await self.store.async_upsert_charge(ChargeSession(
                id=self.store.new_id(), location_kind=LOCATION_PUBLIC, provider="manual",
                started_at=(started_at or now).isoformat(), ended_at=now.isoformat(), kwh=kwh, price=price,
                price_currency=self.currency, location_name=location_name, start_battery_pct=None,
                end_battery_pct=None, needs_review=False, note=note, price_source="manual",
            ))
        await self.async_request_refresh()

    async def _find_place(self, charge: ChargeSession, snapshot: VehicleSnapshot | None) -> None:
        """Where the car charges (a named place, a zone, a Tesla Supercharger, an Open Charge Map station): the
        charge's place, operator and position (never fatal)."""
        if snapshot is None or charge.location_name or charge.zone or snapshot.latitude is None:
            return
        charge.latitude, charge.longitude = snapshot.latitude, snapshot.longitude
        try:
            found = await async_place(self.hass, snapshot.latitude, snapshot.longitude,
                                      self.store.settings.get("places"), self.ocm_api_key)
        except Exception:  # noqa: BLE001 - the place is an extra, the charge must be saved
            _LOGGER.debug("%s: place of the public charge not found", self.vehicle_name, exc_info=True)
            return
        charge.zone, charge.location_name, charge.operator = found["zone"], found["name"], found["operator"]

    def _auto_price(self, charge: ChargeSession) -> tuple[float | None, str | None]:
        """The price per kWh a public charge gets by itself, and from where: a place named before, the zone, the
        operator, the default price."""
        if charge.latitude is not None and charge.longitude is not None:
            place = nearest_place(self.store.settings.get("places"), charge.latitude, charge.longitude)
            if place is not None and place.get("price") is not None:
                return float(place["price"]), "place"
        if (zone_price := self.zone_price(charge.zone)) is not None:
            return zone_price, "zone"
        if (operator_price := self.operator_price(charge.operator)) is not None:
            return operator_price, "operator"
        default = self.public_default_price()
        return (default, "default") if default else (None, None)

    def operator_price(self, operator: str | None) -> float | None:
        """The price per kWh set for a charging operator (matched without case, either name inside the other)."""
        if not operator:
            return None
        wanted = operator.lower()
        for name, price in (self.store.settings.get("operator_prices") or {}).items():
            key = str(name).lower()
            if key and (key in wanted or wanted in key):
                try:
                    value = float(price)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value) and value >= 0:
                    return value
        return None

    async def async_set_operator_price(self, operator: str, price: float | None) -> None:
        """Set (or with None remove) a charging operator's price per kWh."""
        prices = {name: value for name, value in (self.store.settings.get("operator_prices") or {}).items()
                  if name.lower() != operator.strip().lower()}
        if price is not None:
            prices[operator.strip()] = round(float(price), 4)
        await self.store.async_set_setting("operator_prices", prices)
        self.async_update_listeners()

    async def async_remember_place(self, name: str, latitude: float, longitude: float, price: float | None,
                                   operator: str | None = None) -> None:
        """Keep a named place (and its price per kWh): a public charge within PLACE_RADIUS_M gets them by itself."""
        places = [place for place in (self.store.settings.get("places") or [])
                  if not (distance_m(latitude, longitude, float(place["latitude"]), float(place["longitude"]))
                          <= PLACE_RADIUS_M or str(place.get("name", "")).lower() == name.lower())]
        places.append({"name": name, "latitude": round(latitude, 6), "longitude": round(longitude, 6),
                       "price": None if price is None else round(float(price), 4), "operator": operator})
        await self.store.async_set_setting("places", places)
        self.async_update_listeners()

    async def async_forget_place(self, name: str) -> bool:
        places = self.store.settings.get("places") or []
        kept = [place for place in places if str(place.get("name", "")).lower() != name.strip().lower()]
        await self.store.async_set_setting("places", kept)
        self.async_update_listeners()
        return len(kept) != len(places)

    def zone_price(self, zone: str | None) -> float | None:
        """The price per kWh of charging in this zone (0 = free), if one is set."""
        prices = self.store.settings.get("zone_prices") or {}
        try:
            value = float(prices[zone]) if zone and zone in prices else None
        except (TypeError, ValueError):
            return None
        return value if value is not None and math.isfinite(value) and value >= 0 else None

    async def async_set_zone_price(self, zone: str, price: float | None) -> None:
        """Set (or with None remove) the price per kWh of charging in a zone."""
        prices = dict(self.store.settings.get("zone_prices") or {})
        if price is None:
            prices.pop(zone, None)
        else:
            prices[zone] = round(float(price), 4)
        await self.store.async_set_setting("zone_prices", prices)
        self.async_update_listeners()

    def _recent_auto_priced(self, now: datetime) -> ChargeSession | None:
        """The latest public charge priced with the default or a zone price, if it ended within
        PUBLIC_DEFAULT_REPLACE_HOURS: a price logged by hand now is that charge's real price ("unless I put in something
        else")."""
        latest = None
        for charge in self.store.charges:
            ended = dt_util.parse_datetime(charge.ended_at or "")
            if (charge.location_kind == LOCATION_PUBLIC and charge.price_source in ("place", "zone", "operator", "default")
                    and ended is not None
                    and now - ended <= timedelta(hours=PUBLIC_DEFAULT_REPLACE_HOURS)
                    and (latest is None or ended > latest[0])):
                latest = (ended, charge)
        return latest[1] if latest else None

    def public_default_price(self) -> float:
        """The price per kWh a public charge gets when it ends (0 = none, then the phones are asked as before)."""
        try:
            value = float(self.store.settings.get("public_default_price") or 0)
        except (TypeError, ValueError):
            return 0.0
        return value if math.isfinite(value) and value > 0 else 0.0

    def _capacity_kwh(self) -> float | None:
        smart = getattr(self, "smart", None)
        capacity = getattr(smart, "capacity", None) if smart is not None else None
        capacity = capacity or self.battery_capacity_kwh
        return capacity if capacity and capacity > 0 else None

    def _energy_added_entity(self) -> str | None:
        """The car's own counter of the energy added in its current/last charge (Tesla Fleet charge_energy_added,
        Tesla Custom energy_added), of the car's integrations the one that reported last."""
        sources = getattr(self._vehicle_provider, "sources", None)
        if sources is None:
            return None
        return sources.freshest_matching(self.hass, lambda entry: entry.domain == "sensor" and "energy_added" in (
            f"{entry.translation_key or ''} {entry.unique_id or ''} {entry.entity_id}"))

    def public_kwh_estimate(self, charge: ChargeSession) -> tuple[float | None, str | None]:
        """About what a public charge put in, and from where: the car's own counter when it moved during this charge
        and roughly agrees with the battery ("car"), else the battery's gain over the charge ("battery")."""
        estimate = None
        capacity = self._capacity_kwh()
        start, end = charge.start_battery_pct, charge.end_battery_pct
        if capacity and start is not None and end is not None and end > start:
            estimate = round((end - start) / 100 * capacity / PUBLIC_CHARGE_EFFICIENCY, 1)
        entity_id = self._energy_added_entity()
        state = self.hass.states.get(entity_id) if entity_id else None
        started = dt_util.parse_datetime(charge.started_at or "")
        try:
            counter = float(state.state) if state is not None else None
        except (TypeError, ValueError):
            counter = None
        if (counter is not None and math.isfinite(counter) and counter > 0 and started is not None
                and state.last_changed >= started
                and (estimate is None or 0.5 <= counter / estimate <= 2.0)):
            return round(counter, 1), "car"
        return (estimate, "battery") if estimate else (None, None)

    def home_power_kw(self) -> float | None:
        """The home charger's power while this car has it (kW): 0 while another car on a shared charger has it."""
        for provider in self._charger_providers.values():
            if CAP_LIVE_POWER not in provider.capabilities:
                continue
            reader = getattr(provider, "live_power_w", None)
            if reader is not None:
                power_w = reader(self.hass)
            else:
                state = provider.get_live_state(self.hass)
                power_w = state.power_w if state is not None else None
            if power_w is None:
                continue
            smart = getattr(self, "smart", None)
            if smart is not None and smart.shared and not smart.car_present:
                return 0.0
            return round(max(power_w, 0.0) / 1000, 3)
        return None

    def home_energy(self, since: datetime | None = None) -> float:
        """kWh charged at home: the charges that ended since then (all of them without a time) and the one running."""
        total = 0.0
        for charge in self.store.charges:
            if charge.location_kind != LOCATION_HOME:
                continue
            if charge.ended_at:
                ended = dt_util.parse_datetime(charge.ended_at)
                if since is None or (ended is not None and ended >= since):
                    total += charge.kwh or 0.0
            else:
                total += self.running_kwh(charge.id)
        return round(total, 2)

    def running_kwh(self, charge_id: str) -> float:
        """What the charger has counted for the open home charge so far, also the energy no price was found for."""
        meter = self.store.meter(charge_id) or {}
        start, last = meter.get("kwh"), meter.get("last")
        counted = last - start if isinstance(start, (int, float)) and isinstance(last, (int, float)) else 0.0
        return max(counted, float(meter.get("priced") or 0.0), 0.0)

    async def async_add_charge(self, kwh: float, price: float | None, started_at: datetime,
                               ended_at: datetime | None = None, location: str = LOCATION_HOME,
                               location_name: str | None = None, note: str | None = None) -> None:
        """A finished charge the ledger did not record itself (home or public), e.g. from before EV Ledger."""
        zone = dt_util.get_default_time_zone()
        started = dt_util.as_utc(started_at if started_at.tzinfo else started_at.replace(tzinfo=zone))
        ended = ended_at or started_at
        ended = dt_util.as_utc(ended if ended.tzinfo else ended.replace(tzinfo=zone))
        await self.store.async_upsert_charge(ChargeSession(
            id=self.store.new_id(), location_kind=LOCATION_PUBLIC if location == "public" else LOCATION_HOME,
            provider="manual", started_at=started.isoformat(), ended_at=max(ended, started).isoformat(), kwh=kwh,
            price=price, price_currency=self.currency, location_name=location_name, start_battery_pct=None,
            end_battery_pct=None, needs_review=price is None, note=note,
        ))
        await self.async_request_refresh()

    def costs_by_month(self) -> dict[str, dict]:
        """Charging per calendar month (local time, by when the charge ended): kWh and cost, home and away."""
        months: dict[str, dict] = {}

        def month(key: str) -> dict:
            return months.setdefault(key, {"month": key, "cost": 0.0, "kwh": 0.0, "home_cost": 0.0,
                                           "public_cost": 0.0, "charges": 0, "unpriced": 0})

        for charge in self.store.charges:
            ended = dt_util.parse_datetime(charge.ended_at or "")
            if ended is None:
                continue
            bucket = month(dt_util.as_local(ended).strftime("%Y-%m"))
            bucket["charges"] += 1
            bucket["kwh"] += charge.kwh or 0.0
            if charge.price is None:
                bucket["unpriced"] += 1
                continue
            bucket["cost"] += charge.price
            bucket["public_cost" if charge.location_kind == LOCATION_PUBLIC else "home_cost"] += charge.price
        # The home charge running now counts in this month, priced as it charges.
        open_home = self.store.get_open_charge(LOCATION_HOME)
        if open_home is not None and (meter := self.store.meter(open_home.id)):
            bucket = month(dt_util.now().strftime("%Y-%m"))
            bucket["cost"] += float(meter.get("cost") or 0.0)
            bucket["home_cost"] += float(meter.get("cost") or 0.0)
            bucket["kwh"] += self.running_kwh(open_home.id)
        return months

    def distance_today(self) -> float:
        """Kilometres driven today: the trips that ended today and the one under way."""
        today = dt_util.now().date()
        total = 0.0
        for trip in self.store.trips:
            if trip.ended_at is None:
                if trip.start_odometer_km is not None and self._last_odometer_km is not None:
                    total += max(self._last_odometer_km - trip.start_odometer_km, 0.0)
                continue
            ended = dt_util.parse_datetime(trip.ended_at)
            if ended and dt_util.as_local(ended).date() == today:
                total += trip.distance_km or 0.0
        return round(total, 1)

    # ----------------------------------------------------------------- tyres

    def odometer_now(self) -> float | None:
        return self._last_odometer_km

    def tyre_stats(self) -> dict[str, Any]:
        """The tyre sets with their use, each season and the comparison (see tyres.stats)."""
        return tyre_logic.stats(self.store.tyre_sets, self.store.tyre_changes, self.store.trips, self.store.charges,
                                self.battery_capacity_kwh, self._last_odometer_km, dt_util.utcnow())

    def active_tyre_set(self) -> TyreSet | None:
        set_id = tyre_logic.active_set(self.store.tyre_changes)
        return self.store.get_tyre_set(set_id) if set_id else None

    def tyre_suggestion(self, now: datetime | None = None) -> dict[str, Any] | None:
        now = now or dt_util.utcnow()
        temps = [(start, trip.start_outside_temp_c) for trip in self.store.trips
                 if trip.start_outside_temp_c is not None and (start := dt_util.parse_datetime(trip.started_at))]
        return tyre_logic.suggestion(self.active_tyre_set(), self.store.tyre_sets, temps, now)

    def find_tyre_set(self, key: str) -> TyreSet | None:
        """A set by its id or (case-insensitive) name."""
        found = self.store.get_tyre_set(key)
        if found is None:
            found = next((t for t in self.store.tyre_sets if t.name.casefold() == key.strip().casefold()), None)
        return found

    async def async_save_tyre_set(self, values: dict[str, Any], set_id: str | None = None) -> TyreSet:
        """A new set, or the given fields of an existing one (None clears a field)."""
        tyre = self.store.get_tyre_set(set_id) if set_id else None
        if set_id and tyre is None:
            raise KeyError(set_id)
        if tyre is None:
            tyre = TyreSet(id=self.store.new_id(), name=values.get("name") or "Hjulsæt")
        for key, value in values.items():
            if hasattr(tyre, key) and key not in ("id", "treads"):
                setattr(tyre, key, value)
        if tyre.km_before is None:
            tyre.km_before = 0.0
        await self.store.async_upsert_tyre_set(tyre)
        self.async_update_listeners()  # show it now; no new reading of the car needed
        return tyre

    async def async_delete_tyre_set(self, set_id: str) -> bool:
        deleted = await self.store.async_delete_tyre_set(set_id)
        self.async_update_listeners()  # show it now; no new reading of the car needed
        return deleted

    def _odometer_at(self, at: datetime) -> float | None:
        return tyre_logic.odometer_at(at, self.store.trips, self._last_odometer_km, dt_util.utcnow())

    async def async_change_tyres(self, set_id: str, at: datetime | None = None, odometer_km: float | None = None,
                                 note: str | None = None) -> TyreChange:
        """The set went on the car then (now without a time); the odometer is read from the trips when not given."""
        when = dt_util.as_utc(at) if at else dt_util.utcnow()
        if tyre_logic.active_set(self.store.tyre_changes, when) == set_id:
            raise ValueError("already_mounted")
        change = TyreChange(id=self.store.new_id(), set_id=set_id, at=when.isoformat(),
                            odometer_km=odometer_km if odometer_km is not None else self._odometer_at(when),
                            note=note)
        await self.store.async_upsert_tyre_change(change)
        tyre = self.store.get_tyre_set(set_id)
        if tyre is not None and tyre.retired:
            tyre.retired = False
            await self.store.async_upsert_tyre_set(tyre)
        self.hass.bus.async_fire(f"{DOMAIN}_tyres_changed", {"vehicle": self.vehicle_name, "set_id": set_id,
                                                              "set": tyre.name if tyre else None, **change.to_dict()})
        self.async_update_listeners()  # show it now; no new reading of the car needed
        return change

    async def async_update_tyre_change(self, change_id: str, at: datetime | None = None,
                                       odometer_km: float | None = None, note: str | None = None) -> TyreChange:
        change = next((c for c in self.store.tyre_changes if c.id == change_id), None)
        if change is None:
            raise KeyError(change_id)
        if at is not None:
            change.at = dt_util.as_utc(at).isoformat()
            if odometer_km is None:
                change.odometer_km = self._odometer_at(dt_util.as_utc(at))
        if odometer_km is not None:
            change.odometer_km = odometer_km
        if note is not None:
            change.note = note or None
        await self.store.async_upsert_tyre_change(change)
        self.async_update_listeners()  # show it now; no new reading of the car needed
        return change

    async def async_delete_tyre_change(self, change_id: str) -> bool:
        deleted = await self.store.async_delete_tyre_change(change_id)
        self.async_update_listeners()  # show it now; no new reading of the car needed
        return deleted

    async def async_log_tread(self, set_id: str, mm: float | None = None, each: list[float] | None = None,
                              at: datetime | None = None, odometer_km: float | None = None) -> dict:
        """A tread depth reading: one value, or one per tyre (the least one counts)."""
        tyre = self.store.get_tyre_set(set_id)
        if tyre is None:
            raise KeyError(set_id)
        values = [float(v) for v in (each or []) if v is not None]
        depth = min(values) if values else mm
        if depth is None:
            raise ValueError("no_depth")
        when = dt_util.as_utc(at) if at else dt_util.utcnow()
        reading = {"id": self.store.new_id(), "at": when.isoformat(), "mm": round(float(depth), 1),
                   "each": values or None,
                   "odometer_km": odometer_km if odometer_km is not None else self._odometer_at(when)}
        tyre.treads = sorted([*tyre.treads, reading], key=lambda r: r["at"])
        await self.store.async_upsert_tyre_set(tyre)
        self.async_update_listeners()  # show it now; no new reading of the car needed
        return reading

    async def async_delete_tread(self, set_id: str, reading_id: str) -> bool:
        tyre = self.store.get_tyre_set(set_id)
        if tyre is None:
            return False
        kept = [r for r in tyre.treads if r.get("id") != reading_id]
        if len(kept) == len(tyre.treads):
            return False
        tyre.treads = kept
        await self.store.async_upsert_tyre_set(tyre)
        self.async_update_listeners()  # show it now; no new reading of the car needed
        return True

    def _tyre_reminder(self, now: datetime) -> None:
        """A phone message once a season when it is time to change (smart charging's phones, its own switch)."""
        smart = getattr(self, "smart", None)
        if smart is None or not smart.flags.get("tyre_reminder") or not smart.notify.targets:
            return
        local = dt_util.as_local(now)
        if not 9 <= local.hour < 20:
            return
        tip = self.tyre_suggestion(now)
        if tip is None or tip["key"] == self.store.tyre_notified:
            return
        active = self.active_tyre_set()
        goal = {"winter": "vinterhjul", "summer": "sommerhjul"}.get(tip["to"], "andre hjul")
        text = f"{tip['reason']} Tid til {goal}" + (f" ({tip['set']})." if tip.get("set") else ".")
        if active is not None:
            text += f" På bilen nu: {active.name}."
        text += " Tryk Skift hjul i bilkortet, når de er skiftet."
        self.hass.async_create_task(self.store.async_set_tyre_notified(tip["key"]))
        self.hass.async_create_background_task(
            smart.notify.async_send_note("tyres", f"skift til {goal}", text, icon="mdi:tire"),
            "evledger_tyre_reminder")

    # -------------------------------------------------------------- charging

    async def _process_charge(self, snapshot: VehicleSnapshot, now: datetime) -> None:
        live_states = {}
        for provider_id, provider in self._charger_providers.items():
            if CAP_LIVE_POWER in provider.capabilities:
                state = provider.get_live_state(self.hass)
                if state is not None:
                    live_states[provider_id] = state

        home_charging = any(state.is_charging for state in live_states.values())
        smart = getattr(self, "smart", None)
        if home_charging and smart is not None and smart.shared and not smart.car_present:
            home_charging = False  # another of the cars on the shared charger is the one charging
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
            start_battery_pct = snapshot.battery_pct
            if open_public is not None and self._opened_just_before(open_public, now):
                # The car said it was charging before the home charger did: that was this home charge starting.
                await self.store.async_delete_charge(open_public.id)
                if open_public.start_battery_pct is not None:
                    start_battery_pct = open_public.start_battery_pct
                open_public = None
                _LOGGER.info("%s: the public session just opened was the home charge starting", self.vehicle_name)
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
                start_battery_pct=start_battery_pct,
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
                await self._find_place(open_public, snapshot)
                await self.store.async_upsert_charge(open_public)
                _LOGGER.info("%s: public charging started%s", self.vehicle_name,
                             f" at {open_public.location_name}" if open_public.location_name else "")
        elif not vehicle_charging and open_public is not None:
            open_public.ended_at = now.isoformat()
            open_public.end_battery_pct = snapshot.battery_pct
            if open_public.location_name is None and open_public.zone is None:
                await self._find_place(open_public, snapshot)  # the car had no position when it started
            # A place named before, the zone (e.g. free at family), the operator, else the public default price (e.g.
            # Tesla Supercharger's flat price) prices the charge at once; any price given later replaces it
            per_kwh, source = self._auto_price(open_public)
            kwh = self.public_kwh_estimate(open_public)[0] if per_kwh is not None else None
            if per_kwh is not None and (kwh or per_kwh == 0):
                open_public.kwh = kwh
                open_public.price = round((kwh or 0) * per_kwh, 2)
                open_public.price_source = source
                open_public.needs_review = False
            await self.store.async_upsert_charge(open_public)
            _LOGGER.info(
                "%s: public charging ended, %s",
                self.vehicle_name,
                f"priced with the {open_public.price_source} price ({open_public.kwh} kWh, {open_public.price})"
                if open_public.price_source in ("place", "zone", "operator", "default") else "waiting for price (log_public_charge service)",
            )
            if (smart := getattr(self, "smart", None)) is not None:
                # A message is an extra: a fault in it must not stop the ledger (the charge is already saved).
                smart._optional("public price question", lambda: smart.routines.ask_public_price(open_public))

    @staticmethod
    def _opened_just_before(charge: ChargeSession, now: datetime) -> bool:
        """A public session the car opened (not one logged by hand) within PUBLIC_BEFORE_HOME_MINUTES."""
        started = dt_util.parse_datetime(charge.started_at or "")
        return (charge.provider == "vehicle" and charge.ended_at is None and started is not None
                and now - started <= timedelta(minutes=PUBLIC_BEFORE_HOME_MINUTES))

    def _billed_cost(self, now: datetime, kwh: float | None, spot: bool, started: datetime | None = None):
        for provider in self._charger_providers.values():
            if CAP_COST_LOOKUP in provider.capabilities and (provider.provider_id == "spot_price") == spot:
                cost = provider.get_recent_session_cost(self.hass, now, known_kwh=kwh, session_start=started)
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
        cost = self._billed_cost(now, charge.kwh, spot=False, started=dt_util.parse_datetime(charge.started_at or ""))
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
            # The message when the whole charge is done; a fault in it must not stop the ledger.
            smart._optional("charge finished", lambda: smart.routines.charge_finished(charge))
        _LOGGER.info(
            "%s: home charging ended, %.2f kWh, %s %.2f",
            self.vehicle_name,
            charge.kwh or 0,
            charge.price_currency,
            charge.price or 0,
        )
