"""Everyday help around the plan: the message when a charge is done, the evening check (plug-in reminder,
charger offline), the car's climate before the ready-by time, and trips from a calendar."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CALENDAR_LEAD_MINUTES,
    CALENDAR_LOOKAHEAD_HOURS,
    CALENDAR_MARGIN_MINUTES,
    CALENDAR_REFRESH_MINUTES,
    CHARGER_OFFLINE_ALERT_MINUTES,
    CONF_CAR_CLIMATE,
    CONF_TRIP_CALENDAR,
    CONF_TRIP_KEYWORD,
    PRECONDITION_OFF_AFTER_MINUTES,
    REMINDER_WINDOW_HOURS,
    STARTED_NOTE_QUIET_MINUTES,
    STATUS_DONE,
)
from .control import ChargerState
from .plan import MODE_MANUAL, MODE_OFF

if TYPE_CHECKING:
    from .planner import ChargePlanner

_LOGGER = logging.getLogger(__name__)


@dataclass
class ChargeRun:
    """The home charging sessions since the cable went in, summed for the message when the plan is done
    (a plan in several periods is several sessions in the ledger)."""

    kwh: float = 0.0
    price: float = 0.0
    price_known: bool = True
    sessions: int = 0
    start_soc: float | None = None
    end_soc: float | None = None
    started: datetime | None = None
    ended: datetime | None = None

    def add(self, charge: Any) -> None:
        self.sessions += 1
        self.kwh += charge.kwh or 0.0
        if charge.price is None:
            self.price_known = False
        else:
            self.price += charge.price
        if self.start_soc is None:
            self.start_soc = charge.start_battery_pct
        if charge.end_battery_pct is not None:
            self.end_soc = charge.end_battery_pct
        started = dt_util.parse_datetime(charge.started_at or "")
        ended = dt_util.parse_datetime(charge.ended_at or "")
        if started and (self.started is None or started < self.started):
            self.started = started
        if ended and (self.ended is None or ended > self.ended):
            self.ended = ended


def done_text(run: ChargeRun, unit: str, soc: float | None) -> str:
    """Energy and price of the whole charge, the battery before and after, and when it charged."""

    def number(value: float, digits: int) -> str:
        return f"{value:.{digits}f}".replace(".", ",")

    lines = []
    energy = f"{number(run.kwh, 1)} kWh"
    if run.price_known and run.kwh > 0:
        lines.append(f"{energy} for {number(run.price, 2)} {unit} ({number(run.price / run.kwh, 2)} {unit}/kWh)")
    elif run.price_known:
        lines.append(f"{energy} for {number(run.price, 2)} {unit}")
    else:
        lines.append(f"{energy} (prisen kendes ikke endnu)")
    end_soc = soc if soc is not None else run.end_soc
    if run.start_soc is not None and end_soc is not None:
        lines.append(f"Batteri {run.start_soc:.0f} % → {end_soc:.0f} %")
    if run.started and run.ended:
        begin, end = dt_util.as_local(run.started), dt_util.as_local(run.ended)
        period = f"{begin.strftime('%H:%M')}–{end.strftime('%H:%M')}"
        if run.sessions > 1:
            period += f" ({run.sessions} perioder)"
        lines.append(period)
    return "\n".join(lines)


class Routines:
    def __init__(self, planner: ChargePlanner) -> None:
        self.planner = planner
        self.hass = planner.hass
        self.run = ChargeRun()
        # The day the evening check was made (kept across restarts by the reminder switch).
        self.evening_checked: date | None = None
        self._offline_since: datetime | None = None
        # Charging started: whether it charged at the last look (None until the first one, so a charge already running
        # at start-up is not a new start) and when the last "started" message went out.
        self._was_charging: bool | None = None
        self._started_note: datetime | None = None
        self._offline_alerted = False
        # The ready-by (or departure) time the phones were asked to precondition for, and whether they said yes
        # (then the climate was turned on by us, and is turned off again if nobody left).
        self.precondition_goal: datetime | None = None
        self.preconditioned = False
        self._climate_id: str | None = None
        self._calendar_checked: datetime | None = None
        self._calendar_busy = False
        # Calendar events whose trip was cleared by hand: not added again.
        self.dismissed: set[str] = set()

    # -- the charge is done ---------------------------------------------------------------------

    def charge_finished(self, charge: Any) -> None:
        """A home charging session ended in the ledger (with its kWh and price)."""
        self.run.add(charge)
        self.planner.async_recalculate()

    def new_plug(self) -> None:
        self.run = ChargeRun()

    def check_done(self, now: datetime) -> None:
        """Send the whole charge once the plan is done (or the cable is out) and the last session is closed."""
        p = self.planner
        run = self.run
        if not run.sessions or p.open_charge():
            return
        # Out for longer than a charger reboot or a lost connection takes.
        unplugged = p.unplugged(now)
        if p.status != STATUS_DONE and not unplugged:
            return
        self.run = ChargeRun()
        if not (p.flags["notify_done"] and p.notify.targets):
            return
        title = "opladning færdig" if p.status == STATUS_DONE else "opladning slut"
        text = done_text(run, p.price_unit or "kr", p._battery_soc())
        p.entry.async_create_background_task(
            self.hass, p.notify.async_send_note("done", title, text), "ev_smart_charge_notify_done")

    # -- charging started -----------------------------------------------------------------------

    def check_started(self, now: datetime) -> None:
        """Tell the phones when the charger starts charging this car: plan, expected end, price and target. A short
        stop and start (the car pausing, a charger reboot) does not send it again within half an hour."""
        p = self.planner
        charging = p.charger_state == ChargerState.CHARGING and p.car_present
        was, self._was_charging = self._was_charging, charging
        if not charging or was is not False:
            return
        if self._started_note is not None and now - self._started_note < timedelta(minutes=STARTED_NOTE_QUIET_MINUTES):
            return
        self._started_note = now
        if not (p.flags["notify_start"] and p.notify.targets):
            return
        text = p.notify.start_text(p, now)
        actions = [{"action": f"{p.notify.prefix}OFF", "title": "Pause"}]
        p.entry.async_create_background_task(
            self.hass, p.notify.async_send_note("start", "ladning startet", text, actions),
            "ev_smart_charge_notify_start")

    # -- charger offline ------------------------------------------------------------------------

    def check_offline(self, now: datetime) -> None:
        """Tell the phones at once when the charger is offline while the plan wants to charge."""
        p = self.planner
        if p.backend is None or p.charger_state != ChargerState.UNKNOWN or not p.charge_desired:
            self._offline_since = None
            if p.charger_state != ChargerState.UNKNOWN:
                self._offline_alerted = False
            return
        self._offline_since = self._offline_since or now
        if (self._offline_alerted or now - self._offline_since < timedelta(minutes=CHARGER_OFFLINE_ALERT_MINUTES)
                or not (p.info_enabled and p.notify.targets)):
            return
        self._offline_alerted = True
        p.entry.async_create_background_task(
            self.hass, p.notify.async_send_alert(
                "Laderen er offline, og opladningen skulle være i gang. Den startes, så snart laderen svarer igen."),
            "ev_smart_charge_notify_offline")

    # -- the evening check ----------------------------------------------------------------------

    def evening(self, now: datetime) -> None:
        """Once a day at the reminder time: remind to plug in, and warn when the charger is offline."""
        p = self.planner
        at = datetime.combine(now.date(), p.times["reminder_time"], tzinfo=now.tzinfo)
        if self.evening_checked == now.date() or not at <= now < at + timedelta(hours=REMINDER_WINDOW_HOURS):
            return
        if not p.notify.targets:
            return
        if p._battery_soc() is None and p.last_soc is None and not p._inputs_waited(now):
            return  # just started: wait for the car (the window is long enough)
        self.evening_checked = now.date()
        for key, title, text in (("reminder", "sæt bilen til", self.reminder_text(now)),
                                 ("offline", "laderen er offline", self.offline_text())):
            if text:
                p.entry.async_create_background_task(
                    self.hass, p.notify.async_send_note(key, title, text), f"ev_smart_charge_notify_{key}")
        p.notify_listeners()

    def reminder_text(self, now: datetime) -> str | None:
        p = self.planner
        if not p.flags["plug_reminder"] or p.car_home() is False or p.car_plugged() is not False:
            return None
        soc = p._battery_soc()
        soc = soc if soc is not None else p.last_soc
        if soc is None:
            return None
        trip_need = p.trip_target_soc if p.trip_active else None
        if soc >= p.settings["reminder_soc"] and (trip_need is None or soc >= trip_need):
            return None
        goal = p.goal_time(now)
        by = f" inden {p.notify.when(goal)}" if goal else ""
        why = f"turen kræver {min(trip_need, 100):.0f} %" if trip_need is not None and soc < trip_need else None
        text = f"Bilen er ikke sat til, og batteriet er på {soc:.0f} %"
        text += f" ({why})" if why else ""
        return f"{text}. Sæt kablet i, så lader den billigst{by}."

    def offline_text(self) -> str | None:
        p = self.planner
        if p.backend is None or p.charger_state != ChargerState.UNKNOWN or p.mode in (MODE_OFF, MODE_MANUAL):
            return None
        if p.car_plugged() is False:
            return None  # the car's own plug sensor says it is not plugged in: nothing waits
        return ("Laderen er offline. Bilen kan ikke lade efter planen, før laderen svarer igen. "
                "Tjek laderen og dens netværk.")

    # -- the car's climate before the ready-by time -----------------------------------------------

    def climate_entity(self) -> str | None:
        """The car's climate entity: chosen in the setup, else one on this car only – the battery sensor's device or a
        device with the same name in another car integration – preferring one that can send commands to the car
        (Tesla Fleet, Teslemetry, Tessie) over Tesla Custom."""
        if chosen := self.planner.options.get(CONF_CAR_CLIMATE):
            return chosen
        if self._climate_id is None:
            self._climate_id = ""
            registry = er.async_get(self.hass)
            devices = dr.async_get(self.hass)
            battery = registry.async_get(self.planner.battery_entity)
            car = devices.async_get(battery.device_id) if battery is not None and battery.device_id else None
            if car is not None:
                name = car.name_by_user or car.name
                same_car = [device.id for device in devices.devices.values()
                            if device.id == car.id or (name and (device.name_by_user or device.name) == name)]
                found = [other for device_id in same_car for other in er.async_entries_for_device(registry, device_id)
                         if other.domain == "climate" and not other.disabled_by and "overheat" not in other.unique_id]
                order = ("tesla_fleet", "teslemetry", "tessie")
                found.sort(key=lambda other: order.index(other.platform) if other.platform in order else len(order))
                self._climate_id = found[0].entity_id if found else ""
        return self._climate_id or None

    def precondition(self, now: datetime) -> None:
        """Before the ready-by (or departure) time the phones are asked whether to warm the car; the climate is only
        turned on after an answer (every control of the car is confirmed), never on its own."""
        p = self.planner
        climate = self.climate_entity()
        if climate is None:
            return
        goal = self.precondition_goal
        if goal is not None and now >= goal + timedelta(minutes=PRECONDITION_OFF_AFTER_MINUTES):
            self.precondition_goal = None
            state = self.hass.states.get(climate)
            # Still plugged in at home: nobody left, so the car does not keep the cabin warm for nothing.
            if (self.preconditioned and state is not None
                    and state.state not in (STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN)
                    and p.car_plugged() and p.car_home() is not False):
                self._climate("turn_off", climate)
            self.preconditioned = False
        target = p.goal_time(now)
        if not p.flags["precondition"] or target is None or self.precondition_goal == target:
            return
        start = target - timedelta(minutes=p.settings["precondition_minutes"])
        if start <= now < target and p.car_home() is not False and p.notify.targets:
            self.precondition_goal = target
            self.preconditioned = False
            p.entry.async_create_background_task(
                self.hass, p.notify.async_send_precondition(target), "ev_smart_charge_precondition")

    def answer_precondition(self, yes: bool) -> None:
        """The phone's answer: warm the car now (only before the time it was asked for)."""
        goal = self.precondition_goal
        if not yes or goal is None or dt_util.now() >= goal or (climate := self.climate_entity()) is None:
            return
        self.preconditioned = True
        self._climate("turn_on", climate)

    def _climate(self, service: str, entity_id: str) -> None:
        _LOGGER.info("%s: climate.%s on %s", self.planner.entry.title, service, entity_id)

        async def call() -> None:
            try:
                await self.hass.services.async_call("climate", service, {"entity_id": entity_id}, blocking=True)
            except (HomeAssistantError, ValueError) as err:
                _LOGGER.warning("climate.%s on %s failed: %s", service, entity_id, err)

        self.planner.entry.async_create_background_task(self.hass, call(), "ev_smart_charge_climate")

    # -- trips from a calendar ------------------------------------------------------------------

    def calendar_due(self, now: datetime) -> bool:
        return (bool(self.planner.options.get(CONF_TRIP_CALENDAR)) and not self._calendar_busy
                and (self._calendar_checked is None
                     or now - self._calendar_checked >= timedelta(minutes=CALENDAR_REFRESH_MINUTES)))

    async def async_calendar(self, now: datetime) -> None:
        """Read the next events and make the first matching one the temporary plan."""
        calendar = self.planner.options.get(CONF_TRIP_CALENDAR)
        self._calendar_checked = now
        self._calendar_busy = True
        try:
            response = await self.hass.services.async_call(
                "calendar", "get_events",
                {"entity_id": calendar, "start_date_time": now.isoformat(),
                 "end_date_time": (now + timedelta(hours=CALENDAR_LOOKAHEAD_HOURS)).isoformat()},
                blocking=True, return_response=True)
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("Reading the calendar %s failed: %s", calendar, err)
            return
        finally:
            self._calendar_busy = False
        events = ((response or {}).get(calendar) or {}).get("events") or []
        self.planner.async_calendar_trip(self.pick(events, now))

    def pick(self, events: list[dict], now: datetime) -> dict | None:
        """The first event with the keyword in its title (or, without a keyword, with a location) that is not
        so close that the car should already have left."""
        keyword = str(self.planner.options.get(CONF_TRIP_KEYWORD) or "").strip().lower()
        found = []
        for event in events:
            start = dt_util.parse_datetime(str(event.get("start") or ""))
            if start is None or start.tzinfo is None:
                continue  # all-day events have no time to be ready by
            summary = str(event.get("summary") or "").strip()
            location = str(event.get("location") or "").strip()
            if keyword:
                if keyword not in summary.lower() and keyword not in str(event.get("description") or "").lower():
                    continue
            elif not location:
                continue
            key = f"{start.isoformat()}|{summary}"
            if key in self.dismissed or start - timedelta(minutes=CALENDAR_MARGIN_MINUTES) <= now:
                continue
            found.append({"key": key, "start": start, "summary": summary, "location": location})
        return min(found, key=lambda event: event["start"]) if found else None

    @staticmethod
    def departure(start: datetime, duration_min: float | None) -> datetime:
        """Leave so the car is there at the event's start: the drive time and a margin, or a fixed lead."""
        lead = duration_min + CALENDAR_MARGIN_MINUTES if duration_min else CALENDAR_LEAD_MINUTES
        return (start - timedelta(minutes=lead)).replace(second=0, microsecond=0)
