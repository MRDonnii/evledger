"""EV Ledger without smart charging stays as it was; the options flow switches it on."""

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .test_smart_charge import DOMAIN, setup, state


async def test_without_smart_charging_only_ledger_sensors(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    data = {**entry.data}
    data.pop("smart_charge")
    other = MockConfigEntry(domain=DOMAIN, title="Anden", data={**data, "vehicle_name": "Anden"})
    other.add_to_hass(hass)
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    domains = {entity.domain for entity in er.async_entries_for_config_entry(er.async_get(hass), other.entry_id)}
    assert domains == {"sensor"}
    assert hass.data[DOMAIN][other.entry_id].smart is None
    assert hass.states.get("select.anden_charge_mode") is None


async def test_options_flow_switches_smart_charging_on(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    # start from a ledger without smart charging
    hass.config_entries.async_update_entry(entry, data={k: v for k, v in entry.data.items() if k != "smart_charge"})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("select.bil_charge_mode") is None or state(hass, "select.bil_charge_mode") == "unavailable"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    keep = {key: entry.data[key] for key in ("battery_entity", "odometer_entity", "device_tracker_entity",
                                              "charging_binary_entity", "spot_price_entity", "zaptec_power_entity",
                                              "zaptec_session_energy_entity") if key in entry.data}
    result = await hass.config_entries.options.async_configure(result["flow_id"], keep)
    assert result["step_id"] == "smart_charge"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"enabled": True})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["smart_charge"]["enabled"] is True
    assert state(hass, "select.bil_charge_mode") == "smart"
    planner = hass.data[DOMAIN][entry.entry_id].smart
    assert planner.options["zaptec_mode_entity"] == "sensor.charger_mode", "Zaptec found from the ledger's charger"
    assert planner.options["price_entities"] == ["sensor.price"], "the ledger's spot price"
