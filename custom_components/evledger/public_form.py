"""Logging a public charge from a dashboard: kWh, price and place, then a button. The same as the
log_public_charge action, without helpers or a script of one's own."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN


class PublicChargeForm:
    """What has been typed in so far; the fields empty again once the charge is logged."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator
        self.kwh = 0.0
        self.price = 0.0
        self.location = ""
        self.fields: list[Entity] = []

    async def async_log(self) -> None:
        if self.kwh <= 0:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="public_charge_no_kwh")
        await self.coordinator.async_log_public_charge(self.kwh, self.price, self.location.strip() or None)
        self.kwh, self.price, self.location = 0.0, 0.0, ""
        for field in self.fields:
            field.async_write_ha_state()


def form_of(coordinator) -> PublicChargeForm:
    if getattr(coordinator, "public_form", None) is None:
        coordinator.public_form = PublicChargeForm(coordinator)
    return coordinator.public_form


class _FormEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, form: PublicChargeForm, entry: ConfigEntry, key: str) -> None:
        self.form = form
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
        form.fields.append(self)


class PublicChargeEnergy(_FormEntity, NumberEntity):
    _attr_icon = "mdi:lightning-bolt"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 500
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "kWh"

    @property
    def native_value(self) -> float:
        return self.form.kwh

    async def async_set_native_value(self, value: float) -> None:
        self.form.kwh = float(value)
        self.async_write_ha_state()


class PublicChargePrice(_FormEntity, NumberEntity):
    _attr_icon = "mdi:cash"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 100000
    _attr_native_step = 0.01

    @property
    def native_unit_of_measurement(self) -> str:
        return self.form.coordinator.currency

    @property
    def native_value(self) -> float:
        return self.form.price

    async def async_set_native_value(self, value: float) -> None:
        self.form.price = float(value)
        self.async_write_ha_state()


class PublicChargeLocation(_FormEntity, TextEntity):
    _attr_icon = "mdi:map-marker"
    _attr_native_max = 100

    @property
    def native_value(self) -> str:
        return self.form.location

    async def async_set_value(self, value: str) -> None:
        self.form.location = value
        self.async_write_ha_state()


class LogPublicCharge(_FormEntity, ButtonEntity):
    _attr_icon = "mdi:content-save-outline"

    async def async_press(self) -> None:
        await self.form.async_log()


def numbers(coordinator, entry: ConfigEntry) -> list:
    form = form_of(coordinator)
    return [PublicChargeEnergy(form, entry, "public_charge_energy"), PublicChargePrice(form, entry, "public_charge_price")]


def texts(coordinator, entry: ConfigEntry) -> list:
    return [PublicChargeLocation(form_of(coordinator), entry, "public_charge_location")]


def buttons(coordinator, entry: ConfigEntry) -> list:
    return [LogPublicCharge(form_of(coordinator), entry, "log_public_charge")]
