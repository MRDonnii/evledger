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


async def test_tomorrow_prices_next_to_the_spot_price_are_used(hass: HomeAssistant, request):
    from homeassistant.helpers import device_registry as dr

    from .test_smart_charge import prices

    prices_entry = MockConfigEntry(domain="stromligning")
    prices_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=prices_entry.entry_id,
                                                    identifiers={("stromligning", "x")})
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "stromligning", "now", device_id=device.id, config_entry=prices_entry,
                                 suggested_object_id="price")
    registry.async_get_or_create("binary_sensor", "stromligning", "tomorrow", device_id=device.id,
                                 config_entry=prices_entry, suggested_object_id="price_tomorrow")
    registry.async_get_or_create("sensor", "stromligning", "other", device_id=device.id, config_entry=prices_entry,
                                 suggested_object_id="price_other")
    hass.states.async_set("binary_sensor.price_tomorrow", "on", {"prices": prices(False)})
    hass.states.async_set("sensor.price_other", "3")
    entry, _ = await setup(hass, request)
    assert hass.data[DOMAIN][entry.entry_id].smart.options["price_entities"] == [
        "sensor.price", "binary_sensor.price_tomorrow"]


async def test_a_cleared_provider_entity_drops_the_provider(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    hass.states.async_set("sensor.monta_last_charge", "completed")
    hass.config_entries.async_update_entry(entry, data={
        **entry.data, "monta_last_charge_entity": "sensor.monta_last_charge",
        "charger_providers": [*entry.data["charger_providers"], "monta"]})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    # The form sends what is left in the fields: here everything but Monta.
    keep = {key: entry.data[key] for key in ("battery_entity", "odometer_entity", "device_tracker_entity",
                                              "charging_binary_entity", "spot_price_entity", "zaptec_power_entity",
                                              "zaptec_session_energy_entity")}
    result = await hass.config_entries.options.async_configure(result["flow_id"], keep)
    result = await hass.config_entries.options.async_configure(result["flow_id"], entry.data["smart_charge"])
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert "monta" not in entry.data["charger_providers"]
    assert "monta_last_charge_entity" not in entry.data
    assert entry.data["zaptec_power_entity"] == "sensor.charger_power", "the fields that were kept stay"


async def test_monta_can_still_be_chosen(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    hass.states.async_set("sensor.monta_last_charge", "completed")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = {key: entry.data[key] for key in ("battery_entity", "odometer_entity", "device_tracker_entity",
                                                "charging_binary_entity", "spot_price_entity", "zaptec_power_entity",
                                                "zaptec_session_energy_entity")}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**fields, "monta_last_charge_entity": "sensor.monta_last_charge"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], entry.data["smart_charge"])
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.data["charger_providers"] == ["manual", "zaptec", "monta", "spot_price"]
    assert entry.data["monta_last_charge_entity"] == "sensor.monta_last_charge"
