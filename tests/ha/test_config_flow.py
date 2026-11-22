"""Setting everything up in one go: a car from any Tesla integration (or any car by hand), the charger, the power
price and smart charging in the same flow."""

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evledger.providers.vehicle.tesla_custom import TeslaCustomVehicleProvider
from custom_components.evledger.smart import charger as charger_module

from .test_smart_charge import DOMAIN, prices


def device_with(hass: HomeAssistant, domain: str, ident: str, entities) -> str:
    """A device of an integration with entities: (domain, unique_id, object_id, state, attributes, disabled)."""
    integration = MockConfigEntry(domain=domain)
    integration.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=integration.entry_id,
                                                    identifiers={(domain, ident)})
    registry = er.async_get(hass)
    for entity_domain, unique, object_id, value, attributes, disabled in entities:
        registry.async_get_or_create(
            entity_domain, domain, unique, device_id=device.id, config_entry=integration,
            suggested_object_id=object_id,
            disabled_by=er.RegistryEntryDisabler.INTEGRATION if disabled else None)
        if not disabled:
            hass.states.async_set(f"{entity_domain}.{object_id}", value, attributes)
    return device.id


def fleet_car(hass: HomeAssistant) -> str:
    """A car from Tesla Fleet: names from its labels, API fields in the unique ids, the odometer off by default."""
    return device_with(hass, "tesla_fleet", "VIN1", [
        ("sensor", "VIN1-charge_state_battery_level", "fleetcar_battery_level", "60", {"unit_of_measurement": "%"},
         False),
        ("sensor", "VIN1-vehicle_state_odometer", "fleetcar_odometer", "1000", {}, True),
        ("device_tracker", "VIN1-location", "fleetcar_location", "home", {"latitude": 56.0, "longitude": 10.0}, False),
        ("sensor", "VIN1-charge_state_charging_state", "fleetcar_charging", "disconnected", {}, False),
        ("lock", "VIN1-vehicle_state_locked", "fleetcar_lock", "locked", {}, False),
        ("sensor", "VIN1-climate_state_outside_temp", "fleetcar_outside_temperature", "8", {}, False),
    ])


def zaptec_charger(hass: HomeAssistant) -> str:
    return device_with(hass, "zaptec", "charger", [
        ("sensor", "abc_charger_operation_mode", "charger_charger_mode", "disconnected", {}, False),
        ("switch", "abc_charger_operation_mode", "charger_charging", "off", {}, False),
        ("button", "abc_authorize_charge", "charger_authorize_charging", "unknown", {}, False),
        ("sensor", "abc_total_charge_power", "charger_charge_power", "0", {"unit_of_measurement": "W"}, False),
        ("sensor", "abc_session_total_charge", "charger_session_total_charge", "0", {"unit_of_measurement": "kWh"},
         False),
    ])


def price_device(hass: HomeAssistant) -> str:
    return device_with(hass, "stromligning", "prices", [
        ("sensor", "prices_current", "stromligning_current_price_vat", "1.0",
         {"prices": prices(cheap_now=False), "unit_of_measurement": "kr/kWh"}, False),
    ])


def schema_default(result, key):
    for marker in result["data_schema"].schema:
        if str(marker) == key:
            return marker.default() if callable(marker.default) else marker.default
    return None


async def test_tesla_fleet_car_charger_price_and_smart_charging_in_one_setup(hass: HomeAssistant, request):
    patcher = patch.object(charger_module.ChargerBackend, "_call", lambda *args: _noop())
    patcher.start()
    request.addfinalizer(patcher.stop)
    car, charger, price = fleet_car(hass), zaptec_charger(hass), price_device(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "vehicle_name": "Fleetcar", "currency": "DKK", "vehicle_device_id": car, "zaptec_device_id": charger,
        "spot_price_device_id": price, "tesla_model_key": "skip"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "smart_charge", "nothing was missing"
    assert schema_default(result, "enabled") is True, "suggested when the ledger has a power price"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"enabled": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    data = result["data"]
    assert data["battery_entity"] == "sensor.fleetcar_battery_level"
    assert data["odometer_entity"] == "sensor.fleetcar_odometer"
    assert data["device_tracker_entity"] == "device_tracker.fleetcar_location"
    assert data["charging_binary_entity"] == "sensor.fleetcar_charging"
    assert data["locked_entity"] == "lock.fleetcar_lock"
    assert data["zaptec_power_entity"] == "sensor.charger_charge_power"
    assert data["spot_price_entity"] == "sensor.stromligning_current_price_vat"
    assert data["charger_providers"] == ["manual", "zaptec", "spot_price"]
    assert data["smart_charge"]["enabled"] is True
    assert er.async_get(hass).async_get("sensor.fleetcar_odometer").disabled_by is None, \
        "the odometer Tesla Fleet turns off by default is turned on"

    await hass.async_block_till_done()
    assert hass.states.get("select.fleetcar_charge_mode") is not None, "smart charging runs right after setup"
    assert hass.states.get("sensor.fleetcar_charge_status").state == "disconnected"


async def test_any_car_by_hand_without_smart_charging(hass: HomeAssistant):
    for entity_id, value in (("sensor.kia_battery", "50"), ("sensor.kia_odometer", "2000"),
                             ("device_tracker.kia", "home"), ("sensor.kia_charging_state", "charging")):
        hass.states.async_set(entity_id, value)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "vehicle_name": "Kia", "currency": "DKK", "tesla_model_key": "skip"})
    assert result["step_id"] == "fill_missing", "no car device: its sensors are picked by hand"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "battery_entity": "sensor.kia_battery", "odometer_entity": "sensor.kia_odometer",
        "device_tracker_entity": "device_tracker.kia", "charging_binary_entity": "sensor.kia_charging_state"})
    assert result["step_id"] == "smart_charge"
    assert schema_default(result, "enabled") is False, "no power price: not suggested"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"enabled": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["smart_charge"]["enabled"] is False
    await hass.async_block_till_done()
    assert hass.states.get("select.kia_charge_mode") is None


async def test_smart_charging_in_the_setup_needs_prices(hass: HomeAssistant):
    car = fleet_car(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        "vehicle_name": "Fleetcar", "currency": "DKK", "vehicle_device_id": car, "tesla_model_key": "skip"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"enabled": True})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"price_entities": "no_prices"}


def test_a_charging_state_sensor_counts_as_charging(hass: HomeAssistant):
    provider = TeslaCustomVehicleProvider("sensor.b", None, None, "sensor.charging", None)
    hass.states.async_set("sensor.b", "50")
    for value, charging in (("charging", True), ("stopped", False), ("on", True), ("off", False)):
        hass.states.async_set("sensor.charging", value)
        assert provider.get_snapshot(hass).is_charging is charging, value


async def _noop():
    return None
