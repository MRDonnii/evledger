"""EV Ledger's events in the logbook: a home charge finished, and a tyre change."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN

EVENT_CHARGE_FINISHED = f"{DOMAIN}_charge_finished"
EVENT_TYRES_CHANGED = f"{DOMAIN}_tyres_changed"

TEXTS = {
    "en": {"charged": "finished charging at home: {kwh} kWh", "for": " for {price} {currency}",
           "no_price": " (price not known yet)", "tyres": "got the tyre set {set}", "odometer": " at {km} km"},
    "da": {"charged": "ladede færdig hjemme: {kwh} kWh", "for": " for {price} {currency}",
           "no_price": " (prisen kendes ikke endnu)", "tyres": "fik hjulsættet {set} på", "odometer": " ved {km} km"},
}


def _number(value: float, digits: int, language: str) -> str:
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if language == "da" else text


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    language = "da" if str(hass.config.language or "").startswith("da") else "en"
    texts = TEXTS[language]

    @callback
    def describe_charge(event: Event) -> dict[str, str]:
        data = event.data
        message = texts["charged"].format(kwh=_number(float(data.get("kwh") or 0.0), 1, language))
        if data.get("price") is not None:
            message += texts["for"].format(price=_number(float(data["price"]), 2, language),
                                           currency=data.get("price_currency") or "")
        else:
            message += texts["no_price"]
        return {LOGBOOK_ENTRY_NAME: data.get("vehicle") or "EV Ledger", LOGBOOK_ENTRY_MESSAGE: message}

    @callback
    def describe_tyres(event: Event) -> dict[str, str]:
        data = event.data
        message = texts["tyres"].format(set=data.get("set") or data.get("set_id") or "?")
        if data.get("odometer_km") is not None:
            message += texts["odometer"].format(km=_number(float(data["odometer_km"]), 0, language))
        return {LOGBOOK_ENTRY_NAME: data.get("vehicle") or "EV Ledger", LOGBOOK_ENTRY_MESSAGE: message}

    async_describe_event(DOMAIN, EVENT_CHARGE_FINISHED, describe_charge)
    async_describe_event(DOMAIN, EVENT_TYRES_CHANGED, describe_tyres)
