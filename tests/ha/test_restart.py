"""Everything the user set, and the charge control itself, must survive a Home Assistant restart."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from .test_smart_charge import MODE, setup, state


def restore(hass, states: list[State], numbers: dict[str, float] | None = None):
    mock_restore_cache_with_extra_data(hass, [(item, {}) for item in states] + [
        (State(entity_id, str(value)), {"native_value": value, "native_min_value": 0,
                                        "native_max_value": 1000, "native_step": 1,
                                        "native_unit_of_measurement": None})
        for entity_id, value in (numbers or {}).items()])


async def test_settings_survive(hass: HomeAssistant, request):
    departure = (dt_util.now() + timedelta(days=1)).replace(second=0, microsecond=0)
    restore(hass, [
        State("select.bil_charge_mode", "price_cap", {"mode_before_now": "smart"}),
        State("time.bil_ready_by", "06:45:00"),
        State("time.bil_fixed_charging_start", "23:15:00"),
        State("datetime.bil_temporary_departure", departure.isoformat()),
        State("switch.bil_round_trip", "off"),
        State("text.bil_trip_destination", "Aarhus", {"distance_km": 120.0, "duration_min": 80,
                                                    "name": "Aarhus, Danmark", "latitude": 56.15,
                                                    "longitude": 10.2, "method": "route"}),
    ], {"number.bil_target_soc": 100, "number.bil_price_cap": 0.9, "number.bil_consumption": 200})
    await setup(hass, request, charger=False, cheap_now=False)
    assert state(hass, "select.bil_charge_mode") == "price_cap"
    assert float(state(hass, "number.bil_target_soc")) == 100
    assert float(state(hass, "number.bil_price_cap")) == 0.9
    assert state(hass, "time.bil_ready_by") == "06:45:00"
    assert state(hass, "time.bil_fixed_charging_start") == "23:15:00"
    assert dt_util.parse_datetime(state(hass, "datetime.bil_temporary_departure")) == departure
    assert state(hass, "switch.bil_round_trip") == "off"
    # the route comes back without a new lookup: 120 km * 200 Wh * 1.15 = 27.6 kWh
    assert state(hass, "text.bil_trip_destination") == "Aarhus"
    assert float(state(hass, "sensor.bil_trip_energy")) == 27.6


async def test_passed_departure_is_dropped(hass: HomeAssistant, request):
    restore(hass, [State("datetime.bil_temporary_departure", (dt_util.now() - timedelta(hours=1)).isoformat())])
    await setup(hass, request, charger=False)
    assert state(hass, "datetime.bil_temporary_departure") == "unknown"


async def test_charge_now_survives_while_plugged_in(hass: HomeAssistant, request):
    restore(hass, [State("select.bil_charge_mode", "now",
                         {"mode_before_now": "fixed", "now_seen_connected": True})])
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=False)
    assert state(hass, "select.bil_charge_mode") == "now"
    assert not calls["switch.turn_off"]


async def test_unplugged_while_down_ends_charge_now(hass: HomeAssistant, request):
    restore(hass, [State("select.bil_charge_mode", "now",
                         {"mode_before_now": "fixed", "now_seen_connected": True})])
    await setup(hass, request, charger_state="disconnected")
    assert state(hass, "select.bil_charge_mode") == "smart", "a temporary plan that has run returns to the cheapest"


async def test_charge_now_set_before_plugging_in_waits_for_the_car(hass: HomeAssistant, request):
    restore(hass, [State("select.bil_charge_mode", "now",
                         {"mode_before_now": "smart", "now_seen_connected": False})])
    await setup(hass, request, charger_state="disconnected")
    assert state(hass, "select.bil_charge_mode") == "now"
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done()
    hass.states.async_set(MODE, "disconnected")
    await hass.async_block_till_done()
    assert state(hass, "select.bil_charge_mode") == "smart"


async def test_running_planned_charge_is_not_interrupted(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=True)
    assert not calls["switch.turn_off"] and not calls["switch.turn_on"]
    assert state(hass, "sensor.bil_charge_status") == "charging"


async def test_planned_start_is_picked_up_after_restart(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, charger_state="connected_finished", cheap_now=True)
    assert calls["switch.turn_on"] == ["switch.charger_charging"]


async def test_reload_keeps_everything(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger=False, cheap_now=False)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "fixed"}, blocking=True)
    await hass.services.async_call("number", "set_value",
                                   {"entity_id": "number.bil_target_soc", "value": 90}, blocking=True)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert state(hass, "select.bil_charge_mode") == "fixed"
    assert state(hass, "number.bil_target_soc") == "90.0"


async def test_missing_battery_after_restart_does_not_stop_charging(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=False, soc="unavailable")
    assert not calls["switch.turn_off"]
    hass.states.async_set("sensor.car_battery", "50")
    await hass.async_block_till_done()
    assert calls["switch.turn_off"] == ["switch.charger_charging"]


async def test_missing_prices_after_restart_do_not_stop_charging(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=False)
    calls.clear()
    hass.states.async_set("sensor.price", "unavailable", {})
    await hass.async_block_till_done()
    assert not calls


async def test_no_commands_in_the_first_minute_after_start(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=False, grace=60)
    assert not calls


async def test_open_phone_question_survives(hass: HomeAssistant, request):
    since = dt_util.now() - timedelta(minutes=5)
    restore(hass, [State("switch.bil_confirm_plan_on_phone", "on", {"awaiting_since": since.isoformat()})])
    _, calls = await setup(hass, request, charger_state="connected_requesting", cheap_now=True)
    assert state(hass, "switch.bil_confirm_plan_on_phone") == "on"
    assert state(hass, "sensor.bil_charge_status") == "awaiting_confirmation"
    assert not calls["button.press"]
