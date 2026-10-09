"""Confirm the charge plan on the phone: an actionable notification to the chosen Companion apps."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .const import CONF_NOTIFY_ONLY_HOME, CONF_NOTIFY_SERVICES, CONFIRM_TIMEOUT_MINUTES
from .plan import MODE_FIXED, MODE_NOW, MODE_OFF, MODE_PRICE_CAP, MODE_SMART

if TYPE_CHECKING:
    from .planner import ChargePlanner

_LOGGER = logging.getLogger(__name__)

ACTION_EVENT = "mobile_app_notification_action"
ANSWERS = {"CONFIRM": MODE_SMART, "NOW": MODE_NOW, "OFF": MODE_OFF}


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
            if action.startswith(self.prefix) and (mode := ANSWERS.get(action.removeprefix(self.prefix))):
                _LOGGER.debug("Phone answered %s", mode)
                planner.async_answer(mode)

        return self.hass.bus.async_listen(ACTION_EVENT, on_action)

    def message(self, planner: ChargePlanner) -> str:
        cheapest = planner.alternatives.get(MODE_SMART)
        now = planner.alternatives.get(MODE_NOW)
        unit = planner.price_unit or "kr"
        lines = []
        if cheapest and cheapest.blocks:
            start = dt_util.as_local(cheapest.blocks[0].start).strftime("%H:%M")
            end = dt_util.as_local(cheapest.blocks[-1].end).strftime("%H:%M")
            lines.append(f"Billigst: {start}–{end}, {cheapest.cost:.2f} {unit}".replace(".", ","))
        elif cheapest:
            lines.append("Billigst: batteriet er allerede ladet til målet")
        if now and now.cost is not None:
            lines.append(f"Lad nu: {now.cost:.2f} {unit}".replace(".", ","))
        if planner.deadline:
            lines.append(f"Klar {dt_util.as_local(planner.deadline).strftime('%H:%M')}")
        lines.append(f"Uden svar kører Billigst om {CONFIRM_TIMEOUT_MINUTES} min.")
        return "\n".join(lines)

    def plan_text(self, planner: ChargePlanner) -> str:
        """The active plan in one or two lines: what, when, what it costs."""
        unit = planner.price_unit or "kr"
        schedule = planner.schedule

        def money(value: float) -> str:
            return f"{value:.2f} {unit}".replace(".", ",")

        def clock(value) -> str:
            return dt_util.as_local(value).strftime("%H:%M")

        names = {MODE_SMART: "Billigst", MODE_FIXED: "Fast tid", MODE_NOW: "Lad nu", MODE_PRICE_CAP: "Prisloft"}
        if planner.mode == MODE_OFF:
            return "Pause: bilen lades ikke."
        if not schedule.blocks:
            return f"{names.get(planner.mode, planner.mode)}: batteriet er allerede ladet til målet."
        span = f"{clock(schedule.blocks[0].start)}–{clock(schedule.blocks[-1].end)}"
        if len(schedule.blocks) > 1:
            span += f" ({len(schedule.blocks)} perioder)"
        line = f"{names.get(planner.mode, planner.mode)}: {span} · {money(schedule.cost or 0)}"
        now = planner.alternatives.get(MODE_NOW)
        saving = now.cost - schedule.cost if now and now.cost and schedule.cost is not None else 0
        if planner.mode != MODE_NOW and saving >= 0.5:
            line += f" (spar {money(saving)})"
        details = [f"{schedule.energy_kwh:.1f} kWh".replace(".", ",")]
        if schedule.target_soc is not None:
            details.append(f"mål {schedule.target_soc:.0f} %")
        if planner.deadline and planner.mode == MODE_SMART:
            details.append(f"klar {clock(planner.deadline)}")
        return f"{line}\n" + " · ".join(details)

    async def async_send_info(self, planner: ChargePlanner) -> None:
        """Tell the phones which plan is active, with times and price; the same tag replaces an older one."""
        data = {
            "title": f"{self.entry.title}: ladeplan aktiv",
            "message": self.plan_text(planner),
            "data": {
                "tag": self.tag,
                "actions": [
                    {"action": f"{self.prefix}NOW", "title": "Lad nu"},
                    {"action": f"{self.prefix}OFF", "title": "Pause"},
                ],
            },
        }
        for service in self.recipients():
            await self._call(service, data)

    async def async_send_plan(self, planner: ChargePlanner) -> None:
        data = {
            "title": f"{self.entry.title} er sat til opladning",
            "message": self.message(planner),
            "data": {
                "tag": self.tag,
                "actions": [
                    {"action": f"{self.prefix}CONFIRM", "title": "Bekræft billigst"},
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
