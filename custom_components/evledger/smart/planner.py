"""Keeps the charge plan up to date and, when a charger is configured, starts and stops it."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from ..device_resolve import all_devices
from . import co2 as grid_co2
from . import departures as learned_departures
from . import share as charger_share
from . import trip, vehicles
from . import week as week_plan
from .car_refresh import CarRefresh, async_update_entities
from .charger import ChargerBackend, create_backend
from .const import (
    ASSUMED_SOC,
    CAR_REPORT_GRACE_SECONDS,
    CONF_BATTERY_ENTITY,
    CONF_CAPACITY,
    CONF_CAR_PLUGGED_ENTITY,
    CONF_CAR_TRACKER,
    CONF_PRICE_ENTITIES,
    CONF_VEHICLE_MODEL,
    CONFIRM_TIMEOUT_MINUTES,
    DEFAULT_ASK_MIN_SAVING,
    DEFAULT_CAPACITY,
    DEFAULT_CONSUMPTION,
    DEFAULT_EFFICIENCY,
    DEFAULT_FIXED_END,
    DEFAULT_FIXED_START,
    DEFAULT_HEAT_HOLD_PRICE,
    DEFAULT_LOW_PRICE,
    DEFAULT_MIN_SOC,
    DEFAULT_POWER_KW,
    DEFAULT_PRECONDITION_MINUTES,
    DEFAULT_PRICE_CAP,
    DEFAULT_PRICE_FACTOR,
    DEFAULT_QUIET_FROM,
    DEFAULT_QUIET_UNTIL,
    DEFAULT_READY_BY,
    DEFAULT_READY_BY_WEEKEND,
    DEFAULT_READY_MARGIN,
    DEFAULT_REMINDER_SOC,
    DEFAULT_REMINDER_TIME,
    DEFAULT_SMART_FLOOR_SOC,
    DEFAULT_SMART_MAX_PRICE,
    DEFAULT_TARGET_SOC,
    DEFAULT_TRIP_MARGIN,
    DEFAULT_TRIP_RESERVE,
    DEFAULT_WAKE_MINUTES,
    FORECAST_MARGIN,
    HEAT_HOLD_MARGIN,
    INPUT_WAIT_SECONDS,
    LIMIT_LOOK_HOURS,
    MIN_USE_HISTORY_DAYS,
    NOW_DONE_MINUTES,
    OUTAGE_MIN_MINUTES,
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
    USE_DAYS,
    VEHICLE_AUTO,
    WAIT_MAX_DAYS,
    WAIT_MIN_KR,
    WAIT_MIN_SHARE,
    WAIT_RESERVE,
)
from .control import CONNECTED, Action, ChargerState, Controller
from .control import Event as ChargerEvent
from .phone import PhoneNotifier
from .plan import (
    DEFAULT_MODES,
    HOUR_MINUTES,
    MODE_FIXED,
    MODE_MANUAL,
    MODE_NOW,
    MODE_OFF,
    MODE_PRICE_CAP,
    MODE_SMART,
    RESOLUTION_HOUR,
    RESOLUTION_QUARTER,
    RESOLUTIONS,
    SLOT_MINUTES,
    Constraint,
    PlanInput,
    PlanResult,
    Schedule,
    ScheduleInput,
    build_schedule,
    build_timeline,
    calculate,
    cheaper_later,
    finite,
    fixed_window,
    floor_slot,
    next_deadline,
    parse_price_attributes,
)
from .routines import Routines
from .setup import is_forecast

_LOGGER = logging.getLogger(__name__)

# The questions to the phones that are asked once per plug-in, whatever settings change after them.
ASKED_ONCE = frozenset({"price", "cap"})

PLUGGED_STATES = (STATE_ON, "true", "plugged", "connected", "plugged_in")
# The car's own charge limit (a number on the car's device): Tesla Custom "_charge_limit", Tesla Fleet,
# Teslemetry and Tessie "charge_state_charge_limit_soc".
CHARGE_LIMIT_SUFFIXES = ("_charge_limit", "charge_limit_soc")
# The car's own plug sensor: Tesla Fleet / Teslemetry / Tessie "charge cable", Tesla Custom "charger".
CAR_PLUG_SUFFIXES = ("charge_state_conn_charge_cable", "_charger")
# The car's own "full at" time: Tesla Custom, and Tesla Fleet / Teslemetry / Tessie.
CAR_FULL_SUFFIXES = ("_time_charge_complete", "charge_state_minutes_to_full_charge")
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
    # "calendar" when the trip comes from an event in the trip calendar (then kept in step with the event).
    source: str = ""
    event_key: str = ""
    event_start: datetime | None = None


class ChargePlanner:
    """Holds the user settings (owned by the number/time/select/... entities) and the latest plan."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 options: Callable[[], dict] | None = None,
                 vehicle: Callable[[], vehicles.Vehicle | None] | None = None,
                 open_charge: Callable[[], bool] | None = None,
                 charges: Callable[[], list] | None = None,
                 trips: Callable[[], list] | None = None) -> None:
        self.hass = hass
        self.entry = entry
        # EV Ledger supplies the settings from its own vehicle and charger setup.
        self._options = options or (lambda: dict(entry.options or entry.data))
        self._vehicle = vehicle
        # Whether the ledger has a home charging session open (its kWh and price come when it closes).
        self.open_charge = open_charge or (lambda: False)
        # The ledger's charges (home and public), for the monthly summary.
        self.charges = charges or (lambda: [])
        # The ledger's trips: the daily driving ("wait for a cheaper day") and the departure times it learns.
        self.trips = trips or (lambda: [])
        # The ledger itself (set by EV Ledger), for a public charge's price answered on the phone.
        self.ledger = None
        self.settings: dict[str, float] = {
            "target_soc": DEFAULT_TARGET_SOC,
            "charge_power_kw": DEFAULT_POWER_KW,
            "efficiency": DEFAULT_EFFICIENCY,
            "price_factor": DEFAULT_PRICE_FACTOR,
            "price_cap": DEFAULT_PRICE_CAP,
            "min_soc": DEFAULT_MIN_SOC,
            "smart_max_price": DEFAULT_SMART_MAX_PRICE,
            "smart_floor_soc": DEFAULT_SMART_FLOOR_SOC,
            "ask_min_saving": DEFAULT_ASK_MIN_SAVING,
            "ready_margin": DEFAULT_READY_MARGIN,
            "consumption": DEFAULT_CONSUMPTION,
            "trip_margin": DEFAULT_TRIP_MARGIN,
            "trip_reserve": DEFAULT_TRIP_RESERVE,
            "reminder_soc": DEFAULT_REMINDER_SOC,
            "precondition_minutes": DEFAULT_PRECONDITION_MINUTES,
            "low_price": DEFAULT_LOW_PRICE,
            "heat_hold_price": DEFAULT_HEAT_HOLD_PRICE,
        }
        self.times: dict[str, time] = {
            "ready_by_time": time.fromisoformat(DEFAULT_READY_BY),
            "ready_by_weekend": time.fromisoformat(DEFAULT_READY_BY_WEEKEND),
            "fixed_start": time.fromisoformat(DEFAULT_FIXED_START),
            "fixed_end": time.fromisoformat(DEFAULT_FIXED_END),
            "reminder_time": time.fromisoformat(DEFAULT_REMINDER_TIME),
            "quiet_from": time.fromisoformat(DEFAULT_QUIET_FROM),
            "quiet_until": time.fromisoformat(DEFAULT_QUIET_UNTIL),
        }
        # On/off settings: the message when a charge is done, the evening reminder, another ready-by time at the
        # weekend and the car's climate before the ready-by time.
        self.flags: dict[str, bool] = {"notify_start": True, "notify_done": True,
                                       "plug_reminder": True, "weekend_ready_by": False,
                                       "precondition": False, "learn": True, "monthly_summary": True,
                                       "low_price_alert": False, "wait_cheaper_day": False,
                                       "learn_departure": False, "prefer_green": False,
                                       "ask_public_price": False, "morning_check": False, "quiet_night": True,
                                       "week_plan": False, "heat_hold": False,
                                       "tyre_reminder": False, "smart_price_limit": False}
        # Learned departure times ({"history_days", "times"}), the cheaper day waited for, the grid's CO2 forecast,
        # and the ready-by time that just passed with the level the plan aimed at (for the morning check).
        self.learned_departures: dict = {"history_days": 0, "times": None}
        # The week plan: a ready-by time (and, if wanted, its own target) per weekday, or a free day. Used instead of
        # the ready-by time (and the learned departures) while switched on.
        self.week: dict[int, dict | None] = week_plan.default_week(self.times["ready_by_time"])
        self.week_set = False  # set by hand or restored; else made from the ready-by times when it is switched on
        # A one-time ready-by time ({"at": datetime, "target": float | None}): the next deadline until it has passed,
        # then the normal ready-by times again.
        self.once: dict | None = None
        # What decides the next ready-by time ("once", "week", "learned" or "time") and that deadline's own target.
        self.deadline_source = "time"
        self.deadline_target: float | None = None
        # A target of its own (a day of the week plan, the one-time time) is also set as the car's charge limit while
        # the car is plugged in; afterwards the car gets the normal target back.
        self._limit_goal: float | None = None
        self._limit_tried: tuple | None = None
        self._later_targets: dict[datetime, float | None] = {}
        # Keeping the battery at the target while the car heats on the charger: holding now, and why not (for the card).
        self.heat_holding = False
        self.heat_hold_reason: str | None = None
        self.waiting: dict | None = None
        self.regular_deadline: datetime | None = None
        self.co2: dict[datetime, float] = {}
        self.co2_area: str | None = None
        self.co2_updated: datetime | None = None
        self._co2_task = None
        self.passed_deadline: datetime | None = None
        self.passed_target: float | None = None
        self._deadline_target: float | None = None
        # The default plan runs when a car is plugged in; any other plan returns to it once it has run.
        self.default_mode = MODE_SMART
        # Plan in quarters or in whole hours (the mean price of the hour).
        self.resolution = RESOLUTION_QUARTER
        self.mode = MODE_SMART
        self.mode_before_now = MODE_SMART
        # Set once the charger has been connected while a temporary plan (anything but the default plan
        # and manual) is chosen; unplugging after that returns to the default plan. Stored with the
        # select entity, so an unplug while Home Assistant was down is noticed too.
        self.now_seen_connected = False
        # "Charge now" has charged in this run, and since when it has stood finished at the target: then the default
        # plan takes over at once, so a small drop of the battery is topped up by it and not right away at any price.
        self._now_charged = False
        self._now_done_since: datetime | None = None
        # Confirmation on the phone: on/off, and since when a new plan waits for an answer.
        self.confirm_enabled = False
        self.awaiting_since: datetime | None = None
        # Tell the phones which plan is active (when the car is plugged in or the plan changes).
        self.info_enabled = True
        # Price cap: slots above the cap may be used to reach the target in time (until the phone says no).
        self.cap_override = True
        # Price limit on the cheapest plan (user, 2026-10-15: "i stedet for det der lige er bygget"): the plan charges
        # to the target; when part of it costs more than the limit and a later period is cheaper, the phones are asked
        # whether to wait with that part. True = only below the limit for this plug-in (answered "Vent").
        self.limit_wait = False
        self.wait_params = None
        self.limit_view: dict | None = None
        self._limit_params: tuple[float, float] | None = None
        # The answer "Lad kun til X %" fixed when it was given (keep at least %, max price): the plan keeps to that
        # level for the plug-in, also when the question cannot be worked out again for a while (just restarted, the
        # car's level not known yet – 16 Oct 2026 the plan then went back to the target). Restored after a restart.
        self.wait_params: tuple[float, float] | None = None
        self.wait_soc: float | None = None
        # Warnings already sent for this plug-in and these settings (target out of reach, price cap).
        self.warned: set[str] = set()
        self._limit_id: str | None = None
        self._full_ids: list[str] | None = None
        self._plug_ids: list[str] | None = None
        # Several cars on one charger: the share decides which of them is on it.
        self.share: charger_share.ChargerShare | None = None
        self.trip = TripState()
        self.result = PlanResult(None, None, None, None, None, None)
        self.schedule = Schedule()
        # The price slots the plan was made from (quarters or hours, from the current slot to the plan's horizon).
        self.timeline: list = []
        self.horizon: datetime | None = None
        self.alternatives: dict[str, Schedule] = {}
        # The extras (questions, messages, learned times, comparisons) whose fault was logged: an extra that fails
        # must never stop the plan from controlling the charger (logged once, see _optional).
        self._faults: set[str] = set()
        # Home Assistant was away (from, until) while a planned period should have started: told once (routines).
        self.outage: tuple[datetime, datetime] | None = None
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
        self._plug_told = False  # the plugged-in message (plan or confirmation) has been queued for this plug-in
        self._other_car_since: datetime | None = None
        # The last battery level seen (restored with the charge mode), used while the car is not reporting.
        self.last_soc: float | None = None
        self.soc_assumed = False
        # The charging periods of the last plan (kept across a restart): followed until the prices are
        # loaded again, so a restart right at the planned start does not lose the start.
        self.restored_blocks: list[tuple[datetime, datetime]] = []
        self.notify = PhoneNotifier(hass, entry, lambda: self.options)
        # Silent messages at night: on/off and the clock times.
        self.notify.quiet_window = lambda: (self.flags["quiet_night"], self.times["quiet_from"],
                                            self.times["quiet_until"])
        self.routines = Routines(self)
        self._guessed: vehicles.Vehicle | None = None
        self._started_at: datetime | None = None
        # Fetches the car's data again when the car integrations have none (after a restart).
        self.car_refresh = CarRefresh(self)
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
                range_km = finite(state.state) if state else None
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
    def ready_by_weekend(self) -> time | None:
        """The ready-by time on Saturdays and Sundays, when it differs from the workdays'."""
        return self.times["ready_by_weekend"] if self.flags["weekend_ready_by"] else None

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
        # The car's own plug sensors and location tell which car is on a shared charger.
        watched.extend(self._plug_entities())
        if tracker := self.options.get(CONF_CAR_TRACKER):
            watched.append(tracker)
        if self.backend:
            watched.extend(self.backend.entities)
        # The car's own charge limit caps the target: a new limit (also one set from the target) re-plans at once.
        if limit := self._limit_entity():
            watched.append(limit)
        # The car's climate: keeping the battery at the target while it heats follows it at once.
        if climate := self.routines.climate_entity():
            watched.append(climate)
        self._unsubs.append(async_track_state_change_event(self.hass, list(dict.fromkeys(watched)), self._on_state))
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, timedelta(minutes=1)))
        self._unsubs.append(self.notify.async_listen(self))
        # Act as soon as the first minute after a start is over, not at the next minute tick.
        self._unsubs.append(async_call_later(self.hass, STARTUP_GRACE_SECONDS + 1, self._on_recheck))
        self.share = charger_share.join(self.hass, self)
        self.async_recalculate()

    @callback
    def async_stop(self) -> None:
        charger_share.leave(self.hass, self, self.share)
        self.share = None
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
        self.car_refresh.check(dt_util.utcnow(), self._started_at, self.charger_state in CONNECTED)
        trip_state = self.trip
        if (trip_state.destination and trip_state.route is None and not trip_state.looking_up
                and trip_state.error == "lookup_failed" and trip_state.last_lookup
                and dt_util.utcnow() - trip_state.last_lookup >= LOOKUP_RETRY):
            self._start_lookup()
        now = dt_util.now()
        if self.routines.calendar_due(now):
            self.entry.async_create_background_task(self.hass, self.routines.async_calendar(now),
                                                    "ev_smart_charge_calendar")
        if self._co2_due():
            self._co2_task = self.entry.async_create_background_task(self.hass, self.async_refresh_co2(),
                                                                     "ev_smart_charge_co2")
        self.async_recalculate()
        self.routines.evening(now)
        self.routines.precondition(now)
        self.routines.monthly(now)
        self.routines.morning_check(now)

    # -- setters used by the entities ----------------------------------------------------------

    @callback
    def async_set_setting(self, key: str, value: float) -> None:
        if (value := finite(value)) is None:
            return  # NaN or infinity (from a script or a broken restore): the setting stays as it was
        if self.settings.get(key) != value:
            self._rewarn()
        self.settings[key] = value
        self.async_recalculate()

    @callback
    def async_set_time(self, key: str, value: time) -> None:
        if self.times.get(key) != value:
            self._rewarn()
        self.times[key] = value
        self.async_recalculate()

    @callback
    def async_set_ready_by(self, value: time) -> None:
        self.async_set_time("ready_by_time", value)

    @callback
    def async_set_week(self, week: dict[int, dict | None], enabled: bool | None = None) -> None:
        """A new week plan (and, if given, switch it on or off)."""
        self._rewarn()
        self.week = week
        self.week_set = True
        if enabled is not None:
            self.flags["week_plan"] = enabled
        self.async_recalculate()

    @callback
    def async_set_once(self, at: datetime | None, target: float | None = None) -> None:
        """Set (or with None clear) the one-time ready-by time."""
        self._rewarn()
        self.once = {"at": at.replace(second=0, microsecond=0), "target": target} if at is not None else None
        self.async_recalculate()

    @callback
    def async_set_mode(self, mode: str, restore: bool = False) -> None:
        if (mode == MODE_NOW and not restore and self.shared and self.charger_state in CONNECTED
                and self.share.owner() is not self):
            # "Charge now" on a car that is not told to be on the shared charger: it is this one.
            self.share.forced = self
            self.car_present = True
            self._took_charger()
            self.share.check()
        if mode != self.mode and not restore:
            if mode == MODE_NOW:
                self.mode_before_now = self.mode
            self.now_seen_connected = False
            self.awaiting_since = None  # choosing a plan answers a pending confirmation
            self.cap_override = True
            self.limit_wait = False
            self.wait_params = None
            self.warned.clear()
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

    @property
    def slot_minutes(self) -> int:
        return HOUR_MINUTES if self.resolution == RESOLUTION_HOUR else SLOT_MINUTES

    @callback
    def async_set_resolution(self, value: str, restore: bool = False) -> None:
        """Quarters or whole hours: the plan (and the price cards that follow it) change at once."""
        if value not in RESOLUTIONS:
            return
        if value != self.resolution and not restore:
            self._rewarn()
            self._hold_until = None
        self.resolution = value
        self.async_recalculate()

    def _rewarn(self) -> None:
        """New settings: warn again if they cannot be met either (not while restoring after a restart). The questions
        (wait with the dear part, go over the price cap) are asked once per plug-in: a new setting, or a number
        stepped up and down, does not ask them again (2026-10-15: a limit moved 2.2 → 1.7 → 2.1 → 2.0 could ask
        three times in seconds, also after an answer)."""
        if self._started_at is not None:
            self.warned &= ASKED_ONCE

    @callback
    def async_set_cap_override(self, value: bool) -> None:
        """Allow (or not) charging above the price cap to reach the target in time."""
        self.cap_override = value
        self.warned.add("cap")  # answered: not asked again for this plug-in
        self.async_recalculate()

    @callback
    def async_set_limit_wait(self, value: bool) -> None:
        """Wait with the energy above the price limit (charge only below it) this plug-in, or charge to the target."""
        self.limit_wait = value
        self.wait_params = (self._limit_params or self.wait_params) if value else None
        self.warned.add("price")  # answered: not asked again for this plug-in
        self.async_recalculate()

    @callback
    def async_set_flag(self, key: str, value: bool) -> None:
        if self.flags.get(key) != value and key in ("weekend_ready_by", "week_plan"):
            self._rewarn()
        if key == "week_plan" and value and not self.week_set:
            # Switched on for the first time: start from the ready-by times used so far.
            self.week = week_plan.default_week(self.ready_by, self.ready_by_weekend)
            self.week_set = True
        self.flags[key] = value
        if key == "learn" and value:
            self.routines.apply_learned()
        self.async_recalculate()

    @callback
    def async_set_trip_departure(self, value: datetime | None, manual: bool = True) -> None:
        self._rewarn()
        self.trip.departure = value
        if manual:
            self.trip.source = ""  # set by hand: the calendar no longer moves it
        self.async_recalculate()

    @callback
    def async_set_trip_round_trip(self, value: bool) -> None:
        self._rewarn()
        self.trip.round_trip = value
        self.async_recalculate()

    @callback
    def async_set_trip_destination(self, value: str, route: trip.Route | None = None, manual: bool = True) -> None:
        """Set the destination. A route restored from before a restart is used as is, without a lookup."""
        if manual and (value or "").strip() != self.trip.destination:
            self.trip.source = ""
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
        if self.trip.source == "calendar":
            self.routines.dismissed.add(self.trip.event_key)  # cleared by hand: the event is not added again
        self.trip = TripState()
        self.async_recalculate()

    @callback
    def async_calendar_trip(self, event: dict | None) -> None:
        """The next trip in the calendar: becomes the temporary plan unless one was set by hand."""
        current = self.trip
        if event is None:
            if current.source == "calendar":
                _LOGGER.debug("The calendar event %s is gone, clearing its trip", current.event_key)
                self.trip = TripState()
                self.async_recalculate()
            return
        if current.departure is not None and current.source != "calendar":
            return  # a trip set by hand wins
        if current.source == "calendar" and current.event_key == event["key"]:
            if current.destination != event["location"]:
                self.async_set_trip_destination(event["location"], manual=False)
            self._calendar_departure()
            self.async_recalculate()
            return
        departure = self.routines.departure(event["start"], None)
        if departure <= dt_util.now():
            return
        _LOGGER.debug("Trip from the calendar: %s at %s", event["summary"], event["start"])
        self._rewarn()
        self.trip = TripState(departure=departure, source="calendar", event_key=event["key"],
                              event_start=event["start"])
        self.async_set_trip_destination(event["location"], manual=False)

    def _calendar_departure(self) -> None:
        """A trip from the calendar leaves early enough to be there at the event's start."""
        current = self.trip
        if current.source != "calendar" or current.event_start is None:
            return
        departure = self.routines.departure(current.event_start, current.route.duration_min if current.route else None)
        if departure > dt_util.now():
            current.departure = departure

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
        self._calendar_departure()
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
        return finite(state.state) if state else None

    @property
    def shared(self) -> bool:
        """Other EV Ledger cars use the same charger."""
        return bool(self.share and self.share.shared)

    def _plug_entities(self) -> list[str]:
        """The car's own plug sensors on this car only (the battery sensor's device or a device with the same name)."""
        if self._plug_ids is None:
            registry = er.async_get(self.hass)
            devices = dr.async_get(self.hass)
            battery = registry.async_get(self.battery_entity)
            car = devices.async_get(battery.device_id) if battery is not None and battery.device_id else None
            found: list[str] = []
            if car is not None:
                name = car.name_by_user or car.name
                same_car = [device.id for device in all_devices(devices)
                            if device.id == car.id or (name and (device.name_by_user or device.name) == name)]
                found = [other.entity_id for device_id in same_car
                         for other in er.async_entries_for_device(registry, device_id)
                         if other.domain == "binary_sensor" and not other.disabled_by
                         and other.unique_id.endswith(CAR_PLUG_SUFFIXES)]
            self._plug_ids = found
        return self._plug_ids

    def car_plug_state(self) -> tuple[bool | None, datetime | None]:
        """Whether the car itself says it is plugged in (the chosen plug sensor, else its own ones), and since when."""
        ids = list(dict.fromkeys([*([self.plugged_entity] if self.plugged_entity else []), *self._plug_entities()]))
        known: list[tuple[bool, datetime]] = []
        for entity_id in ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable", ""):
                continue
            known.append((state.state.lower() in PLUGGED_STATES, state.last_changed))
        if not known:
            return None, None
        plugged = [changed for value, changed in known if value]
        if plugged:
            return True, max(plugged)
        return False, max(changed for _, changed in known)

    def _car_present(self) -> bool:
        """False only when the car's own plug sensors all say it is not plugged in (another car is). One of them
        saying plugged in is enough: the car's integrations do not update at the same time, and a sleeping car leaves
        one of them on "disconnected" for hours. With several cars on the charger: only the car that is on it."""
        if self.shared:
            return self.share.owner() is self
        if not self.plugged_entity:
            return True
        return self.car_plug_state()[0] is not False

    def _limit_entity(self) -> str | None:
        """The car's charge limit entity on the battery sensor's device, found once (again after a plug-in)."""
        if self._limit_id is None:
            self._limit_id = ""
            registry = er.async_get(self.hass)
            battery = registry.async_get(self.battery_entity)
            if battery is not None and battery.device_id is not None:
                for other in er.async_entries_for_device(registry, battery.device_id):
                    if (other.domain == "number" and not other.disabled_by
                            and other.unique_id.endswith(CHARGE_LIMIT_SUFFIXES)):
                        self._limit_id = other.entity_id
                        break
        return self._limit_id or None

    def _limit_command_entity(self) -> str | None:
        """The charge limit to send a new limit to: on the car's device or a device with the same name, preferring
        an integration that can command the car (Tesla Fleet, Teslemetry, Tessie) over Tesla Custom, whose owner
        API commands newer cars ignore."""
        registry = er.async_get(self.hass)
        devices = dr.async_get(self.hass)
        battery = registry.async_get(self.battery_entity)
        car = devices.async_get(battery.device_id) if battery is not None and battery.device_id else None
        if car is None:
            return self._limit_entity()
        name = car.name_by_user or car.name
        same_car = [device.id for device in all_devices(devices)
                    if device.id == car.id or (name and (device.name_by_user or device.name) == name)]
        found = [other for device_id in same_car for other in er.async_entries_for_device(registry, device_id)
                 if other.domain == "number" and not other.disabled_by
                 and other.unique_id.endswith(CHARGE_LIMIT_SUFFIXES)
                 and (state := self.hass.states.get(other.entity_id)) is not None and state.state != "unavailable"]
        found.sort(key=lambda entry: entry.platform == "tesla_custom")
        return found[0].entity_id if found else self._limit_entity()

    async def async_sync_car_limit(self, target: float) -> None:
        """Set the car's own charge limit to a new target, so the car does not stop below it (or charge past it
        on its own). Clamped to what the car accepts (Tesla: 50–100 %)."""
        if not (entity_id := self._limit_command_entity()) or (state := self.hass.states.get(entity_id)) is None:
            return
        low, high = finite(state.attributes.get("min", 50)), finite(state.attributes.get("max", 100))
        if low is None or high is None or low > high:
            low, high = 50.0, 100.0
        if (value := finite(target)) is None:
            return
        value = min(max(value, low), high)
        if (now := finite(state.state)) is not None and abs(now - value) < 0.5:
            return  # (unknown while the car sleeps: set it anyway)
        try:
            await self.hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value},
                                                blocking=True)
            self.async_recalculate()
        except Exception as err:  # noqa: BLE001 - the car may be asleep or offline; the plan keeps its own target
            _LOGGER.warning("Could not set the car's charge limit %s to %s %%: %s", entity_id, value, err)

    def car_limit(self) -> float | None:
        """The charge limit set in the car: it stops there whatever the plan says."""
        if not (entity_id := self._limit_entity()) or (state := self.hass.states.get(entity_id)) is None:
            return None
        value = finite(state.state)
        return value if value is not None and 50 <= value <= 100 else None

    def _full_entities(self) -> list[str]:
        """The car's own "full at" time sensors on this car only (the battery sensor's device or a device with the
        same name): Tesla Custom, Tesla Fleet, Teslemetry, Tessie."""
        if self._full_ids is None:
            registry = er.async_get(self.hass)
            devices = dr.async_get(self.hass)
            battery = registry.async_get(self.battery_entity)
            car = devices.async_get(battery.device_id) if battery is not None and battery.device_id else None
            found: list[str] = []
            if car is not None:
                name = car.name_by_user or car.name
                same_car = [device.id for device in all_devices(devices)
                            if device.id == car.id or (name and (device.name_by_user or device.name) == name)]
                found = [other.entity_id for device_id in same_car
                         for other in er.async_entries_for_device(registry, device_id)
                         if other.domain == "sensor" and not other.disabled_by
                         and other.unique_id.endswith(CAR_FULL_SUFFIXES)]
            self._full_ids = found
        return self._full_ids

    def car_full_at(self, now: datetime) -> datetime | None:
        """When the car says it will be full, while it charges up to its own charge limit. The car knows that it
        charges slower near the top, so the plan's last period then ends when the car says."""
        limit = self.car_limit()
        if self.charger_state != ChargerState.CHARGING or limit is None or self.target < limit - 0.5:
            return None
        for entity_id in self._full_entities():
            state = self.hass.states.get(entity_id)
            value = dt_util.parse_datetime(state.state) if state else None
            if value is not None and now < value < now + timedelta(hours=24):
                return value
        return None

    def block_end(self, block, now: datetime) -> datetime | None:
        """When a charging period ends: the plan's, or the car's own time for the last period while it charges."""
        if block is None:
            return None
        blocks = self.schedule.blocks
        if blocks and block == blocks[-1] and block.start <= now and (full := self.car_full_at(now)) is not None:
            return full
        return block.end

    @property
    def active_target(self) -> float:
        """The target for the next ready-by time: its own (one-time time, a day of the week plan) or the normal one."""
        return self.deadline_target if self.deadline_target is not None else self.settings["target_soc"]

    @property
    def target(self) -> float:
        """The target for the next ready-by time, no higher than the car's own charge limit."""
        return self.capped(self.active_target)

    def capped(self, target: float | None) -> float:
        """A target (None: the normal one) no higher than the car's own charge limit."""
        value = target if target is not None else self.settings["target_soc"]
        limit = self.car_limit()
        return min(value, limit) if limit else value

    @property
    def car_full(self) -> bool:
        """The car has reached its own charge limit (or 100 %): it will not take more, so a charger that
        stopped is done, not stopped by someone."""
        soc = self._battery_soc()
        if soc is None:
            return False
        limit = self.car_limit()
        return soc >= 99.5 or (limit is not None and soc >= limit - 1)

    def constraints(self, now: datetime, mode: str | None = None) -> tuple[Constraint, ...]:
        target = self.target
        result = []
        mode = mode or self.mode
        # The price cap is ready by the same time: above the cap only what is needed for that (and only
        # while the phones have not said no).
        if mode == MODE_SMART and self.waiting:
            # Waiting for a cheaper day: the target is due on that day's departure instead.
            result.append(Constraint(self.waiting["deadline"], self.waiting.get("target") or target))
        elif mode in (MODE_SMART, MODE_MANUAL, MODE_PRICE_CAP):
            result.append(Constraint(self.deadline, target))
        if (mode in (MODE_SMART, MODE_MANUAL, MODE_PRICE_CAP) and self.regular_deadline
                and self.regular_deadline < result[-1].deadline and self.settings["min_soc"]):
            # A later departure (learned, or the cheaper day): the usual ready-by time still keeps the minimum level.
            result.append(Constraint(self.regular_deadline, self.settings["min_soc"]))
        if self.trip_active and self.trip.departure > now:
            trip_target = self.trip_target_soc
            limit = self.car_limit() or 100.0
            result.append(Constraint(self.trip.departure, min(max(target, trip_target or 0.0), limit), firm=True))
        return tuple(result)

    def note_outage(self, alive_at: datetime | None) -> None:
        """At start-up: Home Assistant was last alive at alive_at. A planned period that should have started while it
        was away is an outage to tell about (a normal restart takes a minute or two)."""
        now = dt_util.utcnow()
        if alive_at is None or now - alive_at < timedelta(minutes=OUTAGE_MIN_MINUTES):
            return
        if any(start < now and end > alive_at for start, end in self.restored_blocks):
            self.outage = (alive_at, now)

    def charging_now(self, now: datetime | None = None) -> bool:
        """The charger is charging this car – not an old "charging" from the charger's cloud just after our own stop
        (16 Oct 2026: Zaptec said "charging" for a minute after the stop at the end of a period)."""
        return (self.charger_state == ChargerState.CHARGING
                and not self.controller.settling(now or dt_util.utcnow()))

    def _optional(self, name: str, func, default=None):
        """Run an extra so that a fault in it never stops the plan: the charger is still controlled by the plan and
        the fault is logged once (with its traceback) instead of every minute."""
        try:
            return func()
        except Exception:  # noqa: BLE001 – whatever an extra raises, the charging plan goes on
            if name not in self._faults:
                self._faults.add(name)
                _LOGGER.exception("%s: %s failed; the charging plan goes on without it", self.entry.title, name)
            else:
                _LOGGER.debug("%s: %s failed again", self.entry.title, name, exc_info=True)
            return default

    @callback
    def async_recalculate(self) -> None:
        now = dt_util.now()
        if self.trip.departure is not None and self.trip.departure <= now:
            _LOGGER.debug("Trip departure passed, clearing the temporary plan")
            if self.trip.source == "calendar":
                self.routines.dismissed.add(self.trip.event_key)
            self.trip = TripState()

        if self.backend:
            self.charger_state = self.backend.state()
            was_present, self.car_present = self.car_present, self._car_present()
            event = self.controller.observe(self.charger_state, now)
            self._handle_event(event, now)
            if (not self.shared and self.car_present and not was_present and self._new_plug and not self._plug_told
                    and self.charger_state in CONNECTED and self.mode != MODE_MANUAL and event is None):
                self._queue_plug_message()  # the car reported its plug after the charger did
            if self.shared:
                if self.charger_state == ChargerState.DISCONNECTED:
                    self.share.unplugged()
                elif self.car_present and not was_present and self.charger_state in CONNECTED and event is None:
                    # Told which car is on the charger after it was plugged in: this one, with its own plan.
                    self._took_charger()
                self.share.check()
            if self.charger_state == ChargerState.DISCONNECTED:
                self._disconnected_since = self._disconnected_since or now
            elif self.charger_state in CONNECTED:
                self._disconnected_since = None
            unplugged = self.unplugged(now)
            if self.mode not in (self.default_mode, MODE_MANUAL):
                if self.charger_state in CONNECTED:
                    self.now_seen_connected = True
                elif unplugged and self.now_seen_connected:
                    _LOGGER.debug("Car unplugged, %s has run, back to the default plan %s", self.mode,
                                  self.default_mode)
                    self.mode = self.default_mode
                    self.now_seen_connected = False
            self._end_charge_now(now)
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
            if state is None or (state.domain == "binary_sensor" and state.state == STATE_OFF):
                # Off: tomorrow's prices are not out yet (Strømligning's list is then a forecast, if any).
                continue
            slots.extend(parse_price_attributes(dict(state.attributes), estimated=is_forecast(self.hass, entity_id)))
            unit = state.attributes.get("unit_of_measurement")
            if unit and not self.price_unit:
                self.price_unit = str(unit).split("/")[0].strip() or None
        # Where two price lists cover the same time, the first price sensor (the ledger's own) counts.
        unique: dict = {}
        for slot in slots:
            unique.setdefault(slot.start, slot)
        ordered = [unique[key] for key in sorted(unique)]
        self.slot_count = len(ordered)
        previous, previous_target = self.deadline, self._deadline_target
        if self.flags["learn_departure"]:
            self.learned_departures = self._optional(
                "learned departures", lambda: learned_departures.learn(self.trips(), self._home(), now),
                self.learned_departures)
        if self.once is not None and self.once["at"] <= now:
            _LOGGER.debug("The one-time ready-by time %s has passed, back to the normal times", self.once["at"])
            self.once = None
        normal, normal_target, normal_source = self.normal_deadlines(now, 1)[0]
        if self.once is not None:
            self.deadline, self.deadline_target, self.deadline_source = self.once["at"], self.once["target"], "once"
        else:
            self.deadline, self.deadline_target, self.deadline_source = normal, normal_target, normal_source
        # The minimum level is kept by the usual time every day: the ready-by time, or the week plan's earliest time.
        earliest = week_plan.earliest(self.week) if self.week_active else None
        self.regular_deadline = (next_deadline(now, earliest) if earliest is not None
                                 else next_deadline(now, self.ready_by, self.ready_by_weekend))
        if self.once is not None and normal < self.once["at"]:
            # A one-time time after the next normal one: until then the minimum level is kept.
            self.regular_deadline = min(self.regular_deadline, normal)
        self._follow_car_limit()
        if previous is not None and previous <= now and previous != self.deadline:
            # The ready-by time just passed: the morning check looks at what the plan aimed for then.
            self.passed_deadline, self.passed_target = previous, previous_target
        soc = self._battery_soc()
        self.soc_assumed = False
        if soc is not None:
            self.last_soc = soc
        elif self.last_soc is not None or self._inputs_waited(now):
            # The car is not reporting (asleep, cloud down, not loaded yet after a restart): plan with the
            # last level it reported (kept across restarts) right away, or after a while with a low guess.
            soc = self.last_soc if self.last_soc is not None else ASSUMED_SOC
            self.soc_assumed = True
        self.result = calculate(PlanInput(
            soc=soc,
            target_soc=self.target,
            capacity_kwh=self.capacity,
            efficiency=self.settings["efficiency"],
            power_kw=self.settings["charge_power_kw"],
            price_factor=self.settings["price_factor"],
            deadline=self.deadline,
            slots=ordered,
        ), now)

        window = fixed_window(now, self.times["fixed_start"], self.times["fixed_end"])
        later_with = (self.normal_deadlines(now, WAIT_MAX_DAYS + 1)
                      if self.flags["wait_cheaper_day"] and self.once is None else [])
        later = [moment for moment, _target, _source in later_with]
        self._later_targets = {moment: target for moment, target, _source in later_with}
        horizon = max([self.deadline, window[1], *later, *(c.deadline for c in self.constraints(now))])
        base = build_timeline(now, ordered, horizon, self.slot_minutes)
        timeline = self._optional("CO2 forecast", lambda: self._with_co2(base), base)
        self.timeline, self.horizon = timeline, horizon

        # The charge running now, or the start already announced, stays while it is still among the cheapest.
        keep: datetime | None = None
        if self.charging_now(now) and self.mode in HOLD_MODES:
            keep = now
        elif (announced := self.schedule.next_block(now)) is not None:
            keep = max(announced.start, now)

        def plan_for(mode: str, constraints_for: tuple[Constraint, ...] | None = None,
                     keep_plan: bool = True, limit: tuple[float, float] | bool | None = None) -> Schedule:
            # limit: (keep at least %, max price) for waiting with the dear rest; False: to the target; None: the
            # answer given (Vent med resten) with the best later period found.
            answer = (self.wait_params or self._limit_params) if self.limit_wait else None
            wait = answer if limit is None else (limit or None)
            return build_schedule(ScheduleInput(
                mode=mode,
                soc=soc,
                target_soc=self.target,
                capacity_kwh=self.capacity,
                efficiency=self.settings["efficiency"],
                power_kw=self.settings["charge_power_kw"],
                price_factor=self.settings["price_factor"],
                timeline=timeline,
                constraints=constraints_for if constraints_for is not None else self.constraints(now, mode),
                window=window if mode == MODE_FIXED else None,
                price_cap=self.settings["price_cap"],
                min_soc=self.settings["min_soc"],
                cap_override=self.cap_override,
                keep_start=keep if mode == self.mode and keep_plan else None,
                green=self.flags["prefer_green"],
                max_price=wait[1] if wait else None,
                floor_soc=wait[0] if wait else None,
                wake_h=self.controller.wake_delay(DEFAULT_WAKE_MINUTES) / 60,
                margin_h=self.settings["ready_margin"] / 60,
                charging=self.charging_now(now),
            ), now)

        self.waiting = None
        # A charge already running is not stopped to wait for another day.
        if later and self.mode == MODE_SMART and soc is not None and self.charger_state != ChargerState.CHARGING:
            self.waiting = self._optional("wait for a cheaper day",
                                          lambda: self._cheaper_day(now, soc, later, plan_for))
        constraints = self.constraints(now)
        self._deadline_target = None if self.waiting else (
            self.target if self.mode in (MODE_SMART, MODE_PRICE_CAP) else None)

        # Asking before dear charging: the best later period sets what the plan does after "Vent med resten".
        self._limit_params = None
        self.limit_view = self._optional("price question",
                                         lambda: self._limit_view(now, soc, plan_for, constraints, ordered))
        if self.limit_view is None:
            self._limit_params = None  # no view (or it failed): "Vent med resten" falls back to the plan to the target
        self.schedule = plan_for(self.mode, constraints)
        # The level the plan reaches while it waits with the rest (the fixed answer, or the one worked out now).
        self.wait_soc = (round(self._soc_after(self.schedule.energy_kwh, soc), 1)
                         if self.limit_wait and (self.wait_params or self._limit_params) and soc is not None else None)
        # What the other plans would cost right now, so they can be compared before choosing.
        self.alternatives = self._optional(
            "plan comparison", lambda: {mode: self.schedule if mode == self.mode else plan_for(mode)
                                        for mode in (MODE_NOW, MODE_SMART, MODE_FIXED, MODE_PRICE_CAP)},
            {self.mode: self.schedule})

        self._optional("plan note", lambda: self.routines.note_plan(now))
        self._optional("plug-in reminder", lambda: self.routines.plug_soon(now))
        self._optional("cheap power message", lambda: self.routines.low_price(now))
        self._control(now)
        self._optional("warnings", lambda: self._warn(now))
        self._optional("charger offline check", lambda: self.routines.check_offline(now))
        self._optional("charger stop check", lambda: self.routines.check_stop(now))
        self._optional("outage message", lambda: self.routines.check_outage(now))
        self._optional("started message", lambda: self.routines.check_started(now))
        self._optional("done message", lambda: self.routines.check_done(now))
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
            self._optional("entity update", update)

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
            self.warned.clear()
            self.cap_override = True
            self.limit_wait = False
            self.wait_params = None
            self._limit_id = None
            self._full_ids = None
            self._new_plug = True
            self._plug_told = False
            self.routines.new_plug()
            self._refresh_car()
        if not new_plug and event == ChargerEvent.PLUGGED:
            return
        if event == ChargerEvent.PLUGGED and self.car_present and self.mode != MODE_MANUAL:
            self._queue_plug_message()
        elif (event == ChargerEvent.MANUAL_START and self.car_present and not self.charge_desired
              and self.mode not in (MODE_NOW, MODE_MANUAL)):
            # Started from the charger's app or the car while the plan did not want to charge:
            # follow the user and charge now.
            self.mode_before_now, self.mode = self.mode, MODE_NOW
            self.now_seen_connected = True
            self.controller.last_desired = True

    def _queue_plug_message(self) -> None:
        """The plan to the phones (or the question about it) when this car has been plugged in."""
        self._plug_told = True
        if self.confirm_enabled and self.notify.targets:
            self.awaiting_since = dt_util.now()
            self._notify_pending = True
        elif self.info_enabled and self.notify.targets:
            self._info_pending = True

    def _took_charger(self) -> None:
        _LOGGER.debug("%s is the car on the shared charger", self.entry.title)
        self._alerted.clear()
        self.warned.clear()
        self.cap_override = True
        self.limit_wait = False
        self.wait_params = None
        self._limit_id = None
        self._new_plug = True
        self.controller.reset()
        self.routines.new_plug()
        if self.mode != MODE_MANUAL and self.info_enabled and self.notify.targets:
            self._info_pending = True

    def unplugged(self, now: datetime) -> bool:
        """The charger has been disconnected longer than a reboot or a lost connection takes."""
        return (self._disconnected_since is not None
                and now - self._disconnected_since >= timedelta(seconds=UNPLUG_GRACE_SECONDS))

    def car_home(self) -> bool | None:
        """Whether the car is at home by its location tracker (None when there is none or it is not known)."""
        tracker = self.options.get(CONF_CAR_TRACKER)
        state = self.hass.states.get(tracker) if tracker else None
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        return state.state == "home"

    def car_plugged(self) -> bool | None:
        """Whether this car is plugged in: the charger and the car's own plug sensor, None when not known."""
        if self.backend is not None and self.charger_state != ChargerState.UNKNOWN:
            return self.charger_state in CONNECTED and self.car_present
        return self.car_plug_state()[0]

    @callback
    def notify_listeners(self) -> None:
        for update in list(self._listeners):
            self._optional("entity update", update)

    def _restored_ahead(self, now: datetime) -> bool:
        """The plan from before a restart still has a charging period that has not ended."""
        return any(end > now for _, end in self.restored_blocks)

    def price_now(self, now: datetime) -> float | None:
        """The power price of the slot running now (as the price sensor gives it)."""
        for slot in self.timeline:
            if slot.start <= now < slot.end:
                return slot.price
        return None

    def heat_hold(self, now: datetime) -> bool:
        """While the car heats (or cools) on the charger, keep the battery at the target so the climate does not use
        it: start when it is HEAT_HOLD_MARGIN below the target, go on to the target, only up to the price limit."""
        reason = None
        if not self.flags["heat_hold"]:
            reason = "off"
        elif self.mode in (MODE_OFF, MODE_MANUAL):
            reason = "paused"
        elif not (self.charger_state in CONNECTED and self.car_present):
            reason = "not_plugged_in"
        elif not self._climate_on():
            reason = "climate_off"
        else:
            soc = self._battery_soc()
            price = self.price_now(now)
            if soc is None:
                reason = "no_battery_level"
            elif price is None:
                reason = "no_price"
            elif price > self.settings["heat_hold_price"]:
                reason = "too_expensive"
            elif soc >= self.target - 0.5:
                reason = "at_target"
            elif not self.heat_holding and soc > self.target - HEAT_HOLD_MARGIN:
                reason = "at_target"
        holding = reason is None
        if holding != self.heat_holding:
            _LOGGER.debug("Keeping the battery at the target while heating: %s (%s)", holding, reason)
        self.heat_holding, self.heat_hold_reason = holding, reason
        return holding

    def _climate_on(self) -> bool:
        climate = self.routines.climate_entity()
        state = self.hass.states.get(climate) if climate else None
        return state is not None and state.state not in ("off", "unknown", "unavailable")

    def desired(self, now: datetime) -> bool:
        """Should the car charge right now according to the plan."""
        if self.mode in (MODE_OFF, MODE_MANUAL) or self.awaiting_since:
            self.heat_hold(now)
            return False
        if self.car_full and self.charger_state != ChargerState.CHARGING:
            # Full to the car's own limit: starting again would only be refused (and look like a fault).
            self.heat_hold(now)
            return False
        if self.heat_hold(now):
            return True
        if not self.slot_count and self._restored_ahead(now) and self.mode != MODE_NOW:
            # No prices yet (just restarted): follow the plan from before the restart.
            return any(start <= now < end for start, end in self.restored_blocks)
        if self.schedule.charge_now:
            if self.charger_state == ChargerState.CHARGING and self.mode in HOLD_MODES:
                # Keep going to the end of the quarter, so small plan changes do not toggle the charger.
                self._hold_until = floor_slot(now, self.slot_minutes) + timedelta(minutes=self.slot_minutes)
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
        if self.mode == MODE_MANUAL or (not self.car_present and (self.mode != MODE_NOW or self.shared)):
            return
        missing = ((self._battery_soc() is None and self.last_soc is None)
                   or (not self.slot_count and not self._restored_ahead(now)))
        if self.mode != MODE_NOW and missing and not self._inputs_waited(now):
            # Right after a start the car or the price sensor may not be loaded yet and nothing is known
            # from before: leave the charger as it is for a while instead of acting on a plan without them.
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
        entities = list(dict.fromkeys(
            entity for entity in (self.plugged_entity, *self._plug_entities(), self.battery_entity) if entity))
        if entities and self.hass.services.has_service("homeassistant", "update_entity"):
            self.entry.async_create_background_task(
                self.hass, async_update_entities(self.hass, entities), "ev_smart_charge_refresh_car")

    @callback
    def _alert(self, now: datetime) -> None:
        """Tell the phones once when charging cannot follow the plan."""
        if self.status != STATUS_OTHER_CAR:
            self._other_car_since = None
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
        if self.shared and self.status == STATUS_OTHER_CAR:
            if self.share.owner() is not None:
                return  # another of the cars is on the charger: nothing to tell
            alerts[STATUS_OTHER_CAR] = ("Laderen er tilsluttet, men ingen af bilerne melder sig endnu. "
                                        f"Er det {self.entry.title}, så tryk Lad nu.")
        if (text := alerts.get(self.status)) and self.status not in self._alerted:
            if self.status == STATUS_OTHER_CAR:
                if not self._new_plug:
                    return  # only when a car is plugged in, not at every restart while it stands there
                self._other_car_since = self._other_car_since or now
                if now - self._other_car_since < timedelta(seconds=CAR_REPORT_GRACE_SECONDS):
                    return  # the car may not have reported its plug yet
            self._alerted.add(self.status)
            self.entry.async_create_background_task(
                self.hass, self.notify.async_send_alert(text), "ev_smart_charge_notify_alert")

    def _end_charge_now(self, now: datetime) -> None:
        """Charge now ends when it has charged the car to the target (or the car's own limit): back to the default
        plan after NOW_DONE_MINUTES standing finished. Not when Charge now is the default plan itself."""
        if self.mode != MODE_NOW or self.default_mode == MODE_NOW:
            self._now_charged, self._now_done_since = False, None
            return
        if self.charger_state == ChargerState.CHARGING:
            self._now_charged, self._now_done_since = True, None
            return
        soc = self._battery_soc()
        reached = self.car_full or (soc is not None and soc >= self.target - 0.5)
        if not (self._now_charged and reached and self.charger_state in CONNECTED):
            self._now_done_since = None
            return
        self._now_done_since = self._now_done_since or now
        if now - self._now_done_since >= timedelta(minutes=NOW_DONE_MINUTES):
            _LOGGER.debug("Charge now reached the target, back to the default plan %s", self.default_mode)
            self.mode = self.default_mode
            self.now_seen_connected = False
            self._now_charged, self._now_done_since = False, None

    @property
    def week_active(self) -> bool:
        return self.flags["week_plan"] and week_plan.has_days(self.week)

    def normal_deadlines(self, now: datetime, count: int) -> list[tuple[datetime, float | None, str]]:
        """The next normal ready-by times (without the one-time time), each with its own target and what set it:
        the week plan (when it is on), else the learned departures (when that is on and learned), else the set time."""
        if self.week_active:
            found = week_plan.deadlines(now, self.week, count)
            if found:
                return [(moment, target, "week") for moment, target in found]
        times = self.learned_departures.get("times") if self.flags["learn_departure"] else None
        if times and any(times.values()):
            found = learned_departures.deadlines(now, times, count)
            if found:
                return [(moment, None, "learned") for moment in found]
        result, moment = [], now
        for _ in range(count):
            moment = next_deadline(moment, self.ready_by, self.ready_by_weekend)
            result.append((moment, None, "time"))
            moment += timedelta(minutes=1)
        return result

    def _follow_car_limit(self) -> None:
        """A ready-by time with its own target sets the car's charge limit to it while the car is on the charger (the
        car would stop at its old limit), and the normal target back afterwards. Tried once per target and time."""
        if not (self.backend and self.charger_state in CONNECTED and self.car_present):
            return
        if self.deadline_target is not None:
            want = self.deadline_target
        elif self._limit_goal is not None:
            want = self.settings["target_soc"]  # the own target is over: the normal one again
        else:
            return
        limit = self.car_limit()
        if limit is not None and abs(limit - min(max(want, 50.0), 100.0)) < 0.5:
            self._limit_goal = want if self.deadline_target is not None else None
            return
        key = (round(want), self.deadline)
        if self._limit_tried == key:
            return
        self._limit_tried = key
        self._limit_goal = want if self.deadline_target is not None else None
        _LOGGER.debug("Setting the car's charge limit to %s %% for the ready-by time %s", want, self.deadline)
        self.entry.async_create_background_task(self.hass, self.async_sync_car_limit(want), "evledger_follow_limit")

    def upcoming_deadlines(self, now: datetime, count: int) -> list[datetime]:
        """The next normal ready-by times."""
        return [moment for moment, _target, _source in self.normal_deadlines(now, count)]

    def daily_use_kwh(self, now: datetime) -> float | None:
        """The battery energy the car uses on an average day: the ledger's trips of the last USE_DAYS days times
        the consumption. None with too short a history."""
        trips = self.trips()
        starts = [dt_util.parse_datetime(trip.started_at or "") for trip in trips]
        starts = [start for start in starts if start is not None]
        if not starts or now - min(starts) < timedelta(days=MIN_USE_HISTORY_DAYS):
            return None
        since = now - timedelta(days=USE_DAYS)
        days = min(USE_DAYS, (now - min(starts)).total_seconds() / 86400)
        km = sum(trip.distance_km or 0.0 for trip in trips
                 if (start := dt_util.parse_datetime(trip.started_at or "")) is not None and start >= since)
        return km / days * self.settings["consumption"] / 1000

    def _limit_view(self, now: datetime, soc: float | None, plan_for, constraints, ordered) -> dict | None:
        """Ask before dear charging (user, 2026-10-15): the plan to the target, and for the cheapest later periods (day
        and night, each of the next days, from the price forecast) what waiting with the rest saves. Tonight the car
        still charges what the daily driving needs until that period, so the battery keeps the reserve; above that only
        quarters no dearer than the later period (or the fixed price limit, when set)."""
        if (self.mode != MODE_SMART or not self.flags["smart_price_limit"] or soc is None or self.deadline is None
                or self.waiting or self.capacity <= 0):
            return None
        full = plan_for(MODE_SMART, constraints, limit=False)
        use = self.daily_use_kwh(now)
        reserve = self.settings["smart_floor_soc"]
        fixed = self.settings["smart_max_price"] if self.settings["smart_max_price"] > 0 else None
        factor, power = self.settings["price_factor"], self.settings["charge_power_kw"]
        eff = self.settings["efficiency"]
        target = full.target_soc or self.target
        view = {"daily_use_kwh": round(use, 1) if use is not None else None, "reserve_soc": reserve,
                "over_kwh": 0.0, "over_cost": 0.0, "over_price": None, "below_kwh": full.energy_kwh,
                "below_cost": full.cost or 0.0, "below_soc": round(self._soc_after(full.energy_kwh, soc), 1),
                "need_soc": None, "price_limit": None, "saving": 0.0, "ask": False, "windows": []}

        def wall(low: float, high: float) -> float:
            return max(high - low, 0.0) * self.capacity / 100 / eff

        def need_at(moment: datetime) -> float:
            """The level to charge to tonight so the driving until `moment` leaves the reserve."""
            days = max((moment - now).total_seconds() / 86400, 0.0)
            return reserve + (use * days / self.capacity * 100 if use else 0.0)

        if not full.slots or wall(max(soc, reserve), target) < 0.5:
            return view
        after = max([self.deadline, *(slot.end for slot in full.slots)])
        # Only published prices and the forecast count as cheaper later, not a price borrowed from an earlier day.
        timeline = build_timeline(now, ordered, after + timedelta(hours=LIMIT_LOOK_HOURS), self.slot_minutes,
                                  borrow=False)
        best_by_kind: dict[str, dict] = {}
        best: dict | None = None
        for day in range(LIMIT_LOOK_HOURS // 24):
            begin, until = after + timedelta(days=day), after + timedelta(days=day + 1)
            for first in cheaper_later(timeline, begin, wall(max(soc, reserve), target), power, factor, until=until,
                                       zone=now.tzinfo):
                need = min(need_at(first.start), target)
                if wall(max(soc, need), target) < 0.5:
                    continue  # the driving until then needs the whole target tonight
                # A forecast price with the safety margin: tonight charges a little more, and waiting must save
                # even if the forecast was too low.
                later_price = first.price * (1 + FORECAST_MARGIN) if first.estimated else first.price
                cap = later_price if fixed is None else max(later_price, fixed)
                below = plan_for(MODE_SMART, constraints, keep_plan=False, limit=(need, cap))
                over_kwh = full.energy_kwh - below.energy_kwh
                if over_kwh < 0.5:
                    continue
                over_cost = (full.cost or 0.0) - (below.cost or 0.0)
                window = next((w for w in cheaper_later(timeline, begin, over_kwh, power, factor, until=until,
                                                        zone=now.tzinfo)
                               if w.kind == first.kind), None)
                if window is None:
                    continue
                saving = over_cost - over_kwh * (window.price * (1 + FORECAST_MARGIN) if window.estimated
                                                 else window.price)
                if saving < 0.5:
                    continue
                found = {"kind": window.kind, "start": window.start, "end": window.end, "price": window.price,
                         "saving": round(saving, 2), "estimated": window.estimated, "need_soc": round(need, 1),
                         "over_kwh": round(over_kwh, 2), "over_cost": round(over_cost, 2), "cap": round(cap, 4),
                         "below_kwh": below.energy_kwh, "below_cost": below.cost or 0.0,
                         "below_soc": round(self._soc_after(below.energy_kwh, soc), 1)}
                if window.kind not in best_by_kind or saving > best_by_kind[window.kind]["saving"]:
                    best_by_kind[window.kind] = found
                if best is None or saving > best["saving"]:
                    best = found
        if best is None:
            return view
        view["windows"] = sorted(best_by_kind.values(), key=lambda item: item["start"])
        view.update(over_kwh=best["over_kwh"], over_cost=best["over_cost"],
                    over_price=round(best["over_cost"] / best["over_kwh"], 4), below_kwh=best["below_kwh"],
                    below_cost=best["below_cost"], below_soc=best["below_soc"], need_soc=best["need_soc"],
                    price_limit=best["cap"], saving=best["saving"],
                    ask=best["saving"] >= self.settings["ask_min_saving"])
        self._limit_params = (best["need_soc"], best["cap"])
        return view

    def _cheaper_day(self, now: datetime, soc: float, later: list[datetime], plan_for) -> dict | None:
        """A later departure whose night is clearly cheaper, when the battery lasts until then."""
        use = self.daily_use_kwh(now)
        if use is None or self.trip_active or self.capacity <= 0:
            return None
        targets = getattr(self, "_later_targets", {})
        base = plan_for(MODE_SMART, (Constraint(later[0], self.capped(targets.get(later[0]))),), keep_plan=False)
        if not base.blocks or not base.energy_kwh or base.cost is None:
            return None
        price_now = base.cost / base.energy_kwh
        floor = max(self.settings["min_soc"], 0.0) + WAIT_RESERVE
        best = None
        for deadline in later[1:]:
            days = (deadline - now).total_seconds() / 86400
            soc_then = soc - use * days / self.capacity * 100
            if soc_then < floor:
                break
            target = self.capped(targets.get(deadline))
            plan = plan_for(MODE_SMART, (Constraint(deadline, target),), keep_plan=False)
            if not plan.blocks or plan.cost is None or not plan.energy_kwh or plan.blocks[0].start < later[0]:
                continue
            price = plan.cost / plan.energy_kwh
            saving = (price_now - price) * plan.energy_kwh
            if price <= price_now * (1 - WAIT_MIN_SHARE) and saving >= WAIT_MIN_KR and (
                    best is None or saving > best["saving"]):
                best = {"deadline": deadline, "target": target, "price": round(price, 4),
                        "price_now": round(price_now, 4), "saving": round(saving, 2),
                        "soc_then": round(soc_then), "estimated": plan.estimated}
        return best

    def _with_co2(self, timeline: list) -> list:
        """The grid's CO2 per kWh on each slot ("prefer green power"), when the forecast covers it."""
        if not self.flags["prefer_green"] or not self.co2:
            return timeline
        result = []
        for slot in timeline:
            values, quarter = [], slot.start
            while quarter < slot.end:
                if (value := self.co2.get(quarter.astimezone(UTC))) is not None:
                    values.append(value)
                quarter += timedelta(minutes=15)
            result.append(replace(slot, co2=sum(values) / len(values)) if values else slot)
        return result

    async def async_refresh_co2(self) -> None:
        """Fetch the grid's CO2 forecast (Denmark) for "prefer green power"."""
        self.co2_area = grid_co2.price_area(*self._home())
        if self.co2_area is None:
            return
        try:
            self.co2 = await grid_co2.async_fetch(async_get_clientsession(self.hass), self.co2_area, dt_util.now())
            self.co2_updated = dt_util.utcnow()
        except Exception as err:  # noqa: BLE001 - the plan goes on without the CO2 forecast
            _LOGGER.debug("CO2 forecast not available: %s", err)
        finally:
            self._co2_task = None
        self.async_recalculate()

    def _co2_due(self) -> bool:
        return (self.flags["prefer_green"] and self._co2_task is None
                and (self.co2_updated is None or dt_util.utcnow() - self.co2_updated >= grid_co2.REFRESH))

    def goal_time(self, now: datetime) -> datetime | None:
        """When the target has to be reached: the ready-by time, or an earlier temporary departure."""
        times = [self.deadline] if self.deadline else []
        if self.trip_active and self.trip.departure > now:
            times.append(self.trip.departure)
        return min(times) if times else None

    def _soc_after(self, kwh: float, soc: float) -> float:
        return min(soc + kwh * self.settings["efficiency"] / self.capacity * 100, 100.0)

    @callback
    def _warn(self, now: datetime) -> None:
        """Tell the phones once (per plug-in and settings) when the plan cannot reach its target in time,
        and ask before the price cap is exceeded to reach it. Without an answer the plan goes on. Above the price
        limit's floor, when part of the plan costs more than the limit and a later period is cheaper, the phones are
        asked whether to wait with that part (and what it saves); without an answer the plan charges to the target."""
        if not (self.backend and self.charger_state in CONNECTED and self.car_present
                and self.info_enabled and self.notify.targets) or self.awaiting_since:
            return
        soc = self._battery_soc()
        if soc is None or not self.slot_count:
            return  # judged on real values only
        if self._started_at and (dt_util.utcnow() - self._started_at).total_seconds() < STARTUP_GRACE_SECONDS:
            return
        schedule = self.schedule
        target = schedule.target_soc or self.target
        found: dict[str, str] = {}
        if self.mode == MODE_PRICE_CAP and self.cap_override and schedule.over_cap_kwh >= 0.5:
            found["cap"] = self.notify.cap_text(self, now)
        elif self.mode in (MODE_SMART, MODE_FIXED) and schedule.shortfall_kwh >= 0.5:
            reach = max(target - schedule.shortfall_kwh * self.settings["efficiency"] / self.capacity * 100, soc)
            found["short"] = self.notify.short_text(self, now, reach, target)
        view = self.limit_view
        labels: dict[str, tuple[str, str]] = {}
        if view and view["ask"] and not self.limit_wait:
            found["price"] = self.notify.price_text(self, now, soc, view)
            labels["price"] = self.notify.wait_labels(self, soc, view)
        limit = self.car_limit()
        need = self.trip_target_soc if self.trip_active else None
        if limit is not None and need is not None and need > limit + 0.5:
            found["limit"] = (f"Turen kræver {min(need, 100):.0f} %, men bilens ladegrænse er {limit:.0f} %. "
                              "Hæv grænsen i bilens app, så lader planen nok.")
        for key, text in found.items():
            if key in self.warned:
                continue
            self.warned.add(key)
            self.entry.async_create_background_task(
                self.hass, self.notify.async_send_warning(key, text, labels.get(key)), f"ev_smart_charge_warn_{key}")

    def _status(self, state: ChargerState, desired: bool) -> str:
        if self.mode == MODE_MANUAL:
            return STATUS_MANUAL
        if self.awaiting_since and state in CONNECTED:
            return STATUS_AWAITING_CONFIRMATION
        if state == ChargerState.DISCONNECTED:
            return STATUS_DISCONNECTED
        if state == ChargerState.UNKNOWN:
            return STATUS_UNKNOWN
        if not self.car_present and (self.mode != MODE_NOW or self.shared):
            return STATUS_OTHER_CAR
        if state == ChargerState.CHARGING and not self.controller.settling(dt_util.utcnow()):
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
        if self.schedule.energy_kwh <= 0 and self.schedule.limit_kwh >= 0.5 and not self.car_full:
            return STATUS_WAITING  # the price is above the limit: waits for cheaper power
        if (self.schedule.energy_kwh <= 0 and self.mode != MODE_NOW) or self.car_full:
            return STATUS_DONE
        return STATUS_WAITING
