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
    CONF_BATTERY_ENTITY,
    CONF_CHARGER_PROVIDERS,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_CURRENCY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_MONTA_LAST_CHARGE_ENTITY,
    CONF_MONTA_WALLET_ENTITY,
    CONF_ODOMETER_ENTITY,
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


def _entity_selector(domain: str | list[str]) -> selector.EntitySelector:
    return selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))


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


class EvLedgerFlowMixin:
    """Shared step logic for both the initial config flow and the options flow."""

    _data: dict[str, Any]

    async def _async_step_vehicle(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data.update(user_input)
            return await self._async_step_chargers(None)
        return self.async_show_form(step_id="vehicle", data_schema=_vehicle_schema(self._data))

    async def _async_step_chargers(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data[CONF_CHARGER_PROVIDERS] = user_input[CONF_CHARGER_PROVIDERS]
            if CHARGER_PROVIDER_ZAPTEC in user_input[CONF_CHARGER_PROVIDERS]:
                return await self._async_step_zaptec(None)
            if CHARGER_PROVIDER_MONTA in user_input[CONF_CHARGER_PROVIDERS]:
                return await self._async_step_monta(None)
            return self._async_finish()
        return self.async_show_form(step_id="chargers", data_schema=_chargers_schema(self._data))

    async def _async_step_zaptec(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data.update(user_input)
            if CHARGER_PROVIDER_MONTA in self._data.get(CONF_CHARGER_PROVIDERS, []):
                return await self._async_step_monta(None)
            return self._async_finish()
        return self.async_show_form(step_id="zaptec", data_schema=_zaptec_schema(self._data))

    async def _async_step_monta(self, user_input: dict[str, Any] | None):
        if user_input is not None:
            self._data.update(user_input)
            return self._async_finish()
        return self.async_show_form(step_id="monta", data_schema=_monta_schema(self._data))

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
            return await self._async_step_vehicle(None)

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

    async def async_step_vehicle(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_vehicle(user_input)

    async def async_step_chargers(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_chargers(user_input)

    async def async_step_zaptec(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_zaptec(user_input)

    async def async_step_monta(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_monta(user_input)

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
        return await self._async_step_vehicle(None)

    async def async_step_vehicle(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_vehicle(user_input)

    async def async_step_chargers(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_chargers(user_input)

    async def async_step_zaptec(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_zaptec(user_input)

    async def async_step_monta(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_monta(user_input)

    def _async_finish(self):
        self.hass.config_entries.async_update_entry(self._entry, data=self._data)
        return self.async_create_entry(title="", data={})
