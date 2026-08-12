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
    resolve_tesla_vehicle_entities,
    resolve_zaptec_charger_entities,
)
from .tesla_models import CUSTOM_MODEL_KEY, TESLA_MODEL_SPECS

SKIP_MODEL_KEY = "skip"

FIELD_VEHICLE_DEVICE = "vehicle_device_id"
FIELD_ZAPTEC_DEVICE = "zaptec_device_id"
FIELD_MONTA_DEVICE = "monta_device_id"

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


def _device_selector(integration: str) -> selector.DeviceSelector:
    return selector.DeviceSelector(selector.DeviceSelectorConfig(integration=integration))


def _vehicle_device_schema() -> vol.Schema:
    return vol.Schema({vol.Required(FIELD_VEHICLE_DEVICE): _device_selector("tesla_custom")})


def _zaptec_device_schema() -> vol.Schema:
    return vol.Schema({vol.Required(FIELD_ZAPTEC_DEVICE): _device_selector("zaptec")})


def _monta_device_schema() -> vol.Schema:
    return vol.Schema({vol.Required(FIELD_MONTA_DEVICE): _device_selector("monta")})


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


def _chargers_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_CHARGER_PROVIDERS,
                default=defaults.get(CONF_CHARGER_PROVIDERS, [CHARGER_PROVIDER_MANUAL]),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(
                            value=CHARGER_PROVIDER_ZAPTEC, label="Zaptec (live power, no cost)"
                        ),
                        selector.SelectOptionDict(
                            value=CHARGER_PROVIDER_MONTA, label="Monta (session cost)"
                        ),
                        selector.SelectOptionDict(
                            value=CHARGER_PROVIDER_MANUAL,
                            label="Manual entry (public/away charging)",
                        ),
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
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


def _efficiency_schema(defaults: dict[str, Any]) -> vol.Schema:
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
    return vol.Schema(
        {
            vol.Required(
                CONF_TESLA_MODEL_KEY, default=defaults.get(CONF_TESLA_MODEL_KEY, SKIP_MODEL_KEY)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
        }
    )


def _efficiency_custom_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_BATTERY_CAPACITY_KWH,
                default=defaults.get(CONF_BATTERY_CAPACITY_KWH, vol.UNDEFINED),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=200, step=0.1, mode=selector.NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_RATED_WH_PER_KM,
                default=defaults.get(CONF_RATED_WH_PER_KM, vol.UNDEFINED),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=400, step=1, mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


class EvLedgerFlowMixin:
    """Shared step logic for both the initial config flow and the options flow."""

    _data: dict[str, Any]

    # ---- vehicle: pick a device, auto-resolve, only ask for the rest if needed ----

    async def _async_step_vehicle_device(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            resolved = resolve_tesla_vehicle_entities(self.hass, user_input[FIELD_VEHICLE_DEVICE])
            self._data.update({k: v for k, v in resolved.items() if v is not None})
            if all(self._data.get(f) for f in REQUIRED_VEHICLE_FIELDS):
                return await self._async_step_chargers(None)
            return await self._async_step_vehicle(None)
        return self.async_show_form(
            step_id="vehicle_device", data_schema=_vehicle_device_schema()
        )

    async def _async_step_vehicle(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_step_chargers(None)
        return self.async_show_form(step_id="vehicle", data_schema=_vehicle_schema(self._data))

    async def _async_step_chargers(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data[CONF_CHARGER_PROVIDERS] = user_input[CONF_CHARGER_PROVIDERS]
            if CHARGER_PROVIDER_ZAPTEC in user_input[CONF_CHARGER_PROVIDERS]:
                return await self._async_step_zaptec_device(None)
            if CHARGER_PROVIDER_MONTA in user_input[CONF_CHARGER_PROVIDERS]:
                return await self._async_step_monta_device(None)
            return await self._async_step_efficiency(None)
        return self.async_show_form(step_id="chargers", data_schema=_chargers_schema(self._data))

    # ---- zaptec: pick a device, auto-resolve, only ask for the rest if needed ----

    async def _async_step_zaptec_device(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            resolved = resolve_zaptec_charger_entities(
                self.hass, user_input[FIELD_ZAPTEC_DEVICE]
            )
            self._data.update({k: v for k, v in resolved.items() if v is not None})
            if all(self._data.get(f) for f in REQUIRED_ZAPTEC_FIELDS):
                return await self._async_step_after_zaptec()
            return await self._async_step_zaptec(None)
        return self.async_show_form(step_id="zaptec_device", data_schema=_zaptec_device_schema())

    async def _async_step_zaptec(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_step_after_zaptec()
        return self.async_show_form(step_id="zaptec", data_schema=_zaptec_schema(self._data))

    async def _async_step_after_zaptec(self):
        if CHARGER_PROVIDER_MONTA in self._data.get(CONF_CHARGER_PROVIDERS, []):
            return await self._async_step_monta_device(None)
        return await self._async_step_efficiency(None)

    # ---- monta: pick a device, auto-resolve, only ask for the rest if needed ----

    async def _async_step_monta_device(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            resolved = resolve_monta_charger_entities(self.hass, user_input[FIELD_MONTA_DEVICE])
            self._data.update({k: v for k, v in resolved.items() if v is not None})
            if all(self._data.get(f) for f in REQUIRED_MONTA_FIELDS):
                return await self._async_step_efficiency(None)
            return await self._async_step_monta(None)
        return self.async_show_form(step_id="monta_device", data_schema=_monta_device_schema())

    async def _async_step_monta(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_step_efficiency(None)
        return self.async_show_form(step_id="monta", data_schema=_monta_schema(self._data))

    async def _async_step_efficiency(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            key = user_input[CONF_TESLA_MODEL_KEY]
            self._data[CONF_TESLA_MODEL_KEY] = key
            if key == CUSTOM_MODEL_KEY:
                return await self._async_step_efficiency_custom(None)
            if key in TESLA_MODEL_SPECS:
                spec = TESLA_MODEL_SPECS[key]
                self._data[CONF_BATTERY_CAPACITY_KWH] = spec["battery_kwh"]
                self._data[CONF_RATED_WH_PER_KM] = spec["wltp_wh_per_km"]
                self._data[CONF_MODEL_LABEL] = spec["label"]
            else:
                self._data.pop(CONF_BATTERY_CAPACITY_KWH, None)
                self._data.pop(CONF_RATED_WH_PER_KM, None)
                self._data.pop(CONF_MODEL_LABEL, None)
            return self._async_finish()
        return self.async_show_form(
            step_id="efficiency", data_schema=_efficiency_schema(self._data)
        )

    async def _async_step_efficiency_custom(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data.update(user_input)
            self._data[CONF_MODEL_LABEL] = "Custom"
            return self._async_finish()
        return self.async_show_form(
            step_id="efficiency_custom", data_schema=_efficiency_custom_schema(self._data)
        )

    def _async_finish(self):
        raise NotImplementedError


class EvLedgerConfigFlow(ConfigFlow, EvLedgerFlowMixin, domain=DOMAIN):
    """Handle initial setup of an EV Ledger vehicle."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {CONF_VEHICLE_PROVIDER: VEHICLE_PROVIDER_TESLA_CUSTOM}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            await self.async_set_unique_id(user_input[CONF_VEHICLE_NAME].lower())
            self._abort_if_unique_id_configured()
            return await self._async_step_vehicle_device(None)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_VEHICLE_NAME): str,
                    vol.Required(CONF_CURRENCY, default=DEFAULT_CURRENCY): str,
                }
            ),
            errors=errors,
        )

    async def async_step_vehicle_device(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_vehicle_device(user_input)

    async def async_step_vehicle(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_vehicle(user_input)

    async def async_step_chargers(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_chargers(user_input)

    async def async_step_zaptec_device(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_zaptec_device(user_input)

    async def async_step_zaptec(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_zaptec(user_input)

    async def async_step_monta_device(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_monta_device(user_input)

    async def async_step_monta(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_monta(user_input)

    async def async_step_efficiency(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_efficiency(user_input)

    async def async_step_efficiency_custom(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_efficiency_custom(user_input)

    def _async_finish(self):
        return self.async_create_entry(title=self._data[CONF_VEHICLE_NAME], data=self._data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "EvLedgerOptionsFlow":
        return EvLedgerOptionsFlow(config_entry)


class EvLedgerOptionsFlow(OptionsFlow, EvLedgerFlowMixin):
    """Let the user revisit every entity mapping after initial setup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry
        self._data: dict[str, Any] = dict(config_entry.data)

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        # Reconfiguring: go straight to the editable form with current values
        # pre-filled, rather than forcing a device re-pick.
        return await self._async_step_vehicle(None)

    async def async_step_vehicle_device(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_vehicle_device(user_input)

    async def async_step_vehicle(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_vehicle(user_input)

    async def async_step_chargers(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_chargers(user_input)

    async def async_step_zaptec_device(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_zaptec_device(user_input)

    async def async_step_zaptec(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_zaptec(user_input)

    async def async_step_monta_device(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_monta_device(user_input)

    async def async_step_monta(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_monta(user_input)

    async def async_step_efficiency(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_efficiency(user_input)

    async def async_step_efficiency_custom(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_efficiency_custom(user_input)

    def _async_finish(self):
        self.hass.config_entries.async_update_entry(self._entry, data=self._data)
        return self.async_create_entry(title="", data={})
