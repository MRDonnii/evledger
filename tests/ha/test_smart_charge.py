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
                soc="50", grace=0, start_delay=0, prices=True):
    """An EV Ledger entry for the car "Bil" with smart charging on (and a simulated Zaptec charger)."""
    hass.states.async_set("sensor.car_battery", soc, {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.car_odometer", "1000", {"unit_of_measurement": "km"})
    hass.states.async_set("device_tracker.car", "home", {"latitude": 56.0, "longitude": 10.0})
    hass.states.async_set("binary_sensor.car_charging", "off")
    if prices:
        hass.states.async_set("sensor.price", "1.0", {"prices": globals()["prices"](cheap_now),
                                                      "unit_of_measurement": "kr/kWh"})
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


async def test_waits_when_expensive_and_stops_running_charge(hass: HomeAssistant, request, freezer):
    # At noon, so the cheap hour (5 hours on) lies before the default ready-by time (06:45) and is not cut by it.
    freezer.move_to(dt_util.now().replace(hour=12, minute=10, second=0, microsecond=0))
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
    assert state(hass, "select.bil_charge_mode") == "smart", "a start not from the plan is confirmed first (5 min)"
    await later(hass, freezer, 3)
    assert state(hass, "select.bil_charge_mode") == "smart"
    await later(hass, freezer, 3)
    assert state(hass, "select.bil_charge_mode") == "now"
    assert not calls["switch.turn_off"], "not stopped while it was being confirmed"
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


async def test_default_plan_is_used_and_returned_to(hass: HomeAssistant, request, freezer):
    await setup(hass, request, cheap_now=False)
    assert state(hass, "select.bil_default_plan") == "smart"

    async def choose(entity_id, option):
        await hass.services.async_call("select", "select_option", {"entity_id": entity_id, "option": option},
                                       blocking=True)

    await choose("select.bil_default_plan", "fixed")
    assert state(hass, "select.bil_charge_mode") == "fixed", "the plan that ran as the default follows it"
    assert hass.states.get("select.bil_charge_mode").attributes["default_mode"] == "fixed"
    await choose("select.bil_charge_mode", "now")
    hass.states.async_set(MODE, "disconnected")
    await hass.async_block_till_done()
    await later(hass, freezer)
    assert state(hass, "select.bil_charge_mode") == "fixed", "back to the default plan, not the cheapest"
    await choose("select.bil_charge_mode", "price_cap")
    await choose("select.bil_default_plan", "smart")
    assert state(hass, "select.bil_charge_mode") == "price_cap", "a chosen temporary plan is kept"
    with pytest.raises(Exception):
        await choose("select.bil_default_plan", "off")


def car_with_limit(hass, limit: float) -> None:
    """The car's battery sensor and charge limit on one device, like Tesla Custom makes them."""
    tesla = MockConfigEntry(domain="tesla_custom")
    tesla.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=tesla.entry_id,
                                                    identifiers={("tesla_custom", "vin")})
    registry = er.async_get(hass)
    for domain, unique, object_id in (("sensor", "vin_battery", "car_battery"),
                                      ("number", "vin_charge_limit", "car_charge_limit")):
        registry.async_get_or_create(domain, "tesla_custom", unique, device_id=device.id, config_entry=tesla,
                                     suggested_object_id=object_id)
    hass.states.async_set("number.car_charge_limit", str(limit), {"unit_of_measurement": "%"})


def phones(hass) -> list:
    sent = []

    async def fake_notify(call):
        sent.append((call.service, dict(call.data)))

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    return sent


async def with_phone(hass, entry):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a"]}})
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_car_limit_caps_the_plan_target(hass: HomeAssistant, request):
    car_with_limit(hass, 70)
    await setup(hass, request, cheap_now=False)
    assert float(state(hass, "sensor.bil_plan_target_soc")) == 70
    assert hass.states.get("select.bil_charge_mode").attributes["car_limit"] == 70


async def test_target_soc_sets_the_car_charge_limit(hass: HomeAssistant, request):
    car_with_limit(hass, 70)
    hass.states.async_set("number.car_charge_limit", "70", {"unit_of_measurement": "%", "min": 50, "max": 100})
    calls = []

    async def fake_set(call):
        calls.append(dict(call.data))
        hass.states.async_set("number.car_charge_limit", str(call.data["value"]), {"min": 50, "max": 100})

    await setup(hass, request, cheap_now=False)
    hass.services.async_register("number", "set_value", fake_set)  # after setup: the number component has its own
    target = hass.data["entity_components"]["number"].get_entity("number.bil_target_soc")

    async def set_target(value):
        await target.async_set_native_value(value)
        await hass.async_block_till_done(wait_background_tasks=True)

    await set_target(85)
    assert {"entity_id": "number.car_charge_limit", "value": 85.0} in calls
    assert float(state(hass, "sensor.bil_plan_target_soc")) == 85
    # Below what the car accepts: the car gets its lowest limit, the plan keeps the lower target.
    await set_target(40)
    assert calls[-1] == {"entity_id": "number.car_charge_limit", "value": 50.0}
    assert float(state(hass, "sensor.bil_plan_target_soc")) == 40
    # Already the same: no command to the car.
    count = len(calls)
    await set_target(50)
    assert len(calls) == count


async def test_target_soc_prefers_a_commanding_integration_for_the_limit(hass: HomeAssistant, request):
    """Tesla Custom reads the limit, Tesla Fleet (same car name) gets the command."""
    car_with_limit(hass, 70)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    battery = registry.async_get("sensor.car_battery")
    name = "Bil"
    devices.async_update_device(battery.device_id, name=name)
    fleet = MockConfigEntry(domain="tesla_fleet")
    fleet.add_to_hass(hass)
    device = devices.async_get_or_create(config_entry_id=fleet.entry_id, identifiers={("tesla_fleet", "vin")},
                                         name=name)
    registry.async_get_or_create("number", "tesla_fleet", "vin-charge_state_charge_limit_soc", device_id=device.id,
                                 config_entry=fleet, suggested_object_id="car_fleet_charge_limit")
    hass.states.async_set("number.car_fleet_charge_limit", "70", {"min": 50, "max": 100})
    calls = []

    async def fake_set(call):
        calls.append(dict(call.data))

    await setup(hass, request, cheap_now=False)
    hass.services.async_register("number", "set_value", fake_set)
    target = hass.data["entity_components"]["number"].get_entity("number.bil_target_soc")
    await target.async_set_native_value(90)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [{"entity_id": "number.car_fleet_charge_limit", "value": 90.0}]


async def test_full_car_is_done_not_stopped_from_outside(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    car_with_limit(hass, 80)
    entry, calls = await setup(hass, request, charger_state="connected_charging", soc="78")
    await with_phone(hass, entry)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "now"}, blocking=True)
    # The car reaches its own limit and stops drawing power.
    hass.states.async_set("sensor.car_battery", "80", {"unit_of_measurement": "%"})
    hass.states.async_set(MODE, "connected_finished")
    hass.states.async_set(SWITCH, "off")
    await hass.async_block_till_done()
    for _ in range(4):
        await later(hass, freezer, 10)
    assert not calls["switch.turn_on"], "a full car is not started again"
    assert state(hass, "sensor.bil_charge_status") == "done"
    assert not [data for _, data in sent if "stoppet" in data.get("message", "")]


async def test_price_cap_asks_before_exceeding_it(hass: HomeAssistant, request, freezer, monkeypatch):
    monkeypatch.setattr(planner_module, "DEFAULT_WAKE_MINUTES", 0.0)  # the message's exact figures, without waking
    sent = phones(hass)
    entry, calls = await setup(hass, request, cheap_now=False)
    await with_phone(hass, entry)
    ready = (dt_util.now() + timedelta(hours=8)).strftime("%H:%M:00")
    await hass.services.async_call("time", "set_value", {"entity_id": "time.bil_ready_by", "time": ready},
                                   blocking=True)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_charge_mode", "option": "price_cap"}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    cap = [data for _, data in sent if "prisloftet" in data.get("title", "")]
    assert len(cap) == 1
    # 20 kWh to 80 %: 11 kWh in the 0.5 kr hour, 9 kWh above the 1.5 kr cap at 3 kr
    text = cap[0]["message"]
    assert "66 %" in text and "9,0 kWh over loftet" in text and "3,00 kr/kWh" in text and "13,50 kr ekstra" in text
    assert [action["title"] for action in cap[0]["data"]["actions"]] == ["Godkend", "Stop over loftet"]
    switch = hass.states.get("switch.bil_exceed_price_cap")
    assert switch.state == "on", "without an answer the plan goes on above the cap"
    assert switch.attributes["over_cap_kwh"] == 9.0
    await later(hass, freezer, 5)
    assert len([1 for _, data in sent if "prisloftet" in data.get("title", "")]) == 1, "asked once"
    stop = cap[0]["data"]["actions"][1]["action"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": stop})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state(hass, "switch.bil_exceed_price_cap") == "off"
    assert hass.states.get("switch.bil_exceed_price_cap").attributes["over_cap_kwh"] == 0


async def test_confirm_keeps_the_default_plan(hass: HomeAssistant, request):
    await setup(hass, request, cheap_now=False)
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.bil_default_plan", "option": "fixed"}, blocking=True)
    await hass.services.async_call("button", "press", {"entity_id": "button.bil_confirm_plan"}, blocking=True)
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
    assert not any("ikke denne bil" in message["message"] for message in sent), "the car may still be reporting"
    await later(hass, freezer, 5)
    assert any("ikke denne bil" in message["message"] for message in sent)
    actions = {a["title"]: a["action"] for a in sent[-1]["data"]["actions"]}
    hass.bus.async_fire("mobile_app_notification_action", {"action": actions["Lad nu"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert calls["button.press"], "Lad nu charges the other car too"


async def two_integrations_car(hass: HomeAssistant, request, freezer, charger_state="disconnected", cheap_now=True):
    """The car "Bil" from two integrations on same-named devices: Tesla Custom (battery, stale plug sensor "charger")
    and Tesla Fleet (the cable), as at home."""
    sent = []

    async def fake_notify(call):
        sent.append(call.data)

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    custom = MockConfigEntry(domain="tesla_custom")
    custom.add_to_hass(hass)
    custom_device = devices.async_get_or_create(config_entry_id=custom.entry_id, identifiers={("tesla_custom", "1")},
                                                name="Bil")
    registry.async_get_or_create("sensor", "tesla_custom", "vin_battery", device_id=custom_device.id,
                                 config_entry=custom, suggested_object_id="car_battery")
    registry.async_get_or_create("binary_sensor", "tesla_custom", "vin_charger", device_id=custom_device.id,
                                 config_entry=custom, suggested_object_id="car_plug", original_device_class="plug")
    fleet = MockConfigEntry(domain="tesla_fleet")
    fleet.add_to_hass(hass)
    fleet_device = devices.async_get_or_create(config_entry_id=fleet.entry_id, identifiers={("tesla_fleet", "1")},
                                               name="Bil")
    registry.async_get_or_create("binary_sensor", "tesla_fleet", "vin-charge_state_conn_charge_cable",
                                 device_id=fleet_device.id, config_entry=fleet, suggested_object_id="car_cable")
    hass.states.async_set("binary_sensor.car_plug", "off")  # "Disconnected" since the last time the car was awake
    hass.states.async_set("binary_sensor.car_cable", "off")
    entry, calls = await setup(hass, request, charger_state=charger_state, cheap_now=cheap_now)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a"]}})
    await hass.async_block_till_done()
    await later(hass, freezer, 3)
    return entry, calls, sent


async def test_one_plug_sensor_saying_plugged_in_is_enough(hass: HomeAssistant, request, freezer):
    entry, calls, sent = await two_integrations_car(hass, request, freezer)
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, "sensor.bil_charge_status") == "other_car", "no sensor of the car says plugged in yet"
    hass.states.async_set("binary_sensor.car_cable", "on")  # Tesla Fleet reports; Tesla Custom stays "Disconnected"
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert state(hass, "sensor.bil_charge_status") != "other_car"
    assert state(hass, "binary_sensor.car_plug") == "off"
    assert not any("ikke denne bil" in message["message"] for message in sent)
    assert any(message["title"] == "Bil: ladeplan aktiv" for message in sent), "the plan is told when the car reports"
    await later(hass, freezer, 10)
    assert not any("ikke denne bil" in message["message"] for message in sent)


async def test_other_car_alert_waits_for_the_car_to_report(hass: HomeAssistant, request, freezer):
    entry, calls, sent = await two_integrations_car(hass, request, freezer)
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 2)
    assert state(hass, "sensor.bil_charge_status") == "other_car"
    assert not any("ikke denne bil" in message["message"] for message in sent), "not before the car has had time"
    await later(hass, freezer, 4)
    assert sum("ikke denne bil" in message["message"] for message in sent) == 1, "told once when it stays"



def limit_prices(tonight: float = 0.5) -> list[dict]:
    """From noon: 17-18 today `tonight`, tomorrow 12-15 0.80 (day), the night after 01-04 0.60, the rest 3.00."""
    start = dt_util.now().replace(minute=0, second=0, microsecond=0)
    cheap = {5: tonight, 24: 0.8, 25: 0.8, 26: 0.8, 37: 0.6, 38: 0.6, 39: 0.6}
    return [{"start": (start + timedelta(hours=hour)).isoformat(),
             "end": (start + timedelta(hours=hour + 1)).isoformat(), "price": cheap.get(hour, 3.0)}
            for hour in range(-1, 49)]


async def limit_setup(hass, request, freezer, soc="49", price_list=None, min_saving=None):
    sent = phones(hass)
    entry, calls = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc=soc)
    hass.states.async_set("sensor.price", "3.0", {"prices": limit_prices() if price_list is None else price_list,
                                                  "unit_of_measurement": "kr/kWh"})
    await with_phone(hass, entry)
    if min_saving is not None:
        await hass.services.async_call("number", "set_value", {
            "entity_id": "number.bil_ask_when_waiting_saves_at_least", "value": min_saving}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bil_ask_before_dear_charging"},
                                   blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    return entry, calls, sent


def asked_about_waiting(sent) -> list:
    return [data for _, data in sent if "billigere at vente" in data.get("title", "")]


async def test_price_limit_asks_whether_to_wait(hass: HomeAssistant, request, freezer, monkeypatch):
    """The plan charges to the target; when waiting with the dear rest saves enough, the phones are asked."""
    monkeypatch.setattr(planner_module, "DEFAULT_WAKE_MINUTES", 0.0)  # the message's exact figures, without waking
    entry, calls, sent = await limit_setup(hass, request, freezer)
    planner = hass.data[DOMAIN][entry.entry_id].smart  # the entry was reloaded with the phone
    assert planner.schedule.energy_kwh == pytest.approx(31 * 60 / 100 / 0.9, abs=0.01), "to the target 80 %"
    asked = asked_about_waiting(sent)
    assert len(asked) == 1, "cheaper later by more than 10 kr: the phones are asked"
    text = asked[0]["message"]
    assert text.startswith("Bilen er på 49 %. Til målet 80 % koster de sidste 9,7 kWh ca. 3,00 kr/kWh i nat.")
    assert "Lad kun til 66 %: ca. 5,50 kr i nat." in text
    assert "Resten om dagen i morgen kl. 12:00–12:52" in text and "Eller om natten" in text and "spar ca." in text
    assert text.endswith("Uden svar: lad til 80 %.")
    assert [action["title"] for action in asked[0]["data"]["actions"]] == ["Lad kun til 66 %", "Lad til 80 %"], \
        "the answers say what they charge to tonight"
    attrs = hass.states.get("sensor.bil_next_charge_start").attributes
    assert attrs["limit_wait"] is False and attrs["limit_ask"] is True
    assert attrs["above_limit_kwh"] == pytest.approx(9.67, abs=0.01)
    assert attrs["price_limit"] == 0.6, "dearer than the best later period (the night at 0.60)"
    assert [window["kind"] for window in attrs["limit_cheaper"]] == ["day", "night"]
    assert state(hass, "switch.bil_wait_above_the_price_limit") == "off"
    await later(hass, freezer, 5)
    assert len(asked_about_waiting(sent)) == 1, "asked once"
    for value in (9, 11, 10):  # a number stepped up and down: the question is not asked again
        await hass.services.async_call("number", "set_value", {
            "entity_id": "number.bil_ask_when_waiting_saves_at_least", "value": value}, blocking=True)
        await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 1)
    assert len(asked_about_waiting(sent)) == 1, "a new setting does not ask again"
    hass.bus.async_fire("mobile_app_notification_action", {"action": asked[0]["data"]["actions"][0]["action"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state(hass, "switch.bil_wait_above_the_price_limit") == "on"
    assert planner.schedule.energy_kwh == pytest.approx(11.0, abs=0.01), "Vent med resten: tonight's cheap hour only"
    assert state(hass, "select.bil_charge_mode") == "smart"
    hass.bus.async_fire("mobile_app_notification_action", {"action": asked[0]["data"]["actions"][1]["action"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state(hass, "switch.bil_wait_above_the_price_limit") == "off"
    assert planner.schedule.energy_kwh == pytest.approx(20.67, abs=0.01), "Lad til målet"
    await later(hass, freezer, 5)
    assert len(asked_about_waiting(sent)) == 1, "answered: not asked again"


async def test_a_failing_extra_never_stops_the_plan(hass: HomeAssistant, request, freezer, caplog):
    """A fault in an extra (the price question, a message) is logged once; the plan still controls the charger."""
    entry, _, _ = await limit_setup(hass, request, freezer)
    planner = hass.data[DOMAIN][entry.entry_id].smart
    with (patch.object(planner, "_limit_view", side_effect=RuntimeError("boom")),
          patch.object(planner.routines, "check_started", side_effect=KeyError("x")),
          patch.object(planner, "_control", wraps=planner._control) as control):
        planner.async_recalculate()
        planner.async_recalculate()
    assert control.call_count == 2, "the charger is still controlled"
    assert planner.schedule.energy_kwh == pytest.approx(31 * 60 / 100 / 0.9, abs=0.01), "the plan to the target"
    assert planner.limit_view is None and planner._limit_params is None
    assert caplog.text.count("price question failed;") == 1, "logged once, not every minute"
    assert "started message failed" in caplog.text


async def test_price_limit_asks_only_when_waiting_saves_enough(hass: HomeAssistant, request, freezer):
    _, _, sent = await limit_setup(hass, request, freezer, min_saving=30)
    assert not asked_about_waiting(sent), "waiting saves about 23 kr, less than 30"


async def test_price_limit_does_not_ask_when_later_is_not_cheaper(hass: HomeAssistant, request, freezer):
    # Known prices only until tomorrow afternoon, all 3.00 after the ready-by time: a price borrowed from today's
    # cheap hour is no cheaper period.
    _, _, sent = await limit_setup(hass, request, freezer, price_list=prices(cheap_now=False))
    assert not asked_about_waiting(sent)


async def test_price_limit_daily_driving_sets_tonight_s_level(hass: HomeAssistant, request, freezer):
    entry, _, _ = await limit_setup(hass, request, freezer, price_list=limit_prices(tonight=3.0))
    planner = hass.data[DOMAIN][entry.entry_id].smart
    with patch.object(planner, "daily_use_kwh", return_value=6.0):  # 10 % of the battery a day
        planner.async_recalculate()
        await hass.async_block_till_done()
        view = planner.limit_view
    night = next(window for window in view["windows"] if window["kind"] == "night")
    days = (night["start"] - dt_util.now()).total_seconds() / 86400
    assert night["need_soc"] == pytest.approx(20 + 10 * days, abs=0.1), "the reserve plus the driving until then"
    labels = planner.notify.wait_labels(planner, 49.0, {**view, "below_soc": 49.0})
    assert labels == ("Lad ikke i nat", "Lad til 80 %"), "nothing to charge tonight when waiting"


async def test_price_limit_in_cheapest_waits_instead_of_done(hass: HomeAssistant, request, freezer):
    entry, calls, _ = await limit_setup(hass, request, freezer, price_list=limit_prices(tonight=3.0))
    planner = hass.data[DOMAIN][entry.entry_id].smart
    assert planner.schedule.energy_kwh > 0, "without an answer the plan charges to the target"
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.bil_wait_above_the_price_limit"},
                                   blocking=True)
    await later(hass, freezer, 1)
    assert planner.schedule.energy_kwh == 0, "waiting: every quarter tonight is dearer than later"
    assert state(hass, "sensor.bil_charge_status") == "waiting", "not 'target reached'"
    attrs = hass.states.get("sensor.bil_next_charge_start").attributes
    assert attrs["limit_wait"] is True and attrs["above_limit_kwh"] > 0
    assert "Du valgte at vente" in planner.notify.plan_text(planner)
    assert not calls["switch.turn_on"] and not calls["button.press"]
    hass.states.async_set("sensor.car_battery", "10")
    await later(hass, freezer, 1)
    assert planner.schedule.energy_kwh > 0, "below the 20 % reserve: charged to it whatever the price"
    assert round(planner.schedule.limit_soc) == 20


async def test_forecast_prices_get_a_safety_margin_before_asking(hass: HomeAssistant, request, freezer):
    """A later period priced from the forecast counts 10 % dearer when working out what waiting saves: forecasts
    miss, and waiting must still pay when they do."""
    entry, _, _ = await limit_setup(hass, request, freezer)
    planner = hass.data[DOMAIN][entry.entry_id].smart
    known = planner.limit_view["windows"]
    assert known and not any(window["estimated"] for window in known)
    with patch.object(planner_module, "is_forecast", lambda _hass, _entity: True):
        planner.async_recalculate()
        view = planner.limit_view
        with patch.object(planner_module, "FORECAST_MARGIN", 0.0):
            planner.async_recalculate()
            plain = planner.limit_view
    assert view and plain, "still worked out with forecast prices"
    best, best_plain = max(view["windows"], key=lambda w: w["saving"]), max(plain["windows"], key=lambda w: w["saving"])
    assert best["estimated"]
    assert best["saving"] < best_plain["saving"], "the margin makes waiting look less good"
    assert best["saving"] == pytest.approx(best_plain["saving"] - best["over_kwh"] * best["price"] * 0.1, abs=0.6)
