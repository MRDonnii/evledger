"""Keeps the charge plan up to date and, when a charger is configured, starts and stops it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from . import trip, vehicles
from .charger import ChargerBackend, create_backend
from .const import (
    ASSUMED_SOC,
    CONF_BATTERY_ENTITY,
    CONF_CAPACITY,
    CONF_CAR_PLUGGED_ENTITY,
    CONF_PRICE_ENTITIES,
    CONF_VEHICLE_MODEL,
    CONFIRM_TIMEOUT_MINUTES,
    DEFAULT_CAPACITY,
    DEFAULT_CONSUMPTION,
    DEFAULT_EFFICIENCY,
    DEFAULT_FIXED_END,
    DEFAULT_FIXED_START,
    DEFAULT_MIN_SOC,
    DEFAULT_POWER_KW,
    DEFAULT_PRICE_CAP,
    DEFAULT_PRICE_FACTOR,
    DEFAULT_READY_BY,
    DEFAULT_TARGET_SOC,
    DEFAULT_TRIP_MARGIN,
    DEFAULT_TRIP_RESERVE,
    INPUT_WAIT_SECONDS,
    START_DELAY_SECONDS,
    STARTUP_GRACE_SECONDS,
    STATUS_AWAITING_CONFIRMATION,
    STATUS_CHARGING,
    STATUS_DISCONNECTED,
    STATUS_DONE,
    STATUS_MANUAL,
    STATUS_NOT_RESPONDING,
    STATUS_OTHER_CAR,
    STATUS_PAUSED,
    STATUS_PLAN_ONLY,
    STATUS_STARTING,
    STATUS_STOPPED_EXTERNALLY,
    STATUS_UNKNOWN,
    STATUS_WAITING,
    UNPLUG_GRACE_SECONDS,
    VEHICLE_AUTO,
)
from .control import CONNECTED, Action, ChargerState, Controller
from .control import Event as ChargerEvent
from .phone import PhoneNotifier
from .plan import (
    DEFAULT_MODES,
    MODE_FIXED,
    MODE_MANUAL,
    MODE_NOW,
    MODE_OFF,
    MODE_PRICE_CAP,
    MODE_SMART,
    Constraint,
    PlanInput,
    PlanResult,
    Schedule,
    ScheduleInput,
    build_schedule,
    build_timeline,
    calculate,
    fixed_window,
    floor_quarter,
    next_deadline,
    parse_price_attributes,
)

_LOGGER = logging.getLogger(__name__)

PLUGGED_STATES = (STATE_ON, "true", "plugged", "connected", "plugged_in")
HOLD_MODES = (MODE_SMART, MODE_FIXED, MODE_PRICE_CAP)
LOOKUP_RETRY = timedelta(minutes=5)


@dataclass
class TripState:
    departure: datetime | None = None
    destination: str = ""
    round_trip: bool = True
    route: trip.Route | None = None
    error: str | None = None
    looking_up: bool = False
    last_lookup: datetime | None = None


class ChargePlanner:
    """Holds the user settings (owned by the number/time/select/... entities) and the latest plan."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 options: Callable[[], dict] | None = None,
                 vehicle: Callable[[], vehicles.Vehicle | None] | None = None) -> None:
        self.hass = hass
        self.entry = entry
        # EV Ledger supplies the settings from its own vehicle and charger setup.
        self._options = options or (lambda: dict(entry.options or entry.data))
        self._vehicle = vehicle
        self.settings: dict[str, float] = {
            "target_soc": DEFAULT_TARGET_SOC,
            "charge_power_kw": DEFAULT_POWER_KW,
            "efficiency": DEFAULT_EFFICIENCY,
            "price_factor": DEFAULT_PRICE_FACTOR,
            "price_cap": DEFAULT_PRICE_CAP,
            "min_soc": DEFAULT_MIN_SOC,
            "consumption": DEFAULT_CONSUMPTION,
            "trip_margin": DEFAULT_TRIP_MARGIN,
            "trip_reserve": DEFAULT_TRIP_RESERVE,
        }
        self.times: dict[str, time] = {
            "ready_by_time": time.fromisoformat(DEFAULT_READY_BY),
            "fixed_start": time.fromisoformat(DEFAULT_FIXED_START),
            "fixed_end": time.fromisoformat(DEFAULT_FIXED_END),
        }
        # The default plan runs when a car is plugged in; any other plan returns to it once it has run.
        self.default_mode = MODE_SMART
        self.mode = MODE_SMART
        self.mode_before_now = MODE_SMART
        # Set once the charger has been connected while a temporary plan (anything but the default plan
        # and manual) is chosen; unplugging after that returns to the default plan. Stored with the
        # select entity, so an unplug while Home Assistant was down is noticed too.
        self.now_seen_connected = False
        # Confirmation on the phone: on/off, and since when a new plan waits for an answer.
        self.confirm_enabled = False
        self.awaiting_since: datetime | None = None
        # Tell the phones which plan is active (when the car is plugged in or the plan changes).
        self.info_enabled = True
        self.trip = TripState()
        self.result = PlanResult(None, None, None, None, None, None)
        self.schedule = Schedule()
        self.alternatives: dict[str, Schedule] = {}
        self.deadline: datetime | None = None
        self.price_unit: str | None = None
        self.slot_count = 0
        self.status = STATUS_PLAN_ONLY
        self.charge_desired = False
        self.charger_state = ChargerState.UNKNOWN
        self.car_present = True
        self.backend: ChargerBackend | None = None
        self.controller = Controller(start_delay=timedelta(seconds=START_DELAY_SECONDS))
        self._hold_until: datetime | None = None
        self._recheck: CALLBACK_TYPE | None = None
        self._notify_pending = False
        self._info_pending = False
        self._disconnected_since: datetime | None = None
        self._alerted: set[str] = set()
        self._new_plug = False  # a car was plugged in while running (not just found plugged in at start-up)
        # The last battery level seen (restored with the charge mode), used while the car is not reporting.
        self.last_soc: float | None = None
        self.soc_assumed = False
        self.notify = PhoneNotifier(hass, entry, lambda: self.options)
        self._guessed: vehicles.Vehicle | None = None
        self._started_at: datetime | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []

    # -- configuration -----------------------------------------------------------------------

    @property
    def options(self) -> dict:
        return self._options()

    @property
    def battery_entity(self) -> str:
        return self.options[CONF_BATTERY_ENTITY]

    @property
    def price_entities(self) -> list[str]:
        return list(self.options.get(CONF_PRICE_ENTITIES) or [])

    @property
    def plugged_entity(self) -> str | None:
        return self.options.get(CONF_CAR_PLUGGED_ENTITY) or None

    @property
    def capacity(self) -> float:
        if self.options.get(CONF_CAPACITY):
            return float(self.options[CONF_CAPACITY])
        return self.vehicle.capacity_kwh if self.vehicle else DEFAULT_CAPACITY

    @property
    def vehicle(self) -> vehicles.Vehicle | None:
        """The chosen model, or for "automatic" the one that fits the car's device and range."""
        if self._vehicle and (known := self._vehicle()):
            return known
        choice = self.options.get(CONF_VEHICLE_MODEL, VEHICLE_AUTO)
        if choice != VEHICLE_AUTO:
            return vehicles.get(choice)
        if self._guessed is not None:
            return self._guessed
        vehicle, certain = self._guess_vehicle()
        if certain:
            self._guessed = vehicle  # range and battery level were known: keep it
        return vehicle

    def _guess_vehicle(self) -> tuple[vehicles.Vehicle | None, bool]:
        registry = er.async_get(self.hass)
        entry = registry.async_get(self.battery_entity)
        device = dr.async_get(self.hass).async_get(entry.device_id) if entry and entry.device_id else None
        if device is None:
            return None, True
        range_km = None
        for sibling in er.async_entries_for_device(registry, entry.device_id):
            if sibling.domain == "sensor" and sibling.entity_id.endswith("_range"):
                state = self.hass.states.get(sibling.entity_id)
                try:
                    range_km = float(state.state) if state else None
                except (TypeError, ValueError):
                    range_km = None
                if range_km is not None and state.attributes.get("unit_of_measurement") == "mi":
                    range_km *= 1.609344
                break
        soc = self._battery_soc()
        vehicle = vehicles.guess(device.model, range_km, soc)
        return vehicle, vehicle is None or (range_km is not None and soc is not None and soc >= 20)

    @property
    def ready_by(self) -> time:
        return self.times["ready_by_time"]

    @property
    def controls_charger(self) -> bool:
        return self.backend is not None

    # -- lifecycle -----------------------------------------------------------------------------

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def async_start(self) -> None:
        self._started_at = dt_util.utcnow()
        self.backend = create_backend(self.hass, self.options, self.plugged_entity)
        watched = [self.battery_entity, *self.price_entities]
        if self.plugged_entity:
            watched.append(self.plugged_entity)
        if self.backend:
            watched.extend(self.backend.entities)
        self._unsubs.append(async_track_state_change_event(self.hass, list(dict.fromkeys(watched)), self._on_state))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, timedelta(minutes=1)))
        self._unsubs.append(self.notify.async_listen(self))
        self.async_recalculate()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()
        if self._recheck:
            self._recheck()
            self._recheck = None

    @callback
    def _on_state(self, _event: Event) -> None:
        self.async_recalculate()

    @callback
    def _on_recheck(self, _now) -> None:
        self._recheck = None
        self.async_recalculate()

    @callback
    def _on_tick(self, _now) -> None:
        trip_state = self.trip
        if (trip_state.destination and trip_state.route is None and not trip_state.looking_up
                and trip_state.error == "lookup_failed" and trip_state.last_lookup
                and dt_util.utcnow() - trip_state.last_lookup >= LOOKUP_RETRY):
            self._start_lookup()
        self.async_recalculate()

    # -- setters used by the entities ----------------------------------------------------------

    @callback
    def async_set_setting(self, key: str, value: float) -> None:
        self.settings[key] = value
        self.async_recalculate()

    @callback
    def async_set_time(self, key: str, value: time) -> None:
        self.times[key] = value
        self.async_recalculate()

    @callback
    def async_set_ready_by(self, value: time) -> None:
        self.async_set_time("ready_by_time", value)

    @callback
    def async_set_mode(self, mode: str, restore: bool = False) -> None:
        if mode != self.mode and not restore:
            if mode == MODE_NOW:
                self.mode_before_now = self.mode
            self.now_seen_connected = False
            self.awaiting_since = None  # choosing a plan answers a pending confirmation
            if self.info_enabled and self.notify.targets and self.charger_state in CONNECTED and self.car_present:
                self._info_pending = True
        self.mode = mode
        self._hold_until = None
        if not restore:
            self.controller.reset()
        self.async_recalculate()

    @callback
    def async_set_default_mode(self, mode: str, restore: bool = False) -> None:
        """Choose the default plan. A plan that was running as the old default follows the new one."""
        if mode not in DEFAULT_MODES:
            return
        previous, self.default_mode = self.default_mode, mode
        if not restore and mode != previous and self.mode == previous:
            self.async_set_mode(mode)
        else:
            self.async_recalculate()

    @callback
    def async_set_trip_departure(self, value: datetime | None) -> None:
        self.trip.departure = value
        self.async_recalculate()

    @callback
    def async_set_trip_round_trip(self, value: bool) -> None:
        self.trip.round_trip = value
        self.async_recalculate()

    @callback
    def async_set_trip_destination(self, value: str, route: trip.Route | None = None) -> None:
        """Set the destination. A route restored from before a restart is used as is, without a lookup."""
        self.trip.destination = (value or "").strip()
        self.trip.route = route
        self.trip.error = None
        if self.trip.destination and route is None:
            self._start_lookup()
        self.async_recalculate()

    @callback
    def _start_lookup(self) -> None:
        self.trip.looking_up = True
        self.trip.last_lookup = dt_util.utcnow()
        self.entry.async_create_background_task(
            self.hass, self._async_lookup(self.trip.destination), "ev_smart_charge_route")

    @callback
    def async_set_info(self, enabled: bool) -> None:
        self.info_enabled = enabled
        self.async_recalculate()

    @callback
    def async_set_confirm(self, enabled: bool) -> None:
        self.confirm_enabled = enabled
        if not enabled:
            self.awaiting_since = None
        self.async_recalculate()

    @callback
    def async_answer(self, mode: str) -> None:
        """An answer from the phone or the card: confirm the cheapest plan, charge now or pause."""
        if self.awaiting_since is not None:
            # The question is answered: remove it from the other phones before a new plan message is sent.
            self.entry.async_create_background_task(
                self.hass, self.notify.async_clear(), "ev_smart_charge_notify_clear")
        self.awaiting_since = None
        if mode != self.mode:
            self.async_set_mode(mode)
        else:
            self.async_recalculate()

    @callback
    def async_clear_trip(self) -> None:
        self.trip = TripState()
        self.async_recalculate()

    # -- trip ------------------------------------------------------------------------------------

    def _home(self) -> tuple[float, float]:
        return self.hass.config.latitude, self.hass.config.longitude

    async def _async_lookup(self, text: str) -> None:
        route: trip.Route | None = None
        error: str | None = None
        try:
            route = await self._async_route(text)
            if route is None:
                error = "not_found"
        except Exception as err:  # noqa: BLE001 - network and parse errors all mean "no route"
            _LOGGER.warning("Route lookup for %s failed: %s", text, err)
            error = "lookup_failed"
        if text != self.trip.destination:
            return  # the destination changed while we were looking up
        self.trip.route, self.trip.error, self.trip.looking_up = route, error, False
        self.async_recalculate()

    async def _async_route(self, text: str) -> trip.Route | None:
        origin = self._home()
        name = text
        target: tuple[float, float] | None = None
        if text.startswith("zone.") and (zone := self.hass.states.get(text)):
            target = (zone.attributes["latitude"], zone.attributes["longitude"])
            name = zone.name
        elif coordinates := trip.parse_coordinates(text):
            target = coordinates
        session = async_get_clientsession(self.hass)
        if target is None:
            found = await trip.async_geocode(session, text, self.hass.config.language)
            if found is None:
                return None
            target, name = (found[0], found[1]), found[2]
        try:
            road = await trip.async_road_distance(session, origin, target)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Road distance failed, using straight line: %s", err)
            road = None
        if road:
            return trip.Route(round(road[0], 1), round(road[1]), name, target[0], target[1], "route")
        straight = trip.haversine_km(*origin, *target) * trip.DETOUR_FACTOR
        return trip.Route(round(straight, 1), None, name, target[0], target[1], "straight_line")

    @property
    def trip_active(self) -> bool:
        return self.trip.departure is not None

    @property
    def trip_energy_kwh(self) -> float | None:
        if self.trip.route is None:
            return None
        return round(trip.trip_energy_kwh(self.trip.route.distance_km, self.trip.round_trip,
                                          self.settings["consumption"], self.settings["trip_margin"]), 1)

    @property
    def trip_target_soc(self) -> float | None:
        energy = self.trip_energy_kwh
        if energy is None:
            return None
        return round(trip.trip_target_soc(energy, self.capacity, self.settings["trip_reserve"]), 1)

    # -- plan and control ------------------------------------------------------------------------

    def _battery_soc(self) -> float | None:
        state = self.hass.states.get(self.battery_entity)
        try:
            return float(state.state) if state else None
        except (TypeError, ValueError):
            return None

    def _car_present(self) -> bool:
        """False only when the car's own plug sensor says it is not plugged in (another car is)."""
        if not self.plugged_entity or (state := self.hass.states.get(self.plugged_entity)) is None:
            return True
        if state.state == STATE_OFF or state.state in ("false", "unplugged", "disconnected"):
            return False
        return state.state.lower() in PLUGGED_STATES or state.state in ("unknown", "unavailable")

    def constraints(self, now: datetime, mode: str | None = None) -> tuple[Constraint, ...]:
        target = self.settings["target_soc"]
        result = []
        if (mode or self.mode) in (MODE_SMART, MODE_MANUAL):
            result.append(Constraint(self.deadline, target))
        if self.trip_active and self.trip.departure > now:
            trip_target = self.trip_target_soc
            result.append(Constraint(self.trip.departure, min(max(target, trip_target or 0.0), 100.0)))
        return tuple(result)

    @callback
    def async_recalculate(self) -> None:
        now = dt_util.now()
        if self.trip.departure is not None and self.trip.departure <= now:
            _LOGGER.debug("Trip departure passed, clearing the temporary plan")
            self.trip = TripState()

        if self.backend:
            self.charger_state = self.backend.state()
            self.car_present = self._car_present()
            event = self.controller.observe(self.charger_state, now)
            self._handle_event(event, now)
            if self.charger_state == ChargerState.DISCONNECTED:
                self._disconnected_since = self._disconnected_since or now
            elif self.charger_state in CONNECTED:
                self._disconnected_since = None
            unplugged = (self._disconnected_since is not None
                         and now - self._disconnected_since >= timedelta(seconds=UNPLUG_GRACE_SECONDS))
            if self.mode not in (self.default_mode, MODE_MANUAL):
                if self.charger_state in CONNECTED:
                    self.now_seen_connected = True
                elif unplugged and self.now_seen_connected:
                    _LOGGER.debug("Car unplugged, %s has run, back to the default plan %s", self.mode,
                                  self.default_mode)
                    self.mode = self.default_mode
                    self.now_seen_connected = False
            if unplugged:
                self.awaiting_since = None
                self._new_plug = False
            elif self.awaiting_since and now - self.awaiting_since >= timedelta(minutes=CONFIRM_TIMEOUT_MINUTES):
                _LOGGER.debug("No answer on the phone, the plan runs")
                self.awaiting_since = None

        slots = []
        self.price_unit = None
        for entity_id in self.price_entities:
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            slots.extend(parse_price_attributes(dict(state.attributes)))
            unit = state.attributes.get("unit_of_measurement")
            if unit and not self.price_unit:
                self.price_unit = str(unit).split("/")[0].strip() or None
        unique = {slot.start: slot for slot in slots}
        ordered = [unique[key] for key in sorted(unique)]
        self.slot_count = len(ordered)
        self.deadline = next_deadline(now, self.ready_by)
        soc = self._battery_soc()
        self.soc_assumed = False
        if soc is not None:
            self.last_soc = soc
        elif self._inputs_waited(now):
            # The car is not reporting (asleep, cloud down): plan with what we know rather than not at all.
            soc = self.last_soc if self.last_soc is not None else ASSUMED_SOC
            self.soc_assumed = True
        self.result = calculate(PlanInput(
            soc=soc,
            target_soc=self.settings["target_soc"],
            capacity_kwh=self.capacity,
            efficiency=self.settings["efficiency"],
            power_kw=self.settings["charge_power_kw"],
            price_factor=self.settings["price_factor"],
            deadline=self.deadline,
            slots=ordered,
        ), now)

        window = fixed_window(now, self.times["fixed_start"], self.times["fixed_end"])
        constraints = self.constraints(now)
        horizon = max([self.deadline, window[1], *(c.deadline for c in constraints)])
        timeline = build_timeline(now, ordered, horizon)

        def plan_for(mode: str) -> Schedule:
            return build_schedule(ScheduleInput(
                mode=mode,
                soc=soc,
                target_soc=self.settings["target_soc"],
                capacity_kwh=self.capacity,
                efficiency=self.settings["efficiency"],
                power_kw=self.settings["charge_power_kw"],
                price_factor=self.settings["price_factor"],
                timeline=timeline,
                constraints=constraints if mode == self.mode else self.constraints(now, mode),
                window=window if mode == MODE_FIXED else None,
                price_cap=self.settings["price_cap"],
                min_soc=self.settings["min_soc"],
            ), now)

        self.schedule = plan_for(self.mode)
        # What the other plans would cost right now, so they can be compared before choosing.
        self.alternatives = {mode: self.schedule if mode == self.mode else plan_for(mode)
                             for mode in (MODE_NOW, MODE_SMART, MODE_FIXED, MODE_PRICE_CAP)}

        self._control(now)
        if self._info_pending and not self._notify_pending:
            self._info_pending = False
            self.entry.async_create_background_task(
                self.hass, self.notify.async_send_info(self), "ev_smart_charge_notify_info")
        if self._notify_pending:
            self._notify_pending = False
            self._info_pending = False
            self.entry.async_create_background_task(
                self.hass, self.notify.async_send_plan(self), "ev_smart_charge_notify")
        for update in list(self._listeners):
            update()

    def _inputs_waited(self, now: datetime) -> bool:
        return self._started_at is None or (dt_util.utcnow() - self._started_at).total_seconds() >= INPUT_WAIT_SECONDS

    @callback
    def _handle_event(self, event: ChargerEvent | None, now: datetime) -> None:
        if event is None:
            return
        _LOGGER.debug("Charger event %s (mode %s)", event, self.mode)
        # A charger that rebooted or lost its connection for a moment is not a car being plugged in.
        new_plug = event == ChargerEvent.PLUGGED and (
            self._disconnected_since is None
            or now - self._disconnected_since >= timedelta(seconds=UNPLUG_GRACE_SECONDS))
        if event == ChargerEvent.UNPLUGGED:
            self._hold_until = None
        elif event == ChargerEvent.PLUGGED and new_plug:
            self._alerted.clear()
            self._new_plug = True
            self._refresh_car()
        if not new_plug and event == ChargerEvent.PLUGGED:
            return
        if event == ChargerEvent.PLUGGED and self.car_present and self.mode != MODE_MANUAL:
            if self.confirm_enabled and self.notify.targets:
                self.awaiting_since = dt_util.now()
                self._notify_pending = True
            elif self.info_enabled and self.notify.targets:
                self._info_pending = True
        elif (event == ChargerEvent.MANUAL_START and self.car_present and not self.charge_desired
              and self.mode not in (MODE_NOW, MODE_MANUAL)):
            # Started from the charger's app or the car while the plan did not want to charge:
            # follow the user and charge now.
            self.mode_before_now, self.mode = self.mode, MODE_NOW
            self.now_seen_connected = True
            self.controller.last_desired = True

    def desired(self, now: datetime) -> bool:
        """Should the car charge right now according to the plan."""
        if self.mode in (MODE_OFF, MODE_MANUAL) or self.awaiting_since:
            return False
        if self.schedule.charge_now:
            if self.charger_state == ChargerState.CHARGING and self.mode in HOLD_MODES:
                # Keep going to the end of the quarter, so small plan changes do not toggle the charger.
                self._hold_until = floor_quarter(now) + timedelta(minutes=15)
            return True
        if (self._hold_until and now < self._hold_until and self.mode in HOLD_MODES
                and self.schedule.energy_kwh > 0 and self.charger_state == ChargerState.CHARGING):
            return True
        self._hold_until = None
        return False

    @callback
    def _control(self, now: datetime) -> None:
        desired = self.charge_desired = self.desired(now)
        if self.backend is None:
            self.status = STATUS_PLAN_ONLY
            return
        state = self.charger_state
        self.status = self._status(state, desired)
        self._alert(now)
        # "Charge now" charges whichever car is plugged in; the plans only the car they belong to.
        if self.mode == MODE_MANUAL or (not self.car_present and self.mode != MODE_NOW):
            return
        if (self.mode != MODE_NOW and (self._battery_soc() is None or not self.slot_count)
                and not self._inputs_waited(now)):
            # Right after a start the car or the price sensor may not be loaded yet: leave the charger
            # as it is for a while instead of acting on a plan made without them.
            return
        if self._started_at and (dt_util.utcnow() - self._started_at).total_seconds() < STARTUP_GRACE_SECONDS:
            return
        action = self.controller.decide(desired, now)
        if action == Action.NONE:
            if desired and (wait := self.controller.start_wait(now)) and self.charger_state != ChargerState.CHARGING:
                self.status = STATUS_STARTING
                if self._recheck is None:
                    self._recheck = async_call_later(self.hass, wait.total_seconds() + 1, self._on_recheck)
            return
        _LOGGER.info("%s: %s charging (mode %s, charger %s)", self.entry.title, action, self.mode, state)
        if action == Action.START and self.controller.start_attempts <= self.controller.max_start_attempts:
            self.status = STATUS_STARTING  # the slow retries after that keep showing "not responding"
        self.entry.async_create_task(self.hass, self.backend.async_command(action, state), "ev_smart_charge_command")

    @callback
    def _refresh_car(self) -> None:
        """Ask the car's integration for fresh data when the cable goes in (plug and battery level)."""
        entities = [entity for entity in (self.plugged_entity, self.battery_entity) if entity]
        if entities and self.hass.services.has_service("homeassistant", "update_entity"):
            self.entry.async_create_background_task(
                self.hass,
                self.hass.services.async_call("homeassistant", "update_entity", {"entity_id": entities}),
                "ev_smart_charge_refresh_car")

    @callback
    def _alert(self, now: datetime) -> None:
        """Tell the phones once when charging cannot follow the plan."""
        if self.status in (STATUS_CHARGING, STATUS_DISCONNECTED, STATUS_DONE):
            self._alerted.clear()
            return
        if not (self.info_enabled and self.notify.targets):
            return
        alerts = {
            STATUS_NOT_RESPONDING: ("Laderen svarer ikke. Opladningen er ikke startet; "
                                    "der prøves igen hvert kvarter."),
            STATUS_OTHER_CAR: ("Laderen er tilsluttet, men det er ikke denne bil. "
                               "Tryk Lad nu for at lade den alligevel."),
        }
        if self.status == STATUS_STOPPED_EXTERNALLY and self.controller.respects_stop:
            alerts[STATUS_STOPPED_EXTERNALLY] = ("Opladningen blev stoppet af bilen eller appen og startes ikke igen "
                                                 "af sig selv. Tryk Lad nu for at fortsætte.")
        if (text := alerts.get(self.status)) and self.status not in self._alerted:
            if self.status == STATUS_OTHER_CAR and not self._new_plug:
                return  # only when a car is plugged in, not at every restart while it stands there
            self._alerted.add(self.status)
            self.entry.async_create_background_task(
                self.hass, self.notify.async_send_alert(text), "ev_smart_charge_notify_alert")

    def _status(self, state: ChargerState, desired: bool) -> str:
        if self.mode == MODE_MANUAL:
            return STATUS_MANUAL
        if self.awaiting_since and state in CONNECTED:
            return STATUS_AWAITING_CONFIRMATION
        if state == ChargerState.DISCONNECTED:
            return STATUS_DISCONNECTED
        if state == ChargerState.UNKNOWN:
            return STATUS_UNKNOWN
        if not self.car_present and self.mode != MODE_NOW:
            return STATUS_OTHER_CAR
        if state == ChargerState.CHARGING:
            return STATUS_CHARGING
        if desired and self.controller.blocked:
            return STATUS_STOPPED_EXTERNALLY
        if desired and self.controller.gave_up and state != ChargerState.CHARGING:
            return STATUS_NOT_RESPONDING
        if self.mode == MODE_OFF:
            return STATUS_PAUSED
        if desired:
            return STATUS_STARTING
        if self._battery_soc() is None and not self.soc_assumed:
            return STATUS_UNKNOWN  # not "target reached" just because the car has not reported yet
        if self.schedule.energy_kwh <= 0 and self.mode != MODE_NOW:
            return STATUS_DONE
        return STATUS_WAITING
