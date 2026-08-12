"""Config flow for EV Ledger."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CHARGER_PROVIDER_MANUAL,
    CHARGER_PROVIDER_MONTA,
    CHARGER_PROVIDER_SPOT_PRICE,
    CHARGER_PROVIDER_ZAPTEC,
    CONF_BATTERY_CAPACITY_KWH,
    CONF_BATTERY_ENTITY,
    CONF_CHARGER_PROVIDERS,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_CURRENCY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_MODEL_LABEL,
    CONF_MONTA_LAST_CHARGE_ENTITY,
    CONF_MONTA_WALLET_ENTITY,
    CONF_ODOMETER_ENTITY,
    CONF_OUTSIDE_TEMP_ENTITY,
    CONF_RATED_WH_PER_KM,
    CONF_SPOT_PRICE_ENTITY,
    CONF_TESLA_MODEL_KEY,
    CONF_VEHICLE_NAME,
    CONF_VEHICLE_PROVIDER,
    CONF_ZAPTEC_CHARGING_ENTITY,
    CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
    CONF_ZAPTEC_POWER_ENTITY,
    CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
    DEFAULT_CURRENCY,
    DOMAIN,
    VEHICLE_PROVIDER_TESLA_CUSTOM,
)
from .device_resolve import (
    resolve_monta_charger_entities,
    resolve_spot_price_entities,
    resolve_tesla_vehicle_entities,
    resolve_zaptec_charger_entities,
)
from .tesla_models import CUSTOM_MODEL_KEY, TESLA_MODEL_SPECS

SKIP_MODEL_KEY = "skip"

FIELD_VEHICLE_DEVICE = "vehicle_device_id"
FIELD_ZAPTEC_DEVICE = "zaptec_device_id"
FIELD_MONTA_DEVICE = "monta_device_id"
FIELD_SPOT_PRICE_DEVICE = "spot_price_device_id"
FIELD_ADVANCED = "advanced_setup"

REQUIRED_VEHICLE_FIELDS = (
    CONF_BATTERY_ENTITY,
    CONF_ODOMETER_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_CHARGING_BINARY_ENTITY,
)
REQUIRED_ZAPTEC_FIELDS = (CONF_ZAPTEC_POWER_ENTITY, CONF_ZAPTEC_SESSION_ENERGY_ENTITY)
REQUIRED_MONTA_FIELDS = (CONF_MONTA_LAST_CHARGE_ENTITY,)


def _entity_selector(domain: str | list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


def _device_selector(integration: str | None = None) -> selector.DeviceSelector:
    config: dict[str, str] = {}
    if integration:
        config["integration"] = integration
    return selector.DeviceSelector(selector.DeviceSelectorConfig(**config))


def _model_select_options() -> list[selector.SelectOptionDict]:
    options = [
        selector.SelectOptionDict(value=key, label=spec["label"])
        for key, spec in TESLA_MODEL_SPECS.items()
    ]
    options.append(
        selector.SelectOptionDict(value=CUSTOM_MODEL_KEY, label="Custom (enter my own numbers)")
    )
    options.append(
        selector.SelectOptionDict(value=SKIP_MODEL_KEY, label="Skip — no efficiency tracking")
    )
    return options


def _main_schema(defaults: dict[str, Any]) -> vol.Schema:
    """The single setup page: name/currency, one device picker per role.

    Zaptec, Monta and spot price are all optional — leaving a picker empty
    simply leaves that provider out of the config entry. Entities for
    whichever devices *are* picked are resolved automatically after submit;
    a follow-up page only appears if something couldn't be found.
    """
    return vol.Schema(
        {
            vol.Required(
                CONF_VEHICLE_NAME, default=defaults.get(CONF_VEHICLE_NAME, vol.UNDEFINED)
            ): str,
            vol.Required(
                CONF_CURRENCY, default=defaults.get(CONF_CURRENCY, DEFAULT_CURRENCY)
            ): str,
            vol.Required(
                FIELD_VEHICLE_DEVICE, default=defaults.get(FIELD_VEHICLE_DEVICE, vol.UNDEFINED)
            ): _device_selector("tesla_custom"),
            vol.Optional(
                FIELD_ZAPTEC_DEVICE, default=defaults.get(FIELD_ZAPTEC_DEVICE, vol.UNDEFINED)
            ): _device_selector("zaptec"),
            vol.Optional(
                FIELD_MONTA_DEVICE, default=defaults.get(FIELD_MONTA_DEVICE, vol.UNDEFINED)
            ): _device_selector("monta"),
            vol.Optional(
                FIELD_SPOT_PRICE_DEVICE,
                default=defaults.get(FIELD_SPOT_PRICE_DEVICE, vol.UNDEFINED),
            ): _device_selector(),
            vol.Optional(
                CONF_TESLA_MODEL_KEY, default=defaults.get(CONF_TESLA_MODEL_KEY, SKIP_MODEL_KEY)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_model_select_options(), mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Optional(
                CONF_BATTERY_CAPACITY_KWH,
                default=defaults.get(CONF_BATTERY_CAPACITY_KWH, vol.UNDEFINED),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=200, step=0.1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RATED_WH_PER_KM,
                default=defaults.get(CONF_RATED_WH_PER_KM, vol.UNDEFINED),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=400, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                FIELD_ADVANCED, default=defaults.get(FIELD_ADVANCED, False)
            ): selector.BooleanSelector(),
        }
    )


def _vehicle_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_BATTERY_ENTITY, default=defaults.get(CONF_BATTERY_ENTITY, vol.UNDEFINED)
            ): _entity_selector("sensor"),
            vol.Required(
                CONF_ODOMETER_ENTITY, default=defaults.get(CONF_ODOMETER_ENTITY, vol.UNDEFINED)
            ): _entity_selector("sensor"),
            vol.Required(
                CONF_DEVICE_TRACKER_ENTITY,
                default=defaults.get(CONF_DEVICE_TRACKER_ENTITY, vol.UNDEFINED),
            ): _entity_selector("device_tracker"),
            vol.Required(
                CONF_CHARGING_BINARY_ENTITY,
                default=defaults.get(CONF_CHARGING_BINARY_ENTITY, vol.UNDEFINED),
            ): _entity_selector("binary_sensor"),
            vol.Optional(
                CONF_LOCKED_ENTITY, default=defaults.get(CONF_LOCKED_ENTITY, vol.UNDEFINED)
            ): _entity_selector("lock"),
            vol.Optional(
                CONF_OUTSIDE_TEMP_ENTITY,
                default=defaults.get(CONF_OUTSIDE_TEMP_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
        }
    )


def _zaptec_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_ZAPTEC_POWER_ENTITY,
                default=defaults.get(CONF_ZAPTEC_POWER_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Required(
                CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
                default=defaults.get(CONF_ZAPTEC_SESSION_ENERGY_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_ZAPTEC_CHARGING_ENTITY,
                default=defaults.get(CONF_ZAPTEC_CHARGING_ENTITY, vol.UNDEFINED),
            ): _entity_selector(["binary_sensor", "switch"]),
            vol.Optional(
                CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
                default=defaults.get(CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
        }
    )


def _monta_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_MONTA_LAST_CHARGE_ENTITY,
                default=defaults.get(CONF_MONTA_LAST_CHARGE_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_MONTA_WALLET_ENTITY,
                default=defaults.get(CONF_MONTA_WALLET_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
        }
    )


def _spot_price_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SPOT_PRICE_ENTITY,
                default=defaults.get(CONF_SPOT_PRICE_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
        }
    )


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """One combined edit page for the options flow.

    No device picker here (the originally picked device isn't persisted,
    only the entities it resolved to) — this edits entities directly.
    Clearing an entity that identifies a provider (Zaptec power/session,
    Monta last-charge, spot price) drops that provider on save.
    """
    schema_dict: dict[Any, Any] = {}
    schema_dict.update(_vehicle_schema(defaults).schema)
    schema_dict.update(
        {
            vol.Optional(
                CONF_ZAPTEC_POWER_ENTITY,
                default=defaults.get(CONF_ZAPTEC_POWER_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
                default=defaults.get(CONF_ZAPTEC_SESSION_ENERGY_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_ZAPTEC_CHARGING_ENTITY,
                default=defaults.get(CONF_ZAPTEC_CHARGING_ENTITY, vol.UNDEFINED),
            ): _entity_selector(["binary_sensor", "switch"]),
            vol.Optional(
                CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
                default=defaults.get(CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_MONTA_LAST_CHARGE_ENTITY,
                default=defaults.get(CONF_MONTA_LAST_CHARGE_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_MONTA_WALLET_ENTITY,
                default=defaults.get(CONF_MONTA_WALLET_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_SPOT_PRICE_ENTITY,
                default=defaults.get(CONF_SPOT_PRICE_ENTITY, vol.UNDEFINED),
            ): _entity_selector("sensor"),
            vol.Optional(
                CONF_TESLA_MODEL_KEY,
                default=defaults.get(CONF_TESLA_MODEL_KEY, SKIP_MODEL_KEY),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_model_select_options(), mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Optional(
                CONF_BATTERY_CAPACITY_KWH,
                default=defaults.get(CONF_BATTERY_CAPACITY_KWH, vol.UNDEFINED),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=200, step=0.1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Optional(
                CONF_RATED_WH_PER_KM,
                default=defaults.get(CONF_RATED_WH_PER_KM, vol.UNDEFINED),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=400, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )
    return vol.Schema(schema_dict)


def _process_efficiency(data: dict[str, Any], user_input: dict[str, Any]) -> None:
    key = user_input.get(CONF_TESLA_MODEL_KEY, SKIP_MODEL_KEY)
    data[CONF_TESLA_MODEL_KEY] = key
    if key == CUSTOM_MODEL_KEY:
        battery = user_input.get(CONF_BATTERY_CAPACITY_KWH)
        rated = user_input.get(CONF_RATED_WH_PER_KM)
        if battery and rated:
            data[CONF_BATTERY_CAPACITY_KWH] = battery
            data[CONF_RATED_WH_PER_KM] = rated
            data[CONF_MODEL_LABEL] = "Custom"
            return
        key = SKIP_MODEL_KEY
        data[CONF_TESLA_MODEL_KEY] = key
    if key in TESLA_MODEL_SPECS:
        spec = TESLA_MODEL_SPECS[key]
        data[CONF_BATTERY_CAPACITY_KWH] = spec["battery_kwh"]
        data[CONF_RATED_WH_PER_KM] = spec["wltp_wh_per_km"]
        data[CONF_MODEL_LABEL] = spec["label"]
    else:
        data.pop(CONF_BATTERY_CAPACITY_KWH, None)
        data.pop(CONF_RATED_WH_PER_KM, None)
        data.pop(CONF_MODEL_LABEL, None)


class EvLedgerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle initial setup of an EV Ledger vehicle — one page, then only ask
    for anything that couldn't be auto-resolved from the devices picked."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {CONF_VEHICLE_PROVIDER: VEHICLE_PROVIDER_TESLA_CUSTOM}
        self._need_zaptec = False
        self._need_monta = False
        self._need_spot_price = False
        self._advanced = False

    def _missing_required(self) -> bool:
        if not all(self._data.get(f) for f in REQUIRED_VEHICLE_FIELDS):
            return True
        if self._need_zaptec and not all(self._data.get(f) for f in REQUIRED_ZAPTEC_FIELDS):
            return True
        if self._need_monta and not all(self._data.get(f) for f in REQUIRED_MONTA_FIELDS):
            return True
        if self._need_spot_price and not self._data.get(CONF_SPOT_PRICE_ENTITY):
            return True
        return False

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_VEHICLE_NAME].lower())
            self._abort_if_unique_id_configured()

            self._data[CONF_VEHICLE_NAME] = user_input[CONF_VEHICLE_NAME]
            self._data[CONF_CURRENCY] = user_input[CONF_CURRENCY]

            resolved = resolve_tesla_vehicle_entities(self.hass, user_input[FIELD_VEHICLE_DEVICE])
            self._data.update({k: v for k, v in resolved.items() if v is not None})

            providers = [CHARGER_PROVIDER_MANUAL]

            zaptec_device = user_input.get(FIELD_ZAPTEC_DEVICE)
            if zaptec_device:
                self._need_zaptec = True
                providers.append(CHARGER_PROVIDER_ZAPTEC)
                resolved = resolve_zaptec_charger_entities(self.hass, zaptec_device)
                self._data.update({k: v for k, v in resolved.items() if v is not None})

            monta_device = user_input.get(FIELD_MONTA_DEVICE)
            if monta_device:
                self._need_monta = True
                providers.append(CHARGER_PROVIDER_MONTA)
                resolved = resolve_monta_charger_entities(self.hass, monta_device)
                self._data.update({k: v for k, v in resolved.items() if v is not None})

            spot_price_device = user_input.get(FIELD_SPOT_PRICE_DEVICE)
            if spot_price_device:
                self._need_spot_price = True
                providers.append(CHARGER_PROVIDER_SPOT_PRICE)
                resolved = resolve_spot_price_entities(self.hass, spot_price_device)
                self._data.update({k: v for k, v in resolved.items() if v is not None})

            self._data[CONF_CHARGER_PROVIDERS] = providers
            self._advanced = bool(user_input.get(FIELD_ADVANCED, False))
            _process_efficiency(self._data, user_input)

            if self._advanced or self._missing_required():
                return await self.async_step_fill_missing()
            return self.async_create_entry(title=self._data[CONF_VEHICLE_NAME], data=self._data)

        return self.async_show_form(
            step_id="user", data_schema=_main_schema(self._data), errors=errors
        )

    async def async_step_fill_missing(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return self.async_create_entry(title=self._data[CONF_VEHICLE_NAME], data=self._data)

        schema_dict: dict[Any, Any] = {}
        if self._advanced or not all(self._data.get(f) for f in REQUIRED_VEHICLE_FIELDS):
            schema_dict.update(_vehicle_schema(self._data).schema)
        if self._need_zaptec and (
            self._advanced or not all(self._data.get(f) for f in REQUIRED_ZAPTEC_FIELDS)
        ):
            schema_dict.update(_zaptec_schema(self._data).schema)
        if self._need_monta and (
            self._advanced or not all(self._data.get(f) for f in REQUIRED_MONTA_FIELDS)
        ):
            schema_dict.update(_monta_schema(self._data).schema)
        if self._need_spot_price and (self._advanced or not self._data.get(CONF_SPOT_PRICE_ENTITY)):
            schema_dict.update(_spot_price_schema(self._data).schema)
        return self.async_show_form(step_id="fill_missing", data_schema=vol.Schema(schema_dict))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "EvLedgerOptionsFlow":
        return EvLedgerOptionsFlow(config_entry)


class EvLedgerOptionsFlow(OptionsFlow):
    """Reconfigure everything from one combined page of current entities."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._data: dict[str, Any] = dict(config_entry.data)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(user_input)

            providers = [CHARGER_PROVIDER_MANUAL]
            if user_input.get(CONF_ZAPTEC_POWER_ENTITY) and user_input.get(
                CONF_ZAPTEC_SESSION_ENERGY_ENTITY
            ):
                providers.append(CHARGER_PROVIDER_ZAPTEC)
            else:
                for field in (
                    CONF_ZAPTEC_POWER_ENTITY,
                    CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
                    CONF_ZAPTEC_CHARGING_ENTITY,
                    CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
                ):
                    self._data.pop(field, None)

            if user_input.get(CONF_MONTA_LAST_CHARGE_ENTITY):
                providers.append(CHARGER_PROVIDER_MONTA)
            else:
                for field in (CONF_MONTA_LAST_CHARGE_ENTITY, CONF_MONTA_WALLET_ENTITY):
                    self._data.pop(field, None)

            if user_input.get(CONF_SPOT_PRICE_ENTITY):
                providers.append(CHARGER_PROVIDER_SPOT_PRICE)
            else:
                self._data.pop(CONF_SPOT_PRICE_ENTITY, None)

            self._data[CONF_CHARGER_PROVIDERS] = providers
            _process_efficiency(self._data, user_input)

            self.hass.config_entries.async_update_entry(self._entry, data=self._data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(step_id="init", data_schema=_options_schema(self._data))
