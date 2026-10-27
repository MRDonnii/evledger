"""Charging stations around a point, for dashboards such as the Tesla card's map (Claude AI, 2026-10-27; user: "kan
det også vise de her lade steder på kortet … trykke på lade stedet og se prisen").

`evledger/chargers` takes any EV Ledger entity (for the car's ledger), a point and a radius, and answers with the
Superchargers, the operators' own AFIR stations with their prices (afir.py), Open Charge Map's stations (with the
ledger's own key, which never leaves Home Assistant), the places the user named and the zones with a price, each with the
price per kWh the ledger would use there, and how far the AFIR feeds are ("afir": feeds, sites, fetched, loading)."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .afir import afir
from .const import DOMAIN

MAX_RADIUS_KM = 25


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_chargers)


@websocket_api.websocket_command({
    vol.Required("type"): "evledger/chargers",
    vol.Required("entity_id"): str,
    vol.Required("latitude"): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
    vol.Required("longitude"): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
    vol.Optional("radius_km", default=5.0): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=MAX_RADIUS_KM)),
})
@websocket_api.async_response
async def ws_chargers(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    entry = er.async_get(hass).async_get(msg["entity_id"])
    coordinator = hass.data.get(DOMAIN, {}).get(entry.config_entry_id) if entry else None
    if coordinator is None:
        connection.send_error(msg["id"], "not_found", f"{msg['entity_id']} is not an EV Ledger entity")
        return
    stations = await coordinator.async_stations_around(msg["latitude"], msg["longitude"], msg["radius_km"])
    connection.send_result(msg["id"], {
        "stations": stations, "open_charge_map": bool(coordinator.ocm_api_key), "currency": coordinator.currency,
        "default_price": coordinator.public_default_price(),
        "afir": afir(hass).status(coordinator.afir_credentials),
    })
