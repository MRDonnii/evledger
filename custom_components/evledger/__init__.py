"""The EV Ledger integration."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    ATTR_ENDED_AT,
    ATTR_LOCATION,
    SERVICE_ADD_CHARGE,
    SERVICE_CLEAR_READY_BY_ONCE,
    SERVICE_SET_READY_BY_ONCE,
    SERVICE_FORGET_PLACE,
    SERVICE_SET_OPERATOR_PRICE,
    SERVICE_SET_ZONE_PRICE,
    SERVICE_SET_WEEK_PLAN,
    ATTR_CHARGE_ID,
    ATTR_NAME,
    ATTR_OPERATOR,
    ATTR_ZONE,
    ATTR_KWH,
    ATTR_LOCATION_NAME,
    ATTR_NOTE,
    ATTR_PRICE,
    ATTR_STARTED_AT,
    ATTR_TRIP_ID,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_OCM_API_KEY,
    CONF_CURRENCY,
    CONF_VEHICLE_NAME,
    DEFAULT_CURRENCY,
    DOMAIN,
    LOCATION_HOME,
    PLATFORMS,
    SMART_PLATFORMS,
    SERVICE_DELETE_CHARGE,
    SERVICE_DELETE_TRIP,
    SERVICE_LOG_PUBLIC_CHARGE,
    SERVICE_UPDATE_CHARGE,
    SERVICE_CHANGE_TYRES,
    SERVICE_DELETE_TREAD_DEPTH,
    SERVICE_DELETE_TYRE_CHANGE,
    SERVICE_DELETE_TYRE_SET,
    SERVICE_LOG_TREAD_DEPTH,
    SERVICE_SAVE_TYRE_SET,
    SERVICE_UPDATE_TYRE_CHANGE,
    TYRE_SERVICES,
)
from . import issues
from .coordinator import EvLedgerCoordinator
from .providers.registry import build_charger_providers, build_vehicle_provider
from .smart import week as week_plan
from .smart.ent_datetime import set_once
from .smart.planner import ChargePlanner
from .smart.setup import ledger_vehicle, planner_options, smart_enabled
from .store import EvLedgerStore
from .tyres import KINDS as TYRE_KINDS

_LOGGER = logging.getLogger(__name__)

# Numbers from a service call: within these (a range also turns away NaN and infinity).
_KWH = vol.All(vol.Coerce(float), vol.Range(min=0, max=1000))
_MONEY = vol.All(vol.Coerce(float), vol.Range(min=-100_000, max=100_000))
_KM = vol.All(vol.Coerce(float), vol.Range(min=0, max=10_000_000))

LOG_PUBLIC_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_KWH): _KWH,
        vol.Required(ATTR_PRICE): _MONEY,
        vol.Optional(ATTR_LOCATION_NAME): cv.string,
        vol.Optional(ATTR_STARTED_AT): cv.datetime,
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

SET_ZONE_PRICE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_ZONE): cv.entity_domain("zone"),
        vol.Optional(ATTR_PRICE): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0, max=100))),
    }
)

SET_OPERATOR_PRICE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_OPERATOR): cv.string,
        vol.Optional(ATTR_PRICE): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=0, max=100))),
    }
)

FORGET_PLACE_SCHEMA = vol.Schema({vol.Required("entry_id"): cv.string, vol.Required(ATTR_NAME): cv.string})

ADD_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_KWH): _KWH,
        vol.Optional(ATTR_PRICE): _MONEY,
        vol.Required(ATTR_STARTED_AT): cv.datetime,
        vol.Optional(ATTR_ENDED_AT): cv.datetime,
        vol.Optional(ATTR_LOCATION, default="home"): vol.In(["home", "public"]),
        vol.Optional(ATTR_LOCATION_NAME): cv.string,
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

CAR_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_id})
SET_WEEK_PLAN_SCHEMA = CAR_SCHEMA.extend({
    vol.Required("days"): dict,
    vol.Optional("enabled"): cv.boolean,
})
SET_READY_BY_ONCE_SCHEMA = CAR_SCHEMA.extend({
    vol.Required("at"): cv.datetime,
    vol.Optional("target"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=50, max=100))),
})

_TEXT = vol.Any(None, cv.string)
_OPT_MONEY = vol.Any(None, _MONEY)
TYRE_FIELDS = {
    vol.Optional("name"): vol.All(cv.string, vol.Length(min=1, max=40)),
    vol.Optional("kind"): vol.In(TYRE_KINDS),
    vol.Optional("brand"): _TEXT,
    vol.Optional("model"): _TEXT,
    vol.Optional("width_mm"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=100, max=400))),
    vol.Optional("aspect_pct"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=20, max=95))),
    vol.Optional("rim_inch"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=10, max=26))),
    vol.Optional("load_speed"): _TEXT,
    vol.Optional("dot"): _TEXT,
    vol.Optional("rim_name"): _TEXT,
    vol.Optional("rim_width_in"): vol.Any(None, vol.All(vol.Coerce(float), vol.Range(min=3, max=14))),
    vol.Optional("rim_offset_mm"): vol.Any(None, vol.All(vol.Coerce(int), vol.Range(min=-50, max=90))),
    vol.Optional("purchased"): vol.Any(None, cv.date),
    vol.Optional("price"): _OPT_MONEY,
    vol.Optional("km_before"): vol.Any(None, _KM),
    vol.Optional("storage"): _TEXT,
    vol.Optional("note"): _TEXT,
    vol.Optional("retired"): cv.boolean,
}
SAVE_TYRE_SET_SCHEMA = CAR_SCHEMA.extend({vol.Optional("set_id"): cv.string, **TYRE_FIELDS})
TYRE_SET_SCHEMA = CAR_SCHEMA.extend({vol.Required("set_id"): cv.string})
CHANGE_TYRES_SCHEMA = CAR_SCHEMA.extend({
    vol.Required("set_id"): cv.string,
    vol.Optional("at"): cv.datetime,
    vol.Optional("odometer_km"): _KM,
    vol.Optional("note"): cv.string,
})
UPDATE_TYRE_CHANGE_SCHEMA = CAR_SCHEMA.extend({
    vol.Required("change_id"): cv.string,
    vol.Optional("at"): cv.datetime,
    vol.Optional("odometer_km"): _KM,
    vol.Optional("note"): cv.string,
})
TYRE_CHANGE_SCHEMA = CAR_SCHEMA.extend({vol.Required("change_id"): cv.string})
_DEPTH = vol.All(vol.Coerce(float), vol.Range(min=0, max=20))
LOG_TREAD_SCHEMA = vol.All(CAR_SCHEMA.extend({
    vol.Required("set_id"): cv.string,
    vol.Optional("mm"): _DEPTH,
    vol.Optional("each"): vol.All(cv.ensure_list, [_DEPTH], vol.Length(min=1, max=4)),
    vol.Optional("at"): cv.datetime,
    vol.Optional("odometer_km"): _KM,
}), cv.has_at_least_one_key("mm", "each"))
DELETE_TREAD_SCHEMA = CAR_SCHEMA.extend({vol.Required("set_id"): cv.string, vol.Required("reading_id"): cv.string})
DELETE_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_CHARGE_ID): cv.string,
    }
)

UPDATE_CHARGE_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_CHARGE_ID): cv.string,
        vol.Optional(ATTR_KWH): _KWH,
        vol.Optional(ATTR_PRICE): _MONEY,
        vol.Optional(ATTR_LOCATION_NAME): cv.string,
        vol.Optional(ATTR_NOTE): cv.string,
    }
)

DELETE_TRIP_SCHEMA = vol.Schema(
    {
        vol.Required("entry_id"): cv.string,
        vol.Required(ATTR_TRIP_ID): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Ledger from a config entry."""
    store = EvLedgerStore(hass, entry.entry_id)
    await store.async_load()

    vehicle_provider = build_vehicle_provider(entry.data)
    charger_providers = build_charger_providers(entry.data)

    coordinator = EvLedgerCoordinator(
        hass,
        entry.entry_id,
        vehicle_name=entry.data[CONF_VEHICLE_NAME],
        currency=entry.data.get(CONF_CURRENCY, DEFAULT_CURRENCY),
        vehicle_provider=vehicle_provider,
        charger_providers=charger_providers,
        store=store,
    )
    coordinator.battery_capacity_kwh = entry.data.get(CONF_BATTERY_CAPACITY_KWH)
    coordinator.ocm_api_key = (entry.data.get(CONF_OCM_API_KEY) or "").strip() or None
    # A failing first ledger update (e.g. the car's cloud is down right after a restart) must not hold
    # back the charger control; the ledger sensors catch up on the next update.
    await coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Smart charging (charge plans and charger control) is optional and off by default.
    coordinator.smart = None
    coordinator.platforms = list(PLATFORMS)
    if smart_enabled(entry):
        options = planner_options(hass, entry)
        coordinator.smart = ChargePlanner(
            hass, entry, options=lambda: options, vehicle=lambda: ledger_vehicle(entry),
            open_charge=lambda: coordinator.store.get_open_charge(LOCATION_HOME) is not None,
            charges=lambda: coordinator.store.charges, trips=lambda: coordinator.store.trips)
        coordinator.smart.ledger = coordinator
        coordinator.platforms += SMART_PLATFORMS

    await hass.config_entries.async_forward_entry_setups(entry, coordinator.platforms)
    if coordinator.smart:
        coordinator.smart.async_start()
        entry.async_on_unload(coordinator.smart.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    for cancel in issues.async_watch(hass, entry):
        entry.async_on_unload(cancel)

    _async_register_services(hass)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A removed car leaves no repair issues behind."""
    issues.async_remove(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an EV Ledger config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    platforms = getattr(coordinator, "platforms", PLATFORMS)
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            for service in (
                SERVICE_LOG_PUBLIC_CHARGE,
                SERVICE_UPDATE_CHARGE,
                SERVICE_DELETE_CHARGE,
                SERVICE_DELETE_TRIP,
                SERVICE_ADD_CHARGE,
                SERVICE_SET_WEEK_PLAN,
                SERVICE_SET_READY_BY_ONCE,
                SERVICE_CLEAR_READY_BY_ONCE,
                SERVICE_SET_ZONE_PRICE,
                SERVICE_SET_OPERATOR_PRICE,
                SERVICE_FORGET_PLACE,
                *TYRE_SERVICES,
            ):
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)
    return unloaded


def _async_register_services(hass: HomeAssistant) -> None:
    async def _handle_log_public_charge(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"Unknown EV Ledger entry_id: {entry_id}")

        # If there's an open/pending public session waiting for review, it is filled in
        # rather than creating a duplicate — this is the normal case: the car
        # charged away from home, EV Ledger recorded the window but not the
        # price, and the user is now supplying it after the fact.
        await coordinator.async_log_public_charge(
            call.data[ATTR_KWH], call.data[ATTR_PRICE], call.data.get(ATTR_LOCATION_NAME),
            call.data.get(ATTR_NOTE), call.data.get(ATTR_STARTED_AT),
        )

    async def _handle_set_zone_price(call: ServiceCall) -> None:
        """The price per kWh of charging in a zone (0 = free, e.g. at family); without a price the zone's price goes."""
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(call.data["entry_id"])
        if coordinator is None:
            raise ServiceValidationError(f"Unknown EV Ledger entry_id: {call.data['entry_id']}")
        await coordinator.async_set_zone_price(call.data[ATTR_ZONE], call.data.get(ATTR_PRICE))

    def _ledger_of_entry(call: ServiceCall) -> EvLedgerCoordinator:
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(call.data["entry_id"])
        if coordinator is None:
            raise ServiceValidationError(f"Unknown EV Ledger entry_id: {call.data['entry_id']}")
        return coordinator

    async def _handle_set_operator_price(call: ServiceCall) -> None:
        """A charging operator's price per kWh (e.g. Clever's); without a price the operator's price goes."""
        await _ledger_of_entry(call).async_set_operator_price(call.data[ATTR_OPERATOR], call.data.get(ATTR_PRICE))

    async def _handle_forget_place(call: ServiceCall) -> None:
        """Forget a named public charging place."""
        if not await _ledger_of_entry(call).async_forget_place(call.data[ATTR_NAME]):
            raise ServiceValidationError(f"No place named {call.data[ATTR_NAME]!r}")

    async def _handle_add_charge(call: ServiceCall) -> None:
        """A finished charge at home or away that the ledger did not record (e.g. from before EV Ledger)."""
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(call.data["entry_id"])
        if coordinator is None:
            raise ServiceValidationError(f"Unknown EV Ledger entry_id: {call.data['entry_id']}")
        await coordinator.async_add_charge(
            call.data[ATTR_KWH], call.data.get(ATTR_PRICE), call.data[ATTR_STARTED_AT], call.data.get(ATTR_ENDED_AT),
            call.data[ATTR_LOCATION], call.data.get(ATTR_LOCATION_NAME), call.data.get(ATTR_NOTE),
        )

    def _planner(call: ServiceCall) -> ChargePlanner:
        """The smart charging of the car whose EV Ledger entity is given (any of its entities)."""
        entry = er.async_get(hass).async_get(call.data[ATTR_ENTITY_ID])
        coordinator = hass.data.get(DOMAIN, {}).get(entry.config_entry_id) if entry else None
        planner = getattr(coordinator, "smart", None)
        if planner is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_smart_charging",
                                         translation_placeholders={"entity_id": call.data[ATTR_ENTITY_ID]})
        return planner

    async def _handle_set_week_plan(call: ServiceCall) -> None:
        """A ready-by time (and maybe its own target) for each weekday; days left out or null are free."""
        planner = _planner(call)
        try:
            week = week_plan.parse_week(call.data["days"])
        except (TypeError, ValueError) as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="bad_week_plan",
                                         translation_placeholders={"error": str(err)}) from err
        if not week_plan.has_days(week):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="empty_week_plan")
        planner.async_set_week(week, call.data.get("enabled"))

    async def _handle_set_ready_by_once(call: ServiceCall) -> None:
        planner = _planner(call)
        set_once(planner, call.data["at"], call.data.get("target"))

    async def _handle_clear_ready_by_once(call: ServiceCall) -> None:
        _planner(call).async_set_once(None)

    def _ledger(call: ServiceCall) -> EvLedgerCoordinator:
        """The ledger of the car whose EV Ledger entity is given (any of its entities)."""
        entry = er.async_get(hass).async_get(call.data[ATTR_ENTITY_ID])
        coordinator = hass.data.get(DOMAIN, {}).get(entry.config_entry_id) if entry else None
        if coordinator is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_ev_ledger",
                                         translation_placeholders={"entity_id": call.data[ATTR_ENTITY_ID]})
        return coordinator

    def _tyre_set(coordinator: EvLedgerCoordinator, key: str):
        tyre = coordinator.find_tyre_set(key)
        if tyre is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_tyre_set",
                                         translation_placeholders={"set": key})
        return tyre

    async def _handle_save_tyre_set(call: ServiceCall) -> ServiceResponse:
        """A new tyre set, or the given fields of one (set_id)."""
        coordinator = _ledger(call)
        set_id = call.data.get("set_id")
        if set_id:
            set_id = _tyre_set(coordinator, set_id).id
        elif not call.data.get("name"):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tyre_set_needs_name")
        values = {k: v for k, v in call.data.items() if k not in (ATTR_ENTITY_ID, "set_id")}
        if values.get("purchased") is not None:
            values["purchased"] = values["purchased"].isoformat()
        name = values.get("name")
        if name and any(t.name.casefold() == name.strip().casefold() and t.id != set_id
                        for t in coordinator.store.tyre_sets):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tyre_set_name_taken",
                                         translation_placeholders={"set": name})
        if name:
            values["name"] = name.strip()
        tyre = await coordinator.async_save_tyre_set(values, set_id)
        return {"set_id": tyre.id, "name": tyre.name}

    async def _handle_delete_tyre_set(call: ServiceCall) -> None:
        coordinator = _ledger(call)
        await coordinator.async_delete_tyre_set(_tyre_set(coordinator, call.data["set_id"]).id)

    async def _handle_change_tyres(call: ServiceCall) -> ServiceResponse:
        coordinator = _ledger(call)
        tyre = _tyre_set(coordinator, call.data["set_id"])
        try:
            change = await coordinator.async_change_tyres(tyre.id, call.data.get("at"), call.data.get("odometer_km"),
                                                          call.data.get("note"))
        except ValueError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="tyre_set_already_on",
                                         translation_placeholders={"set": tyre.name}) from err
        return {"change_id": change.id, "odometer_km": change.odometer_km}

    async def _handle_update_tyre_change(call: ServiceCall) -> None:
        coordinator = _ledger(call)
        try:
            await coordinator.async_update_tyre_change(call.data["change_id"], call.data.get("at"),
                                                       call.data.get("odometer_km"), call.data.get("note"))
        except KeyError as err:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_tyre_change") from err

    async def _handle_delete_tyre_change(call: ServiceCall) -> None:
        if not await _ledger(call).async_delete_tyre_change(call.data["change_id"]):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_tyre_change")

    async def _handle_log_tread_depth(call: ServiceCall) -> ServiceResponse:
        coordinator = _ledger(call)
        tyre = _tyre_set(coordinator, call.data["set_id"])
        reading = await coordinator.async_log_tread(tyre.id, call.data.get("mm"), call.data.get("each"),
                                                    call.data.get("at"), call.data.get("odometer_km"))
        return {"reading_id": reading["id"], "mm": reading["mm"]}

    async def _handle_delete_tread_depth(call: ServiceCall) -> None:
        coordinator = _ledger(call)
        tyre = _tyre_set(coordinator, call.data["set_id"])
        if not await coordinator.async_delete_tread(tyre.id, call.data["reading_id"]):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_tread_reading")

    async def _handle_delete_charge(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"Unknown EV Ledger entry_id: {entry_id}")

        deleted = await coordinator.store.async_delete_charge(call.data[ATTR_CHARGE_ID])
        if not deleted:
            raise ServiceValidationError(f"No charge with id {call.data[ATTR_CHARGE_ID]!r}")

        await coordinator.async_request_refresh()

    async def _handle_update_charge(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"Unknown EV Ledger entry_id: {entry_id}")

        charge = coordinator.store.get_charge(call.data[ATTR_CHARGE_ID])
        if charge is None:
            raise ServiceValidationError(f"No charge with id {call.data[ATTR_CHARGE_ID]!r}")
        # Home charges too: a measured charge can be wrong (e.g. a charger counter that ran on through pauses).

        if ATTR_KWH in call.data:
            charge.kwh = call.data[ATTR_KWH]
        if ATTR_PRICE in call.data:
            charge.price = call.data[ATTR_PRICE]
        if ATTR_LOCATION_NAME in call.data:
            charge.location_name = call.data[ATTR_LOCATION_NAME] or None
        if ATTR_NOTE in call.data:
            charge.note = call.data[ATTR_NOTE] or None
        charge.needs_review = charge.kwh is None or charge.price is None
        await coordinator.store.async_upsert_charge(charge)
        await coordinator.async_request_refresh()

    async def _handle_delete_trip(call: ServiceCall) -> None:
        entry_id = call.data["entry_id"]
        coordinator: EvLedgerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"Unknown EV Ledger entry_id: {entry_id}")

        deleted = await coordinator.store.async_delete_trip(call.data[ATTR_TRIP_ID])
        if not deleted:
            raise ServiceValidationError(f"No trip with id {call.data[ATTR_TRIP_ID]!r}")

        await coordinator.async_request_refresh()

    services = (
        (SERVICE_LOG_PUBLIC_CHARGE, _handle_log_public_charge, LOG_PUBLIC_CHARGE_SCHEMA),
        (SERVICE_UPDATE_CHARGE, _handle_update_charge, UPDATE_CHARGE_SCHEMA),
        (SERVICE_DELETE_CHARGE, _handle_delete_charge, DELETE_CHARGE_SCHEMA),
        (SERVICE_DELETE_TRIP, _handle_delete_trip, DELETE_TRIP_SCHEMA),
        (SERVICE_ADD_CHARGE, _handle_add_charge, ADD_CHARGE_SCHEMA),
        (SERVICE_SET_ZONE_PRICE, _handle_set_zone_price, SET_ZONE_PRICE_SCHEMA),
        (SERVICE_SET_OPERATOR_PRICE, _handle_set_operator_price, SET_OPERATOR_PRICE_SCHEMA),
        (SERVICE_FORGET_PLACE, _handle_forget_place, FORGET_PLACE_SCHEMA),
        (SERVICE_SET_WEEK_PLAN, _handle_set_week_plan, SET_WEEK_PLAN_SCHEMA),
        (SERVICE_SET_READY_BY_ONCE, _handle_set_ready_by_once, SET_READY_BY_ONCE_SCHEMA),
        (SERVICE_CLEAR_READY_BY_ONCE, _handle_clear_ready_by_once, CAR_SCHEMA),
    )
    for service, handler, schema in services:
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema)
    answering = (
        (SERVICE_SAVE_TYRE_SET, _handle_save_tyre_set, SAVE_TYRE_SET_SCHEMA),
        (SERVICE_CHANGE_TYRES, _handle_change_tyres, CHANGE_TYRES_SCHEMA),
        (SERVICE_LOG_TREAD_DEPTH, _handle_log_tread_depth, LOG_TREAD_SCHEMA),
    )
    for service, handler, schema in answering:
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema,
                                         supports_response=SupportsResponse.OPTIONAL)
    for service, handler, schema in (
        (SERVICE_DELETE_TYRE_SET, _handle_delete_tyre_set, TYRE_SET_SCHEMA),
        (SERVICE_UPDATE_TYRE_CHANGE, _handle_update_tyre_change, UPDATE_TYRE_CHANGE_SCHEMA),
        (SERVICE_DELETE_TYRE_CHANGE, _handle_delete_tyre_change, TYRE_CHANGE_SCHEMA),
        (SERVICE_DELETE_TREAD_DEPTH, _handle_delete_tread_depth, DELETE_TREAD_SCHEMA),
    ):
        if not hass.services.has_service(DOMAIN, service):
            hass.services.async_register(DOMAIN, service, handler, schema=schema)
