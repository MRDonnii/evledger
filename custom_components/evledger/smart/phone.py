"""Confirm the charge plan on the phone: an actionable notification to the chosen Companion apps."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import time
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import (
    CONF_NOTIFY_ICON,
    CONF_NOTIFY_ONLY_HOME,
    CONF_NOTIFY_SERVICES,
    CONF_NOTIFY_URL,
    CONFIRM_TIMEOUT_MINUTES,
    DEFAULT_NOTIFY_ICON,
    NOTIFY_COLOR,
)
from .plan import MODE_FIXED, MODE_MANUAL, MODE_NOW, MODE_OFF, MODE_PRICE_CAP, MODE_SMART, fixed_window

if TYPE_CHECKING:
    from .planner import ChargePlanner

_LOGGER = logging.getLogger(__name__)

ACTION_EVENT = "mobile_app_notification_action"
# Messages that only tell what happens (plan active, charging started, charge done) arrive without sound at night;
# questions and warnings keep their sound.
QUIET_FROM = time(22, 0)
QUIET_UNTIL = time(7, 0)
QUIET_KINDS = ("start", "done")
ANSWERS = {"NOW": MODE_NOW, "OFF": MODE_OFF}
NAMES = {MODE_SMART: "Billigst", MODE_FIXED: "Fast tid", MODE_NOW: "Lad nu", MODE_PRICE_CAP: "Prisloft",
         MODE_MANUAL: "Manuel"}


def tracker_for(service: str) -> str:
    """The Companion app's device tracker for its notify service (notify.mobile_app_<device>)."""
    return f"device_tracker.{service.removeprefix('notify.').removeprefix('mobile_app_')}"


class PhoneNotifier:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, options: Callable[[], dict]) -> None:
        self.hass = hass
        self.entry = entry
        self._options = options
        self.prefix = f"EVSC_{entry.entry_id}_"
        self.tag = f"ev_smart_charge_{entry.entry_id}"

    @property
    def options(self) -> dict:
        return self._options()

    @property
    def targets(self) -> list[str]:
        return [service.removeprefix("notify.") for service in self.options.get(CONF_NOTIFY_SERVICES) or []]

    def recipients(self) -> list[str]:
        """The chosen phones; with "only when home", the ones whose app reports being home."""
        if not self.options.get(CONF_NOTIFY_ONLY_HOME):
            return self.targets
        result = []
        for service in self.targets:
            tracker = self.hass.states.get(tracker_for(service))
            if tracker is None or tracker.state == STATE_HOME:  # unknown phone location: still ask
                result.append(service)
        return result

    @callback
    def async_listen(self, planner: ChargePlanner) -> CALLBACK_TYPE:
        @callback
        def on_action(event: Event) -> None:
            action = str(event.data.get("action", ""))
            if not action.startswith(self.prefix):
                return
            answer = action.removeprefix(self.prefix)
            _LOGGER.debug("Phone answered %s", answer)
            if answer == "CONFIRM":
                planner.async_answer(planner.mode)  # the plan that waits for the answer, e.g. the default plan
            elif answer in ("CAP_OK", "CAP_STOP"):
                planner.async_set_cap_override(answer == "CAP_OK")
            elif answer in ("PRE_ON", "PRE_SKIP"):
                planner.routines.answer_precondition(answer == "PRE_ON")
            elif mode := ANSWERS.get(answer):
                planner.async_answer(mode)

        return self.hass.bus.async_listen(ACTION_EVENT, on_action)

    def message(self, planner: ChargePlanner) -> str:
        return f"{self.plan_text(planner)}\nUden svar kører planen om {CONFIRM_TIMEOUT_MINUTES} min."

    def plan_text(self, planner: ChargePlanner) -> str:
        """The active plan, one fact per line: plan, time, price, energy and target."""
        unit = planner.price_unit or "kr"
        schedule = planner.schedule

        def money(value: float) -> str:
            return f"{value:.2f} {unit}".replace(".", ",")

        def when(value) -> str:
            local = dt_util.as_local(value)
            days = (local.date() - dt_util.now().date()).days
            day = {0: "", 1: "i morgen "}.get(days, local.strftime("%d.%m. "))
            return f"{day}{local.strftime('%H:%M')}"

        if planner.mode == MODE_OFF:
            return "Plan: Pause\nBilen lades ikke."
        lines = [f"Plan: {NAMES.get(planner.mode, planner.mode)}"]
        if not schedule.blocks:
            lines.append("Batteriet er allerede ladet til målet.")
            return "\n".join(lines)
        first, last = schedule.blocks[0].start, schedule.blocks[-1].end
        start = "nu" if first <= dt_util.now() else when(first)
        same_day = dt_util.as_local(first).date() == dt_util.as_local(last).date()
        end = dt_util.as_local(last).strftime("%H:%M") if same_day else when(last)
        time_line = f"Tid: {start} – {end}"
        if len(schedule.blocks) > 1:
            time_line += f" ({len(schedule.blocks)} perioder)"
        lines.append(time_line)
        price = f"Pris: {money(schedule.cost or 0)}"
        now = planner.alternatives.get(MODE_NOW)
        saving = now.cost - schedule.cost if now and now.cost and schedule.cost is not None else 0
        if planner.mode != MODE_NOW and saving >= 0.5:
            price += f" (spar {money(saving)})"
        lines.append(price)
        energy = f"Energi: {schedule.energy_kwh:.1f} kWh".replace(".", ",")
        if schedule.target_soc is not None:
            energy += f" · mål {schedule.target_soc:.0f} %"
        lines.append(energy)
        if planner.deadline and planner.mode in (MODE_SMART, MODE_PRICE_CAP):
            lines.append(f"Klar senest: {when(planner.deadline)}")
        return "\n".join(lines)

    def money(self, planner: ChargePlanner, value: float, per_kwh: bool = False) -> str:
        unit = planner.price_unit or "kr"
        return f"{value:.2f} {unit}{'/kWh' if per_kwh else ''}".replace(".", ",")

    @staticmethod
    def when(value) -> str:
        local = dt_util.as_local(value)
        days = (local.date() - dt_util.now().date()).days
        day = {0: "kl. ", 1: "i morgen kl. "}.get(days, local.strftime("%d.%m. kl. "))
        return f"{day}{local.strftime('%H:%M')}"

    def start_text(self, planner: ChargePlanner, now) -> str:
        """Charging started: the plan, when this charging period ends, the expected price and the target."""
        schedule = planner.schedule
        lines = [f"Plan: {NAMES.get(planner.mode, planner.mode)}"]
        block = schedule.next_block(now)
        if block is not None:
            end = f"Slut ca. {self.when(block.end)}"
            if len(schedule.blocks) > 1:
                end += f" (periode 1 af {len(schedule.blocks)})"
            lines.append(end)
        if schedule.cost is not None and schedule.energy_kwh > 0:
            energy = f"{schedule.energy_kwh:.1f}".replace(".", ",")
            lines.append(f"Forventet pris: {self.money(planner, schedule.cost)} for {energy} kWh")
        soc = planner._battery_soc()
        if schedule.target_soc is not None:
            lines.append(f"Mål {schedule.target_soc:.0f} %" + (f" (nu {soc:.0f} %)" if soc is not None else ""))
        return "\n".join(lines)

    def cap_text(self, planner: ChargePlanner, now) -> str:
        """The price cap cannot reach the target in time: what the cap reaches, and what exceeding it costs."""
        schedule = planner.schedule
        goal = planner.goal_time(now)
        by = f" {self.when(goal)}" if goal else ""
        lines = [
            f"Under prisloftet ({self.money(planner, planner.settings['price_cap'], True)}) når bilen ca. "
            f"{schedule.cap_soc or 0:.0f} %{' inden' + by if by else ''}.",
            f"For at nå {schedule.target_soc or planner.target:.0f} % skal der lades "
            f"{schedule.over_cap_kwh:.1f} kWh over loftet".replace(".", ",")
            + (f", til op til {self.money(planner, schedule.over_cap_max_price, True)}"
               if schedule.over_cap_max_price is not None else "")
            + f" (ca. {self.money(planner, schedule.over_cap_extra)} ekstra).",
            "Uden svar lader den videre til målet.",
        ]
        return "\n".join(lines)

    def short_text(self, planner: ChargePlanner, now, reach: float, target: float) -> str:
        """The plan cannot reach its target in time (plugged in late, fixed window too short)."""
        if planner.mode == MODE_FIXED:
            begin, end = fixed_window(now, planner.times["fixed_start"], planner.times["fixed_end"])
            return (f"Fast tid {dt_util.as_local(begin).strftime('%H:%M')}–{dt_util.as_local(end).strftime('%H:%M')} "
                    f"er for kort: bilen når ca. {reach:.0f} % af målet {target:.0f} %.")
        goal = planner.goal_time(now)
        return (f"Bilen når ca. {reach:.0f} % af målet {target:.0f} %{' ' + self.when(goal) if goal else ''}. "
                "Planen lader så meget, den kan.")

    def _tap(self) -> dict:
        """Open a dashboard page when the notification itself is tapped (iOS: url, Android: clickAction),
        and show an EV charging icon instead of the app icon (iOS: rounded avatar, Android: small icon)."""
        url = self.options.get(CONF_NOTIFY_URL)
        data = {"url": url, "clickAction": url} if url else {}
        data |= {"notification_icon": self.options.get(CONF_NOTIFY_ICON) or DEFAULT_NOTIFY_ICON,
                 "notification_icon_color": "white", "color": NOTIFY_COLOR}
        return data

    @staticmethod
    def quiet() -> dict:
        """iOS "passive" at night: into the notification centre without sound or lighting the screen."""
        now = dt_util.now().time()
        return {"push": {"interruption-level": "passive"}} if now >= QUIET_FROM or now < QUIET_UNTIL else {}

    async def async_send_info(self, planner: ChargePlanner) -> None:
        """Tell the phones which plan is active, with times and price; the same tag replaces an older one."""
        data = {
            "title": f"{self.entry.title}: ladeplan aktiv",
            "message": self.plan_text(planner),
            "data": {
                **self._tap(),
                **self.quiet(),
                "tag": self.tag,
                "actions": [
                    {"action": f"{self.prefix}NOW", "title": "Lad nu"},
                    {"action": f"{self.prefix}OFF", "title": "Pause"},
                ],
            },
        }
        for service in self.recipients():
            await self._call(service, data)

    async def async_send_alert(self, text: str) -> None:
        """Something stands in the way of the plan; the same tag replaces the plan message."""
        data = {
            "title": f"{self.entry.title}: opladning",
            "message": text,
            "data": {**self._tap(), "tag": self.tag,
                     "actions": [{"action": f"{self.prefix}NOW", "title": "Lad nu"}]},
        }
        for service in self.recipients():
            await self._call(service, data)

    async def async_send_precondition(self, goal) -> None:
        """Ask before the car's climate is turned on: without an answer nothing happens."""
        data = {
            "title": f"{self.entry.title}: forvarm bilen?",
            "message": f"Afgang {self.when(goal)}. Klimaet tændes kun, hvis du trykker Forvarm.",
            "data": {**self._tap(), "tag": f"{self.tag}_precondition",
                     "actions": [{"action": f"{self.prefix}PRE_ON", "title": "Forvarm"},
                                 {"action": f"{self.prefix}PRE_SKIP", "title": "Spring over"}]},
        }
        for service in self.recipients():
            await self._call(service, data)

    async def async_send_warning(self, kind: str, text: str) -> None:
        """A warning under its own tag, so it does not replace the plan message. The price cap question
        has Approve / Stop above the cap; the others Charge now."""
        if kind == "cap":
            title = f"{self.entry.title}: prisloftet rækker ikke"
            actions = [{"action": f"{self.prefix}CAP_OK", "title": "Godkend"},
                       {"action": f"{self.prefix}CAP_STOP", "title": "Stop over loftet"}]
        else:
            title = f"{self.entry.title}: når ikke målet"
            actions = [{"action": f"{self.prefix}NOW", "title": "Lad nu"}] if kind == "short" else []
        data = {"title": title, "message": text,
                "data": {**self._tap(), "tag": f"{self.tag}_{kind}", "actions": actions}}
        for service in self.recipients():
            await self._call(service, data)

    async def async_send_note(self, kind: str, title: str, text: str, actions: list[dict] | None = None) -> None:
        """A message under its own tag: charging started (with Pause), the charge is done, a reminder to plug in,
        the charger is offline."""
        data = {"title": f"{self.entry.title}: {title}", "message": text,
                "data": {**self._tap(), "tag": f"{self.tag}_{kind}", **({"actions": actions} if actions else {}),
                         **(self.quiet() if kind in QUIET_KINDS else {})}}
        for service in self.recipients():
            await self._call(service, data)

    async def async_send_plan(self, planner: ChargePlanner) -> None:
        data = {
            "title": f"{self.entry.title} er sat til opladning",
            "message": self.message(planner),
            "data": {
                **self._tap(),
                "tag": self.tag,
                "actions": [
                    {"action": f"{self.prefix}CONFIRM", "title": f"Bekræft {NAMES.get(planner.mode, 'plan').lower()}"},
                    {"action": f"{self.prefix}NOW", "title": "Lad nu"},
                    {"action": f"{self.prefix}OFF", "title": "Pause"},
                ],
            },
        }
        for service in self.recipients():
            await self._call(service, data)

    async def async_clear(self) -> None:
        for service in self.targets:
            await self._call(service, {"message": "clear_notification", "data": {"tag": self.tag}})

    async def _call(self, service: str, data: dict) -> None:
        try:
            await self.hass.services.async_call("notify", service, data, blocking=True)
        except (HomeAssistantError, ValueError) as err:
            _LOGGER.warning("notify.%s failed: %s", service, err)
