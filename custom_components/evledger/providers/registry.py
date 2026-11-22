"""Builds provider instances from a config entry's stored data."""
from __future__ import annotations

from typing import Any

from ..const import (
    CHARGER_PROVIDER_MANUAL,
    CHARGER_PROVIDER_MONTA,
    CHARGER_PROVIDER_SPOT_PRICE,
    CHARGER_PROVIDER_ZAPTEC,
    CONF_BATTERY_ENTITY,
    CONF_CHARGER_PROVIDERS,
    CONF_CHARGING_BINARY_ENTITY,
    CONF_DEVICE_TRACKER_ENTITY,
    CONF_LOCKED_ENTITY,
    CONF_MONTA_LAST_CHARGE_ENTITY,
    CONF_MONTA_WALLET_ENTITY,
    CONF_ODOMETER_ENTITY,
    CONF_OUTSIDE_TEMP_ENTITY,
    CONF_SPOT_PRICE_ENTITY,
    CONF_ZAPTEC_CHARGING_ENTITY,
    CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY,
    CONF_ZAPTEC_POWER_ENTITY,
    CONF_ZAPTEC_SESSION_ENERGY_ENTITY,
)
from .charger.base import ChargerProvider
from .charger.manual import ManualChargerProvider
from .charger.monta import MontaChargerProvider
from .charger.spot_price import SpotPriceChargerProvider
from .charger.zaptec import ZaptecChargerProvider
from .vehicle.base import VehicleProvider
from .vehicle.tesla_custom import TeslaCustomVehicleProvider


def build_vehicle_provider(data: dict[str, Any]) -> VehicleProvider:
    """Only tesla_custom is supported today; more vehicle providers can be added here."""
    return TeslaCustomVehicleProvider(
        battery_entity=data.get(CONF_BATTERY_ENTITY),
        odometer_entity=data.get(CONF_ODOMETER_ENTITY),
        device_tracker_entity=data.get(CONF_DEVICE_TRACKER_ENTITY),
        charging_binary_entity=data.get(CONF_CHARGING_BINARY_ENTITY),
        locked_entity=data.get(CONF_LOCKED_ENTITY),
        outside_temp_entity=data.get(CONF_OUTSIDE_TEMP_ENTITY),
    )


def build_charger_providers(data: dict[str, Any]) -> dict[str, ChargerProvider]:
    """Return {provider_id: instance} for every charger provider enabled on this entry."""
    enabled: list[str] = data.get(CONF_CHARGER_PROVIDERS, [])
    providers: dict[str, ChargerProvider] = {}

    if CHARGER_PROVIDER_ZAPTEC in enabled:
        providers[CHARGER_PROVIDER_ZAPTEC] = ZaptecChargerProvider(
            power_entity=data.get(CONF_ZAPTEC_POWER_ENTITY),
            session_energy_entity=data.get(CONF_ZAPTEC_SESSION_ENERGY_ENTITY),
            charging_entity=data.get(CONF_ZAPTEC_CHARGING_ENTITY),
            completed_energy_entity=data.get(CONF_ZAPTEC_COMPLETED_ENERGY_ENTITY),
        )

    if CHARGER_PROVIDER_MONTA in enabled:
        providers[CHARGER_PROVIDER_MONTA] = MontaChargerProvider(
            last_charge_entity=data.get(CONF_MONTA_LAST_CHARGE_ENTITY),
            wallet_entity=data.get(CONF_MONTA_WALLET_ENTITY),
        )

    if CHARGER_PROVIDER_MANUAL in enabled:
        providers[CHARGER_PROVIDER_MANUAL] = ManualChargerProvider()

    if CHARGER_PROVIDER_SPOT_PRICE in enabled:
        providers[CHARGER_PROVIDER_SPOT_PRICE] = SpotPriceChargerProvider(
            price_entity=data.get(CONF_SPOT_PRICE_ENTITY)
        )

    return providers
