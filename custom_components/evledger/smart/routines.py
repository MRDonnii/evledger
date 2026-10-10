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

from ..device_resolve import all_devices
from .const import (
    CALENDAR_LEAD_MINUTES,
    CALENDAR_LOOKAHEAD_HOURS,
    CALENDAR_MARGIN_MINUTES,
    CALENDAR_REFRESH_MINUTES,
    CHARGE_RUN_KEEP_HOURS,
    CHARGER_OFFLINE_ALERT_MINUTES,
    CONF_CAR_CLIMATE,
    CONF_TRIP_CALENDAR,
    CONF_TRIP_KEYWORD,
    LEARN_EFFICIENCY_RANGE,
    LEARN_MIN_HOURS,
    LEARN_MIN_SAMPLES,
    LEARN_MIN_SOC_GAIN,
    LEARN_POWER_RANGE,
    LEARN_TOP_SOC,
    LEARN_WEIGHT,
    LOW_PRICE_EVERY_HOURS,
    LOW_PRICE_FROM,
    LOW_PRICE_UNTIL,
    MONTH_NAMES,
    MONTHLY_AT,
    MONTHLY_DAYS,
    PLUG_SOON_MINUTES,
    PRECONDITION_OFF_AFTER_MINUTES,
    REMINDER_WINDOW_HOURS,
    SAVED_MONTHS_KEPT,
    SAVING_SHOWN,
    STARTED_NOTE_QUIET_MINUTES,
    STATUS_DONE,
)
from .control import CONNECTED, ChargerState
from .plan import MODE_MANUAL, MODE_NOW, MODE_OFF

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
    # What a kWh would have cost charging right away when the car was plugged in ("Charge now"), for the saving.
    now_price: float | None = None
    noted: datetime | None = None

    @property
    def active(self) -> bool:
        return bool(self.sessions) or self.now_price is not None

    def as_dict(self) -> dict[str, Any]:
        """Kept on the done message switch, so periods that ended before a restart still count."""
        return {"kwh": round(self.kwh, 3), "price": round(self.price, 4), "price_known": self.price_known,
                "sessions": self.sessions, "start_soc": self.start_soc, "end_soc": self.end_soc,
                "started": self.started.isoformat() if self.started else None,
                "ended": self.ended.isoformat() if self.ended else None,
                "now_price": round(self.now_price, 4) if self.now_price is not None else None,
                "noted": self.noted.isoformat() if self.noted else None}

    def saving(self) -> float | None:
        """What the plan saved against charging right away at plug-in (negative: it cost more)."""
        if self.now_price is None or not self.price_known or self.kwh <= 0:
            return None
        return self.kwh * self.now_price - self.price

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChargeRun:
        def when(key: str) -> datetime | None:
            return dt_util.parse_datetime(str(data.get(key) or ""))

        def soc(key: str) -> float | None:
            value = data.get(key)
            return float(value) if isinstance(value, (int, float)) else None

        return cls(kwh=float(data.get("kwh") or 0.0), price=float(data.get("price") or 0.0),
                   price_known=bool(data.get("price_known", True)), sessions=int(data.get("sessions") or 0),
                   start_soc=soc("start_soc"), end_soc=soc("end_soc"), started=when("started"), ended=when("ended"),
                   now_price=soc("now_price"), noted=when("noted"))

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
    saving = run.saving()
    if saving is not None and saving >= SAVING_SHOWN:
        lines.append(f"Sparet {number(saving, 2)} {unit} i forhold til Lad nu ved tilslutning")
    elif saving is not None and saving <= -SAVING_SHOWN:
        lines.append(f"{number(-saving, 2)} {unit} dyrere end Lad nu ved tilslutning")
    return "\n".join(lines)


def _average(old: float | None, sample: float) -> float:
    """A running average that follows new charges without jumping on one odd one."""
    return sample if old is None else old * (1 - LEARN_WEIGHT) + sample * LEARN_WEIGHT


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
        # Learned from the ledger's charges: charging power and efficiency with how many charges they rest on.
        self.learned: dict[str, float] = {}
        # The plan start a "plug in soon" reminder was sent for; the low price message: below now, and when sent.
        self.soon_sent: datetime | None = None
        self._was_below = False
        self.low_sent: datetime | None = None
        # The month the last monthly summary was for, and what the plans saved per month.
        self.summary_sent: str | None = None
        self.saved: dict[str, float] = {}

    # -- the charge is done ---------------------------------------------------------------------

    def charge_finished(self, charge: Any) -> None:
        """A home charging session ended in the ledger (with its kWh and price)."""
        self.run.add(charge)
        self.learn(charge)
        self.planner.async_recalculate()

    def note_plan(self, now: datetime) -> None:
        """At the first plan after a plug-in: what a kWh costs charging right away, for the saving in the done
        message."""
        p = self.planner
        if self.run.now_price is not None or p.charger_state not in CONNECTED or not p.car_present:
            return
        plan = p.alternatives.get(MODE_NOW)
        energy = sum(slot.kwh for slot in plan.slots) if plan is not None else 0.0
        if energy > 0:
            factor = p.settings["price_factor"]
            self.run.now_price = sum(slot.kwh * slot.price * factor for slot in plan.slots) / energy
            self.run.noted = now

    # -- learning the charging power and efficiency ---------------------------------------------

    def learn(self, charge: Any) -> None:
        """Learn the charging power and the efficiency (energy into the battery per kWh from the charger) from the
        ledger's home charges, and plan with them once a couple of charges agree."""
        p = self.planner
        started = dt_util.parse_datetime(charge.started_at or "")
        ended = dt_util.parse_datetime(charge.ended_at or "")
        if not (started and ended and charge.kwh and charge.kwh > 0):
            return
        hours = (ended - started).total_seconds() / 3600
        begin, end = charge.start_battery_pct, charge.end_battery_pct
        learned = self.learned
        # The power from charges of half an hour or more below the top, where cars charge slower.
        if hours >= LEARN_MIN_HOURS and (end is None or end <= LEARN_TOP_SOC):
            power = charge.kwh / hours
            if LEARN_POWER_RANGE[0] <= power <= LEARN_POWER_RANGE[1]:
                learned["power_kw"] = _average(learned.get("power_kw"), power)
                learned["power_samples"] = int(learned.get("power_samples") or 0) + 1
        # The efficiency from charges that raised the battery level by enough to measure it.
        if begin is not None and end is not None and end - begin >= LEARN_MIN_SOC_GAIN and p.capacity > 0:
            efficiency = (end - begin) / 100 * p.capacity / charge.kwh
            if LEARN_EFFICIENCY_RANGE[0] <= efficiency <= LEARN_EFFICIENCY_RANGE[1]:
                learned["efficiency"] = _average(learned.get("efficiency"), efficiency)
                learned["efficiency_samples"] = int(learned.get("efficiency_samples") or 0) + 1
        self.apply_learned()

    def apply_learned(self) -> None:
        """Plan with the learned power and efficiency (when learning is on and enough charges agree)."""
        p = self.planner
        if not p.flags["learn"]:
            return
        learned = self.learned
        if int(learned.get("power_samples") or 0) >= LEARN_MIN_SAMPLES and learned.get("power_kw"):
            p.settings["charge_power_kw"] = round(float(learned["power_kw"]), 1)
        if int(learned.get("efficiency_samples") or 0) >= LEARN_MIN_SAMPLES and learned.get("efficiency"):
            p.settings["efficiency"] = round(float(learned["efficiency"]), 2)

    # -- the monthly summary --------------------------------------------------------------------

    def add_saving(self, run: ChargeRun) -> None:
        saving = run.saving()
        if saving is None or run.ended is None:
            return
        month = dt_util.as_local(run.ended).strftime("%Y-%m")
        self.saved[month] = round(self.saved.get(month, 0.0) + saving, 2)
        for old in sorted(self.saved)[:-SAVED_MONTHS_KEPT]:
            del self.saved[old]

    def monthly(self, now: datetime) -> None:
        """On the first of the month (from 09:00): last month's home charging on the phones, once."""
        p = self.planner
        if not (p.flags["monthly_summary"] and p.notify.targets) or now.day > MONTHLY_DAYS or now.time() < MONTHLY_AT:
            return
        previous = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        if self.summary_sent == previous:
            return
        self.summary_sent = previous
        p.entry.async_create_background_task(
            self.hass, self.async_send_month(previous), "ev_smart_charge_notify_month")

    def month_text(self, month: str) -> str | None:
        """Home charges (kWh, price, price per kWh), the saving from the plans and the public charges of a month."""
        p = self.planner
        unit = p.price_unit or "kr"

        def number(value: float, digits: int) -> str:
            return f"{value:.{digits}f}".replace(".", ",")

        def in_month(charge) -> bool:
            when = dt_util.parse_datetime(charge.ended_at or charge.started_at or "")
            return when is not None and dt_util.as_local(when).strftime("%Y-%m") == month

        charges = [charge for charge in p.charges() if in_month(charge) and charge.ended_at]
        home = [charge for charge in charges if charge.location_kind == "home"]
        public = [charge for charge in charges if charge.location_kind != "home"]
        if not charges:
            return None
        lines = []
        if home:
            kwh = sum(charge.kwh or 0 for charge in home)
            priced = [charge for charge in home if charge.price is not None]
            price = sum(charge.price for charge in priced)
            line = f"Hjemme: {len(home)} ladninger · {number(kwh, 1)} kWh · {number(price, 2)} {unit}"
            priced_kwh = sum(charge.kwh or 0 for charge in priced)
            if priced_kwh > 0:
                line += f" ({number(price / priced_kwh, 2)} {unit}/kWh)"
            lines.append(line)
            if len(priced) < len(home):
                lines.append(f"Uden pris: {len(home) - len(priced)}")
        if (saved := self.saved.get(month)) is not None and abs(saved) >= SAVING_SHOWN:
            lines.append(f"Sparet med ladeplanerne: {number(saved, 2)} {unit}" if saved > 0
                         else f"Ladeplanerne kostede {number(-saved, 2)} {unit} mere end Lad nu")
        if public:
            kwh = sum(charge.kwh or 0 for charge in public)
            price = sum(charge.price or 0 for charge in public)
            lines.append(f"Ude: {len(public)} ladninger · {number(kwh, 1)} kWh · {number(price, 2)} {unit}")
        return "\n".join(lines)

    async def async_send_month(self, month: str, so_far: bool = False) -> None:
        p = self.planner
        text = self.month_text(month)
        if text is None:
            text = "Ingen ladninger."
        year, number = (int(part) for part in month.split("-"))
        name = f"{MONTH_NAMES[number - 1]} {year}" + (" indtil nu" if so_far else "")
        await p.notify.async_send_note("month", f"månedsoversigt {name}", text)

    def new_plug(self) -> None:
        self.run = ChargeRun()

    def restore_run(self, data: Any) -> None:
        """The charge saved before a restart (or a reload of the settings); a run that ended long ago is dropped."""
        if self.run.active or not isinstance(data, dict):
            return
        run = ChargeRun.from_dict(data)
        last = run.ended or run.noted
        if run.active and last and dt_util.utcnow() - last < timedelta(hours=CHARGE_RUN_KEEP_HOURS):
            self.run = run

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
        self.add_saving(run)
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

    def plug_soon(self, now: datetime) -> None:
        """Half an hour before the plan's cheapest start, while the car is home without the cable and low: a reminder
        to plug in (once per start)."""
        p = self.planner
        block = p.schedule.next_block(now)
        if (block is None or block.start <= now or block.start - now > timedelta(minutes=PLUG_SOON_MINUTES)
                or self.soon_sent == block.start or not (p.flags["plug_reminder"] and p.notify.targets)):
            return
        if p.car_home() is False or p.car_plugged() is not False:
            return
        soc = p._battery_soc()
        soc = soc if soc is not None else p.last_soc
        trip_need = p.trip_target_soc if p.trip_active else None
        if soc is None or (soc >= p.settings["reminder_soc"] and (trip_need is None or soc >= trip_need)):
            return
        self.soon_sent = block.start
        cost = p.schedule.cost
        price = f" ({p.notify.money(p, cost)} for {p.schedule.energy_kwh:.1f} kWh)".replace(".", ",") if cost else ""
        text = (f"Billigste ladning starter {p.notify.when(block.start)}{price}. Batteriet er på {soc:.0f} %; "
                "sæt kablet i, så lader den efter planen.")
        p.entry.async_create_background_task(
            self.hass, p.notify.async_send_note("plug_soon", "sæt bilen til", text), "ev_smart_charge_notify_plug_soon")

    def low_price(self, now: datetime) -> None:
        """When the price drops below the chosen level in the daytime and the car is home without the cable and not
        full: a message with Charge now (at most every few hours)."""
        p = self.planner
        slot = next((item for item in p.timeline if item.start <= now < item.end and not item.estimated), None)
        price = slot.price * p.settings["price_factor"] if slot else None
        below = price is not None and price < p.settings["low_price"]
        was_below, self._was_below = self._was_below, below
        if not below or was_below or not (p.flags["low_price_alert"] and p.notify.targets):
            return
        if not LOW_PRICE_FROM <= now.time() < LOW_PRICE_UNTIL:
            return
        if self.low_sent and now - self.low_sent < timedelta(hours=LOW_PRICE_EVERY_HOURS):
            return
        if p.car_home() is False or p.car_plugged() is True or p.car_full:
            return
        soc = p._battery_soc()
        if soc is not None and soc >= p.target - 5:
            return
        self.low_sent = now
        unit = p.price_unit or "kr"
        def kr(value: float) -> str:
            return f"{value:.2f}".replace(".", ",")

        text = (f"Strømmen er billig nu: {kr(price)} {unit}/kWh (under {kr(p.settings['low_price'])})."
                + (f" Batteriet er på {soc:.0f} %." if soc is not None else "") + " Sæt bilen til og tryk Lad nu.")
        actions = [{"action": f"{p.notify.prefix}NOW", "title": "Lad nu"}]
        p.entry.async_create_background_task(
            self.hass, p.notify.async_send_note("low_price", "strømmen er billig", text, actions),
            "ev_smart_charge_notify_low_price")

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
                same_car = [device.id for device in all_devices(devices)
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
