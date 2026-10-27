"""Logging a public charge from a dashboard: kWh, price and place, then a button. The same as the
log_public_charge action, without helpers or a script of one's own."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityCategory

from .const import DOMAIN
from .smart.plan import finite


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
        if (value := finite(value)) is None:
            raise HomeAssistantError("The value is not a number")
        self.form.kwh = value
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
        if (value := finite(value)) is None:
            raise HomeAssistantError("The value is not a number")
        self.form.price = value
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


class PublicDefaultPrice(NumberEntity):
    """The price per kWh a public charge gets when it ends (Claude AI, 2026-10-27; user: Tesla's chargers have one price
    around the clock, "so when I charge away it should use that unless I put in something else"). 0 = none: the phones
    are asked for the price as before. A price given later (the phone's answer, the form, the action) replaces it."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:ev-station"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 50
    _attr_native_step = 0.01
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "public_default_price"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_public_default_price"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    async def async_added_to_hass(self) -> None:
        # A zone price set with the action shows in the attributes at once
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def native_unit_of_measurement(self) -> str:
        return f"{self.coordinator.currency}/kWh"

    @property
    def native_value(self) -> float:
        return self.coordinator.public_default_price()

    @property
    def extra_state_attributes(self) -> dict:
        """The zones with their own price per kWh (evledger.set_zone_price), by name."""
        prices = {}
        for zone, price in (self.coordinator.store.settings.get("zone_prices") or {}).items():
            state = self.hass.states.get(zone) if self.hass else None
            prices[str(state.attributes.get("friendly_name") or zone) if state else zone] = price
        return {"zone_prices": prices}

    async def async_set_native_value(self, value: float) -> None:
        if (value := finite(value)) is None or value < 0:
            raise HomeAssistantError("The value is not a number")
        await self.coordinator.store.async_set_setting("public_default_price", round(value, 4))
        self.async_write_ha_state()


def numbers(coordinator, entry: ConfigEntry) -> list:
    form = form_of(coordinator)
    return [PublicChargeEnergy(form, entry, "public_charge_energy"), PublicChargePrice(form, entry, "public_charge_price"),
            PublicDefaultPrice(coordinator, entry)]


def texts(coordinator, entry: ConfigEntry) -> list:
    return [PublicChargeLocation(form_of(coordinator), entry, "public_charge_location")]


def buttons(coordinator, entry: ConfigEntry) -> list:
    return [LogPublicCharge(form_of(coordinator), entry, "log_public_charge")]
