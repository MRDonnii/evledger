"""Switches: round trip for the temporary trip, confirming new plans on the phone, plan messages, exceeding the
price cap to reach the target, and the everyday helpers (done message, plug-in reminder, weekend ready-by time,
preconditioning)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import departures
from .entity import EvSmartChargeListenerEntity


def build(planner) -> list:
    return list([TripRoundTrip(planner, "trip_round_trip"),
                        ConfirmOnPhone(planner, "confirm_on_phone"),
                        NotifyPlan(planner, "notify_plan"),
                        ExceedPriceCap(planner, "price_cap_override"),
                        *(PlanFlag(planner, key) for key in FLAG_ICONS)])


FLAG_ICONS = {"notify_start": "mdi:ev-station", "notify_done": "mdi:battery-check",
              "plug_reminder": "mdi:power-plug-outline",
              "weekend_ready_by": "mdi:calendar-weekend", "precondition": "mdi:car-defrost-front",
              "learn": "mdi:school-outline", "monthly_summary": "mdi:calendar-month-outline",
              "low_price_alert": "mdi:cash-check", "wait_cheaper_day": "mdi:calendar-arrow-right",
              "learn_departure": "mdi:clock-start", "prefer_green": "mdi:leaf",
              "ask_public_price": "mdi:message-reply-text-outline", "morning_check": "mdi:alarm-check"}


class PlanFlag(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    """An on/off setting of the planner (planner.flags)."""

    def __init__(self, planner, key: str) -> None:
        super().__init__(planner, key)
        self._attr_icon = FLAG_ICONS[key]
        if key in ("notify_start", "notify_done", "plug_reminder", "learn", "monthly_summary", "low_price_alert",
                   "wait_cheaper_day", "learn_departure", "prefer_green", "ask_public_price", "morning_check"):
            self._attr_entity_category = EntityCategory.CONFIG

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self.planner.flags[self._attr_translation_key] = last.state == "on"
        if last and self._attr_translation_key == "plug_reminder":
            # The evening check is made once a day, also across a restart in its window.
            checked = dt_util.parse_date(str(last.attributes.get("checked_on") or ""))
            if checked:
                self.planner.routines.evening_checked = checked
        routines = self.planner.routines
        if last and self._attr_translation_key == "notify_done":
            routines.restore_run(last.attributes.get("charge_run"))
        if last and self._attr_translation_key == "learn" and isinstance(last.attributes.get("learned"), dict):
            routines.learned = {key: value for key, value in last.attributes["learned"].items()
                                if isinstance(value, (int, float))}
            routines.apply_learned()
        if last and self._attr_translation_key == "monthly_summary":
            routines.summary_sent = last.attributes.get("sent_for") or None
            if isinstance(last.attributes.get("saved"), dict):
                routines.saved = {str(month): float(value) for month, value in last.attributes["saved"].items()
                                  if isinstance(value, (int, float))}

    @property
    def is_on(self) -> bool:
        return self.planner.flags[self._attr_translation_key]

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        routines = self.planner.routines
        if self._attr_translation_key == "plug_reminder":
            return {"checked_on": routines.evening_checked.isoformat() if routines.evening_checked else None}
        if self._attr_translation_key == "notify_done":
            # The periods of the charge so far, for the done message after a restart.
            return {"charge_run": routines.run.as_dict() if routines.run.active else None}
        if self._attr_translation_key == "learn":
            # What was learned from the ledger's charges, and how many charges it rests on.
            return {"learned": {key: round(value, 3) for key, value in routines.learned.items()}}
        if self._attr_translation_key == "monthly_summary":
            return {"sent_for": routines.summary_sent, "saved": routines.saved}
        if self._attr_translation_key == "precondition":
            # The car's climate entity (needs a car integration that can send commands to the car).
            return {"climate_entity": routines.climate_entity()}
        planner = self.planner
        if self._attr_translation_key == "wait_cheaper_day":
            waiting = planner.waiting
            use = planner.daily_use_kwh(dt_util.now())
            return {"waiting_for": {**waiting, "deadline": waiting["deadline"].isoformat()} if waiting else None,
                    "daily_use_kwh": round(use, 1) if use is not None else None}
        if self._attr_translation_key == "learn_departure":
            learned = planner.learned_departures
            return {"departures": departures.as_text(learned.get("times")),
                    "history_days": learned.get("history_days"), "next_ready_by": planner.deadline}
        if self._attr_translation_key == "prefer_green":
            return {"price_area": planner.co2_area, "plan_co2": planner.schedule.co2,
                    "co2_updated": planner.co2_updated}
        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_flag(self._attr_translation_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_flag(self._attr_translation_key, False)


class TripRoundTrip(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    _attr_icon = "mdi:swap-horizontal"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self.planner.trip.round_trip = last.state == "on"

    @property
    def is_on(self) -> bool:
        return self.planner.trip.round_trip

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_trip_round_trip(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_trip_round_trip(False)


class ConfirmOnPhone(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    """When on, a plugged-in car waits for an answer on the chosen phones before charging."""

    _attr_icon = "mdi:cellphone-check"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state == "on":
            self.planner.confirm_enabled = True
            # A question still open before a restart stays open.
            if since := dt_util.parse_datetime(str(last.attributes.get("awaiting_since") or "")):
                self.planner.awaiting_since = since

    @property
    def is_on(self) -> bool:
        return self.planner.confirm_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "awaiting_since": self.planner.awaiting_since.isoformat() if self.planner.awaiting_since else None,
            "phones": self.planner.notify.targets,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_confirm(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_confirm(False)


class NotifyPlan(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    """When on, the phones are told which plan is active, with times and price (and Charge now / Pause)."""

    _attr_icon = "mdi:cellphone-message"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self.planner.info_enabled = last.state == "on"

    @property
    def is_on(self) -> bool:
        return self.planner.info_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_info(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_info(False)


class ExceedPriceCap(EvSmartChargeListenerEntity, SwitchEntity, RestoreEntity):
    """Price cap: when the slots below the cap cannot reach the target by the ready-by time, the plan also
    charges above it (the phones are asked first; without an answer it goes on). Off keeps to the cap for
    this plug-in; plugging in again turns it back on."""

    _attr_icon = "mdi:cash-lock-open"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self.planner.cap_override = last.state == "on"

    @property
    def is_on(self) -> bool:
        return self.planner.cap_override

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        schedule = self.planner.schedule
        return {
            "over_cap_kwh": schedule.over_cap_kwh,
            "over_cap_max_price": schedule.over_cap_max_price,
            "over_cap_extra": schedule.over_cap_extra,
            "cap_soc": round(schedule.cap_soc, 1) if schedule.cap_soc is not None else None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.planner.async_set_cap_override(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.planner.async_set_cap_override(False)
