"""Everything the user set, and the charge control itself, must survive a Home Assistant restart."""

from datetime import timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data

from .test_smart_charge import MODE, SWITCH, later, phones, setup, state, with_phone


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


async def test_unplugged_while_down_ends_charge_now(hass: HomeAssistant, request, freezer):
    restore(hass, [State("select.bil_charge_mode", "now",
                         {"mode_before_now": "fixed", "now_seen_connected": True})])
    await setup(hass, request, charger_state="disconnected")
    await later(hass, freezer)
    assert state(hass, "select.bil_charge_mode") == "smart", "a temporary plan that has run returns to the cheapest"


async def test_default_plan_survives_and_is_returned_to(hass: HomeAssistant, request, freezer):
    restore(hass, [State("select.bil_charge_mode", "now", {"mode_before_now": "fixed", "now_seen_connected": True}),
                   State("select.bil_default_plan", "fixed")])
    await setup(hass, request, charger_state="disconnected")
    assert state(hass, "select.bil_default_plan") == "fixed"
    assert state(hass, "select.bil_charge_mode") == "now", "restoring the default does not touch the plan"
    await later(hass, freezer)
    assert state(hass, "select.bil_charge_mode") == "fixed"


async def test_charge_now_set_before_plugging_in_waits_for_the_car(hass: HomeAssistant, request, freezer):
    restore(hass, [State("select.bil_charge_mode", "now",
                         {"mode_before_now": "smart", "now_seen_connected": False})])
    await setup(hass, request, charger_state="disconnected")
    assert state(hass, "select.bil_charge_mode") == "now"
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done()
    hass.states.async_set(MODE, "disconnected")
    await hass.async_block_till_done()
    await later(hass, freezer)
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



async def test_last_battery_level_survives_a_restart(hass: HomeAssistant, request, freezer):
    restore(hass, [State("select.bil_charge_mode", "smart", {"last_soc": 64})])
    await setup(hass, request, charger_state="connected_finished", soc="unavailable")
    await later(hass, freezer, 11)
    assert hass.data["evledger"][next(iter(hass.data["evledger"]))].smart.last_soc == 64
    assert hass.states.get("sensor.bil_charge_status").attributes["battery_level_assumed"] is True



async def test_unknown_battery_is_not_shown_as_target_reached(hass: HomeAssistant, request):
    await setup(hass, request, charger_state="connected_finished", soc="unavailable")
    assert state(hass, "sensor.bil_charge_status") == "unknown"


async def test_price_cap_question_is_not_asked_again_after_a_restart(hass: HomeAssistant, request, freezer):
    ready = (dt_util.now() + timedelta(hours=8)).strftime("%H:%M:00")
    restore(hass, [State("select.bil_charge_mode", "price_cap", {"warned": ["cap"]}),
                   State("time.bil_ready_by", ready)])
    sent = phones(hass)
    entry, _ = await setup(hass, request, cheap_now=False)
    await with_phone(hass, entry)
    await later(hass, freezer, 5)
    assert hass.states.get("switch.bil_exceed_price_cap").attributes["over_cap_kwh"] > 0
    assert not [data for _, data in sent if "prisloftet" in data.get("title", "")]


def saved_plan(start_minutes: float, end_minutes: float) -> list:
    now = dt_util.now()
    return [[(now + timedelta(minutes=start_minutes)).isoformat(), (now + timedelta(minutes=end_minutes)).isoformat()]]


async def test_restart_at_the_planned_start_still_starts(hass: HomeAssistant, request):
    """Neither the car nor the prices are loaded yet: the plan from before the restart is followed."""
    restore(hass, [State("select.bil_charge_mode", "smart", {"last_soc": 50, "planned": saved_plan(-2, 90)})])
    _, calls = await setup(hass, request, soc="unavailable", prices=False)
    assert calls["switch.turn_on"] == [SWITCH]


async def test_last_battery_level_is_used_at_once_after_a_restart(hass: HomeAssistant, request):
    restore(hass, [State("select.bil_charge_mode", "smart", {"last_soc": 50})])
    _, calls = await setup(hass, request, soc="unavailable", cheap_now=True)
    assert calls["switch.turn_on"] == [SWITCH], "no ten minute wait when the level from before is known"


async def test_an_ended_saved_plan_is_not_followed(hass: HomeAssistant, request):
    restore(hass, [State("select.bil_charge_mode", "smart", {"last_soc": 50, "planned": saved_plan(-90, -30)})])
    _, calls = await setup(hass, request, soc="unavailable", prices=False)
    assert not calls["switch.turn_on"]


async def test_cleared_calendar_trip_stays_cleared_after_a_restart(hass: HomeAssistant, request, freezer):
    from homeassistant.core import SupportsResponse

    from .test_routines import with_options
    start = (dt_util.now() + timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
    events = [{"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(), "summary": "Tur",
               "location": ""}]

    async def get_events(call):
        return {"calendar.familie": {"events": list(events)}}

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    restore(hass, [State("datetime.bil_temporary_departure", "unknown", {"dismissed": [f"{start.isoformat()}|Tur"]})])
    entry, _ = await setup(hass, request, charger=False, cheap_now=False)
    planner = await with_options(hass, entry, trip_calendar="calendar.familie", trip_calendar_keyword="tur")
    await later(hass, freezer, 1)
    assert planner.trip.departure is None, "cleared by hand before the restart: not added again"


async def test_done_message_counts_periods_from_before_a_restart(hass: HomeAssistant, request, freezer):
    from .test_routines import phones as all_phones
    from .test_routines import session, with_options
    start = dt_util.now() - timedelta(hours=3)
    run = {"kwh": 6.0, "price": 2.5, "price_known": True, "sessions": 1, "start_soc": 60, "end_soc": 70,
           "started": start.isoformat(), "ended": (start + timedelta(hours=1)).isoformat()}
    restore(hass, [State("switch.bil_message_when_charging_is_done", "on", {"charge_run": run})])
    sent = all_phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_charging", cheap_now=True, soc="70")
    planner = await with_options(hass, entry)
    planner.routines.charge_finished(session(4.0, 1.5, start + timedelta(hours=2), 45, (70, 80)))
    hass.states.async_set("sensor.car_battery", "80")
    hass.states.async_set(MODE, "connected_finished")
    await later(hass, freezer, 1)
    done = [m for m in sent if m["title"] == "Bil: opladning færdig"]
    assert len(done) == 1, sent
    assert done[0]["message"].startswith("10,0 kWh for 4,00 kr (0,40 kr/kWh)\nBatteri 60 % → 80 %"), done[0]
    assert "(2 perioder)" in done[0]["message"]


async def test_an_old_saved_charge_is_not_counted(hass: HomeAssistant, request, freezer):
    from .test_routines import session, with_options
    start = dt_util.now() - timedelta(days=3)
    run = {"kwh": 6.0, "price": 2.5, "price_known": True, "sessions": 1, "start_soc": 60, "end_soc": 70,
           "started": start.isoformat(), "ended": (start + timedelta(hours=1)).isoformat()}
    restore(hass, [State("switch.bil_message_when_charging_is_done", "on", {"charge_run": run})])
    entry, _ = await setup(hass, request, charger_state="connected_charging", cheap_now=True, soc="70")
    planner = await with_options(hass, entry)
    assert planner.routines.run.sessions == 0
    planner.routines.charge_finished(session(4.0, 1.5, dt_util.now() - timedelta(hours=1), 45, (70, 80)))
    await later(hass, freezer, 1)
    assert hass.states.get("switch.bil_message_when_charging_is_done").attributes["charge_run"]["sessions"] == 1


async def test_learned_values_and_the_monthly_summary_survive(hass: HomeAssistant, request):
    restore(hass, [
        State("switch.bil_learn_charging_power_and_efficiency", "on",
              {"learned": {"power_kw": 10.4, "power_samples": 3, "efficiency": 0.87, "efficiency_samples": 2}}),
        State("switch.bil_monthly_summary_on_the_phone", "on", {"sent_for": "2026-09", "saved": {"2026-09": 20.5}}),
        State("select.bil_price_resolution", "hour"),
    ])
    entry, _ = await setup(hass, request)
    planner = entry.runtime_data
    assert planner.settings["charge_power_kw"] == 10.4 and planner.settings["efficiency"] == 0.87
    assert planner.routines.summary_sent == "2026-09" and planner.routines.saved == {"2026-09": 20.5}
    assert planner.resolution == "hour"
