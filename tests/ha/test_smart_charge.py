"""End-to-end tests in Home Assistant with a simulated Zaptec charger."""

from collections import defaultdict
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.evledger.smart import charger as charger_module
from custom_components.evledger.smart import planner as planner_module

DOMAIN = "evledger"
MODE = "sensor.charger_mode"
SWITCH = "switch.charger_charging"
AUTHORIZE = "button.charger_authorize"


def prices(cheap_now: bool) -> list[dict]:
    start = dt_util.now().replace(minute=0, second=0, microsecond=0)
    result = []
    for hour in range(-1, 30):
        begin = start + timedelta(hours=hour)
        price = 0.1 if (cheap_now and hour == 0) else (0.5 if hour == 5 else 3.0)
        result.append({"start": begin.isoformat(), "end": (begin + timedelta(hours=1)).isoformat(), "price": price})
    return result


async def setup(hass: HomeAssistant, request, charger_state="connected_finished", charger=True, cheap_now=True,
                soc="50", grace=0, start_delay=0):
    """An EV Ledger entry for the car "Bil" with smart charging on (and a simulated Zaptec charger)."""
    hass.states.async_set("sensor.car_battery", soc, {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.car_odometer", "1000", {"unit_of_measurement": "km"})
    hass.states.async_set("device_tracker.car", "home", {"latitude": 56.0, "longitude": 10.0})
    hass.states.async_set("binary_sensor.car_charging", "off")
    hass.states.async_set("sensor.price", "1.0", {"prices": prices(cheap_now), "unit_of_measurement": "kr/kWh"})
    data = {"vehicle_name": "Bil", "vehicle_provider": "tesla_custom", "currency": "DKK",
            "battery_entity": "sensor.car_battery", "odometer_entity": "sensor.car_odometer",
            "device_tracker_entity": "device_tracker.car", "charging_binary_entity": "binary_sensor.car_charging",
            "charger_providers": ["manual", "spot_price"], "spot_price_entity": "sensor.price",
            "battery_capacity_kwh": 60, "smart_charge": {"enabled": True, "charger_type": "none"}}
    if charger:
        zaptec = MockConfigEntry(domain="zaptec")
        zaptec.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(config_entry_id=zaptec.entry_id,
                                                        identifiers={("zaptec", "charger")})
        registry = er.async_get(hass)
        for domain, unique, object_id in (("sensor", "abc_charger_operation_mode", "charger_mode"),
                                          ("switch", "abc_charger_operation_mode", "charger_charging"),
                                          ("button", "abc_authorize_charge", "charger_authorize"),
                                          ("sensor", "abc_total_charge_power", "charger_power")):
            registry.async_get_or_create(domain, "zaptec", unique, device_id=device.id,
                                         config_entry=zaptec, suggested_object_id=object_id)
        hass.states.async_set(MODE, charger_state)
        hass.states.async_set(SWITCH, "on" if charger_state == "connected_charging" else "off")
        hass.states.async_set("sensor.charger_power", "0", {"unit_of_measurement": "W"})
        hass.states.async_set("sensor.charger_session", "0", {"unit_of_measurement": "kWh"})
        data["charger_providers"].append("zaptec")
        data |= {"zaptec_power_entity": "sensor.charger_power",
                 "zaptec_session_energy_entity": "sensor.charger_session"}
        data["smart_charge"] = {"enabled": True}  # Zaptec control is found from the ledger's charger
    calls: dict[str, list[str]] = defaultdict(list)

    async def record(self, domain, service, entity_id):
        calls[f"{domain}.{service}"].append(entity_id)

    patcher = patch.object(charger_module.ChargerBackend, "_call", record)
    patcher.start()
    request.addfinalizer(patcher.stop)
    for name, value in (("STARTUP_GRACE_SECONDS", grace), ("START_DELAY_SECONDS", start_delay)):
        patcher = patch.object(planner_module, name, value)
        patcher.start()
        request.addfinalizer(patcher.stop)
    entry = MockConfigEntry(domain=DOMAIN, title="Bil", data=data)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entry.runtime_data = hass.data[DOMAIN][entry.entry_id].smart
    return entry, calls


def state(hass, entity_id):
    return hass.states.get(entity_id).state


async def later(hass, freezer, minutes=3):
    """Move the clock (and the planner's minute tick) forward, e.g. past the unplug grace."""
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def tick(hass, minutes=1):
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes))
    await hass.async_block_till_done()


async def test_starts_paused_charger_in_cheap_slot(hass: HomeAssistant, request):
    _, calls = await setup(hass, request)
    assert calls["switch.turn_on"] == [SWITCH]
    assert state(hass, "sensor.bil_charge_status") == "starting"


async def test_authorizes_waiting_charger(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, charger_state="connected_requesting")
    assert calls["button.press"] == [AUTHORIZE]
    assert not calls["switch.turn_on"]


async def test_waits_when_expensive_and_stops_running_charge(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=False)
    assert calls["switch.turn_off"] == [SWITCH]
    hass.states.async_set(MODE, "connected_finished")
    await hass.async_block_till_done()
    assert state(hass, "sensor.bil_charge_status") == "waiting"
    expected = dt_util.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=5)
    assert dt_util.parse_datetime(state(hass, "sensor.bil_next_charge_start")) == expected


async def test_pause_mode_never_starts(hass: HomeAssistant, request):
    _, calls = await setup(hass, request, cheap_now=False)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "off"}, blocking=True)
    await tick(hass, 10)
    assert not calls["switch.turn_on"]
    assert state(hass, "sensor.bil_charge_status") == "paused"


async def test_manual_start_switches_to_charge_now_until_unplugged(hass: HomeAssistant, request, freezer):
    _, calls = await setup(hass, request, cheap_now=False)
    hass.states.async_set(MODE, "connected_charging")
    await hass.async_block_till_done()
    assert state(hass, "select.bil_charge_mode") == "now"
    assert not calls["switch.turn_off"]
    hass.states.async_set(MODE, "disconnected")
    await hass.async_block_till_done()
    assert state(hass, "select.bil_charge_mode") == "now", "a short disconnect (charger reboot) keeps the plan"
    await later(hass, freezer)
    assert state(hass, "select.bil_charge_mode") == "smart"


async def test_trip_with_coordinates(hass: HomeAssistant, request, aioclient_mock):
    aioclient_mock.get("https://router.project-osrm.org/route/v1/driving/10.0,56.0;10.5,56.5",
                       json={"code": "Ok", "routes": [{"distance": 100000, "duration": 3600}]})
    hass.config.latitude, hass.config.longitude = 56.0, 10.0
    await setup(hass, request, charger=False, cheap_now=False)
    departure = dt_util.now() + timedelta(hours=10)
    await hass.services.async_call("datetime", "set_value", {"entity_id": "datetime.bil_temporary_departure",
                                                             "datetime": departure}, blocking=True)
    await hass.services.async_call("text", "set_value", {"entity_id": "text.bil_trip_destination",
                                                         "value": "56.5, 10.5"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert float(state(hass, "sensor.bil_trip_distance")) == 100
    # 200 km * 180 Wh * 1.15 = 41.4 kWh -> 10 % + 69 % = 79 %
    assert float(state(hass, "sensor.bil_trip_soc_needed")) == pytest.approx(79.0)
    assert float(state(hass, "sensor.bil_plan_target_soc")) == 80  # the daily target is higher
    await hass.services.async_call("button", "press", {"entity_id": "button.bil_clear_temporary_plan"},
                                   blocking=True)
    assert state(hass, "datetime.bil_temporary_departure") == "unknown"


async def test_alternative_plans_are_priced(hass: HomeAssistant, request):
    await setup(hass, request, charger=False, cheap_now=False)
    alternatives = hass.states.get("sensor.bil_planned_charge_cost").attributes["alternatives"]
    assert set(alternatives) == {"now", "smart", "fixed", "price_cap"}
    # 20 kWh: now at 3 kr/kWh, the cheapest plan in the 0.5 kr hour and the default 22-06 window
    assert alternatives["now"]["cost"] > alternatives["smart"]["cost"]
    assert alternatives["smart"]["cost"] == float(hass.states.get("sensor.bil_planned_charge_cost").state)
    assert alternatives["fixed"]["start"] is not None


async def test_start_waits_for_a_steady_plan(hass: HomeAssistant, request, freezer):
    _, calls = await setup(hass, request, start_delay=15)
    assert not calls["switch.turn_on"]
    assert state(hass, "sensor.bil_charge_status") == "starting"
    freezer.tick(timedelta(seconds=17))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls["switch.turn_on"] == [SWITCH]


async def test_any_temporary_plan_returns_to_cheapest_after_unplug(hass: HomeAssistant, request, freezer):
    await setup(hass, request, cheap_now=False)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "fixed"}, blocking=True)
    hass.states.async_set(MODE, "disconnected")
    await hass.async_block_till_done()
    await later(hass, freezer)
    assert state(hass, "select.bil_charge_mode") == "smart"
    # chosen while unplugged: kept for the next time the car is plugged in
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "fixed"}, blocking=True)
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done()
    assert state(hass, "select.bil_charge_mode") == "fixed"


async def test_confirm_on_phone(hass: HomeAssistant, request, freezer):
    sent = []

    async def fake_notify(call):
        sent.append((call.service, call.data))

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    hass.services.async_register("notify", "mobile_app_b", fake_notify)
    hass.states.async_set("device_tracker.a", "home")
    hass.states.async_set("device_tracker.b", "not_home")
    entry, calls = await setup(hass, request, charger_state="disconnected", cheap_now=True)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a", "mobile_app_b"], "notify_only_home": True}})
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bil_confirm_plan_on_phone"},
                                   blocking=True)
    await later(hass, freezer)
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [service for service, _ in sent] == ["mobile_app_a"], "only the phone that is home"
    assert state(hass, "sensor.bil_charge_status") == "awaiting_confirmation"
    assert not calls["button.press"], "nothing starts before the answer, even in a cheap hour"
    actions = [action["action"] for action in sent[0][1]["data"]["actions"]]
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions[0]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state(hass, "sensor.bil_charge_status") != "awaiting_confirmation"
    assert calls["button.press"], "confirmed: the cheapest plan runs (cheap now)"
    assert sent[-1][1]["message"] == "clear_notification"


async def test_plan_info_on_phone_with_charge_now(hass: HomeAssistant, request, freezer):
    sent = []

    async def fake_notify(call):
        sent.append(call.data)

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    entry, calls = await setup(hass, request, charger_state="disconnected", cheap_now=False)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a"], "notify_url": "/dash/car"}})
    await hass.async_block_till_done()
    assert state(hass, "switch.bil_notify_plan_on_phone") == "on"
    await later(hass, freezer)
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(sent) == 1
    assert sent[0]["title"] == "Bil: ladeplan aktiv"
    lines = sent[0]["message"].split("\n")
    assert lines[0] == "Plan: Billigst"
    assert lines[1].startswith("Tid: ")
    assert lines[2].startswith("Pris: ") and "(spar " in lines[2]
    assert lines[3].startswith("Energi: ")
    assert sent[0]["data"]["url"] == "/dash/car" and sent[0]["data"]["clickAction"] == "/dash/car"
    assert sent[0]["data"]["notification_icon"] == "mdi:ev-station"
    actions = {action["title"]: action["action"] for action in sent[0]["data"]["actions"]}
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions["Lad nu"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state(hass, "select.bil_charge_mode") == "now"
    assert calls["button.press"], "Lad nu from the phone starts the charger"
    assert sent[-1]["message"].startswith("Plan: Lad nu\nTid: nu"), "the changed plan is sent again"


async def test_send_plan_button(hass: HomeAssistant, request):
    sent = []

    async def fake_notify(call):
        sent.append(call.data)

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    entry, _ = await setup(hass, request, cheap_now=False)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a"]}})
    await hass.async_block_till_done()
    await hass.services.async_call("button", "press", {"entity_id": "button.bil_send_plan_to_phone"}, blocking=True)
    assert sent and sent[0]["message"].startswith("Plan: Billigst\n")



async def test_charger_reboot_keeps_the_plan_and_sends_no_new_message(hass: HomeAssistant, request, freezer):
    sent = []

    async def fake_notify(call):
        sent.append(call.data)

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    entry, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=False)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a"]}})
    await hass.async_block_till_done()
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "now"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    sent.clear()
    # the charger reboots: offline, briefly disconnected, then waiting for authorisation again
    for mode in ("unavailable", "disconnected", "connected_requesting"):
        hass.states.async_set(MODE, mode)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert state(hass, "select.bil_charge_mode") == "now"
    assert not sent, "no new plan message for a reboot"
    await later(hass, freezer, 1)
    assert calls["button.press"], "charging is started again after the reboot"


async def test_charger_offline_and_back_resumes_the_plan(hass: HomeAssistant, request, freezer):
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=True)
    hass.states.async_set(MODE, "unavailable")
    await hass.async_block_till_done()
    assert state(hass, "sensor.bil_charge_status") == "unknown"
    await later(hass, freezer, 30)
    assert not calls["switch.turn_on"] and not calls["switch.turn_off"], "no commands while offline"
    hass.states.async_set(MODE, "connected_finished")
    await hass.async_block_till_done()
    await later(hass, freezer, 1)
    assert calls["switch.turn_on"] == [SWITCH], "back online and paused: started again"


async def test_command_errors_are_retried_without_end(hass: HomeAssistant, request, freezer):
    _, calls = await setup(hass, request, charger_state="connected_finished", cheap_now=True)
    for _ in range(6):  # the charger never reacts
        await later(hass, freezer, 4)
    quick = len(calls["switch.turn_on"])
    assert quick >= 3
    assert state(hass, "sensor.bil_charge_status") == "not_responding"
    await later(hass, freezer, 16)
    assert len(calls["switch.turn_on"]) > quick, "still retried every 15 minutes"


async def test_battery_and_prices_missing_still_charges_before_the_deadline(hass: HomeAssistant, request, freezer):
    _, calls = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="unavailable")
    hass.states.async_set("sensor.price", "unavailable", {})
    await hass.async_block_till_done()
    await later(hass, freezer, 5)
    assert not calls["switch.turn_on"], "waits for the car and the prices first"
    await later(hass, freezer, 6)
    attrs = hass.states.get("sensor.bil_charge_status").attributes
    assert attrs["battery_level_assumed"] is True
    assert float(state(hass, "sensor.bil_planned_charge_energy")) > 0, "plans a charge anyway"


async def test_other_car_alert_and_charge_now_charges_it(hass: HomeAssistant, request, freezer):
    sent = []

    async def fake_notify(call):
        sent.append(call.data)

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    car = MockConfigEntry(domain="tesla_custom")
    car.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=car.entry_id, identifiers={("tesla_custom", "1")})
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", "tesla_custom", "vin_battery", device_id=device.id, config_entry=car,
                                 suggested_object_id="car_battery")
    registry.async_get_or_create("binary_sensor", "tesla_custom", "vin_charger", device_id=device.id,
                                 config_entry=car, suggested_object_id="car_plug", original_device_class="plug")
    hass.states.async_set("binary_sensor.car_plug", "off")
    entry, calls = await setup(hass, request, charger_state="disconnected", cheap_now=True)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a"]}})
    await hass.async_block_till_done()
    await later(hass, freezer)
    hass.states.async_set(MODE, "connected_requesting")  # the family's other car
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, "sensor.bil_charge_status") == "other_car"
    assert not calls["button.press"]
    assert any("ikke denne bil" in message["message"] for message in sent)
    actions = {a["title"]: a["action"] for a in sent[-1]["data"]["actions"]}
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions["Lad nu"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert calls["button.press"], "Lad nu charges the other car too"
