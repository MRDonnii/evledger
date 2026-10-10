"""The everyday helpers: the message when a charge is done, the evening check, the weekend ready-by time,
preconditioning and trips from a calendar."""

from datetime import datetime, time, timedelta

from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.evledger.models import ChargeSession

from .test_smart_charge import DOMAIN, MODE, later, setup, state


def phones(hass: HomeAssistant) -> list[dict]:
    sent: list[dict] = []

    async def fake_notify(call):
        sent.append(call.data)

    hass.services.async_register("notify", "mobile_app_a", fake_notify)
    return sent


async def with_options(hass: HomeAssistant, entry, **options):
    """Change the smart charging settings (the entry reloads) and return the new planner."""
    hass.config_entries.async_update_entry(entry, data={**entry.data, "smart_charge": {
        **entry.data["smart_charge"], "notify_services": ["mobile_app_a"], **options}})
    await hass.async_block_till_done(wait_background_tasks=True)
    return hass.data[DOMAIN][entry.entry_id].smart


def session(kwh: float, price: float | None, start: datetime, minutes: int, soc: tuple[float, float]) -> ChargeSession:
    return ChargeSession(id=str(start.timestamp()), location_kind="home", provider="zaptec",
                         started_at=start.isoformat(), ended_at=(start + timedelta(minutes=minutes)).isoformat(),
                         kwh=kwh, price=price, price_currency="DKK", location_name="Home",
                         start_battery_pct=soc[0], end_battery_pct=soc[1])


async def set_time(hass: HomeAssistant, entity_id: str, value: time) -> None:
    await hass.services.async_call("time", "set_value", {"entity_id": entity_id, "time": value}, blocking=True)


async def switch(hass: HomeAssistant, entity_id: str, on: bool = True) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True)


async def test_done_message_sums_the_periods(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_charging", cheap_now=True, soc="60")
    planner = await with_options(hass, entry)
    start = dt_util.now() - timedelta(hours=3)
    planner.routines.charge_finished(session(6.0, 2.5, start, 60, (60, 70)))
    await later(hass, freezer, 1)
    assert not [m for m in sent if "færdig" in m["title"]], "the plan is not done after the first period"
    planner.routines.charge_finished(session(4.0, 1.5, start + timedelta(hours=2), 45, (70, 80)))
    hass.states.async_set("sensor.car_battery", "80")
    hass.states.async_set(MODE, "connected_finished")
    await later(hass, freezer, 1)
    done = [m for m in sent if m["title"] == "Bil: opladning færdig"]
    assert len(done) == 1, sent
    assert done[0]["message"].startswith("10,0 kWh for 4,00 kr (0,40 kr/kWh)\nBatteri 60 % → 80 %")
    assert "(2 perioder)" in done[0]["message"]
    await later(hass, freezer, 5)
    assert len([m for m in sent if m["title"] == "Bil: opladning færdig"]) == 1, "sent once"


async def test_done_message_waits_for_the_ledger_and_can_be_turned_off(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=True, soc="80")
    planner = await with_options(hass, entry)
    open_session = [True]
    planner.open_charge = lambda: open_session[0]
    planner.routines.charge_finished(session(5.0, None, dt_util.now() - timedelta(hours=1), 50, (70, 80)))
    await later(hass, freezer, 1)
    assert not sent, "a session still open in the ledger: its kWh and price are not known yet"
    open_session[0] = False
    await later(hass, freezer, 1)
    assert sent[-1]["message"].startswith("5,0 kWh (prisen kendes ikke endnu)")
    await switch(hass, "switch.bil_message_when_charging_is_done", False)
    planner.routines.charge_finished(session(1.0, 0.4, dt_util.now() - timedelta(minutes=30), 20, (79, 80)))
    await later(hass, freezer, 1)
    assert len(sent) == 1, "turned off"


async def test_evening_reminder_when_home_unplugged_and_low(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="disconnected", cheap_now=False, soc="30")
    await with_options(hass, entry)
    now = dt_util.now()
    await set_time(hass, "time.bil_evening_check_at", (now + timedelta(minutes=2)).time().replace(second=0))
    await later(hass, freezer, 1)
    assert not sent, "not before the time"
    await later(hass, freezer, 2)
    reminders = [m for m in sent if m["title"] == "Bil: sæt bilen til"]
    assert len(reminders) == 1
    assert reminders[0]["message"].startswith("Bilen er ikke sat til, og batteriet er på 30 %.")
    assert reminders[0]["data"]["tag"].endswith("_reminder")
    await later(hass, freezer, 30)
    assert len([m for m in sent if m["title"] == "Bil: sæt bilen til"]) == 1, "once a day"


async def test_no_reminder_when_plugged_in_away_or_charged(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="30")
    planner = await with_options(hass, entry)
    await set_time(hass, "time.bil_evening_check_at", dt_util.now().time().replace(second=0))
    await later(hass, freezer, 1)
    assert not [m for m in sent if "sæt bilen til" in m["title"]], "plugged in"
    assert planner.routines.evening_checked == dt_util.now().date()
    hass.states.async_set(MODE, "disconnected")
    hass.states.async_set("device_tracker.car", "not_home")
    assert planner.routines.reminder_text(dt_util.now()) is None, "away from home"
    hass.states.async_set("device_tracker.car", "home")
    hass.states.async_set("sensor.car_battery", "70")
    await later(hass, freezer, 3)
    assert planner.routines.reminder_text(dt_util.now()) is None, "battery above the level"
    hass.states.async_set("sensor.car_battery", "30")
    await hass.async_block_till_done()
    assert planner.routines.reminder_text(dt_util.now()), "low and unplugged at home"


async def test_charger_offline_in_the_evening_and_when_a_charge_is_due(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=True, soc="50")
    await with_options(hass, entry)
    hass.states.async_set(MODE, "unavailable")
    await set_time(hass, "time.bil_evening_check_at", dt_util.now().time().replace(second=0))
    await later(hass, freezer, 1)
    assert [m for m in sent if m["title"] == "Bil: laderen er offline"], "evening warning"
    await later(hass, freezer, 11)
    assert any("skulle være i gang" in m["message"] for m in sent), "a charge is due: warned at once"
    count = len(sent)
    await later(hass, freezer, 11)
    assert len(sent) == count, "warned once"


async def test_weekend_ready_by(hass: HomeAssistant, request, freezer):
    freezer.move_to(datetime(2026, 10, 9, 22, 0, tzinfo=dt_util.get_default_time_zone()))  # a Friday
    entry, _ = await setup(hass, request, charger=False, cheap_now=False)
    planner = entry.runtime_data
    assert planner.deadline.date().isoformat() == "2026-10-10" and planner.deadline.hour == 7
    await set_time(hass, "time.bil_ready_by_at_the_weekend", time(10, 30))
    assert planner.deadline.hour == 7, "only with the switch on"
    await switch(hass, "switch.bil_another_time_at_the_weekend")
    assert (planner.deadline.hour, planner.deadline.minute) == (10, 30)
    assert hass.states.get("sensor.bil_next_charge_start").attributes["deadline"] == planner.deadline


async def test_precondition_asks_first_and_only_this_car(hass: HomeAssistant, request, freezer):
    """The climate is only turned on after "Forvarm" on the phone; the command-capable integration of the same car wins,
    another car's climate is never used."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    custom = MockConfigEntry(domain="tesla_custom")
    custom.add_to_hass(hass)
    car = devices.async_get_or_create(config_entry_id=custom.entry_id, identifiers={("tesla_custom", "vin")},
                                      name="Bil")
    registry.async_get_or_create("sensor", "tesla_custom", "vin_battery", device_id=car.id, config_entry=custom,
                                 suggested_object_id="car_battery")
    registry.async_get_or_create("climate", "tesla_custom", "vin_hvac", device_id=car.id, config_entry=custom,
                                 suggested_object_id="car_hvac")
    fleet = MockConfigEntry(domain="tesla_fleet")
    fleet.add_to_hass(hass)
    for ident, name, object_id in (("1", "Bil", "car_climate"), ("2", "Brother", "other_climate")):
        device = devices.async_get_or_create(config_entry_id=fleet.entry_id, identifiers={("tesla_fleet", ident)},
                                             name=name)
        registry.async_get_or_create("climate", "tesla_fleet", f"{ident}_climate", device_id=device.id,
                                     config_entry=fleet, suggested_object_id=object_id)
    hass.states.async_set("climate.car_climate", "off")
    climate: list[str] = []

    async def record(call):
        climate.append(f"{call.service} {call.data['entity_id']}")
        hass.states.async_set("climate.car_climate", "heat_cool" if call.service == "turn_on" else "off")

    hass.services.async_register("climate", "turn_on", record)
    hass.services.async_register("climate", "turn_off", record)
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False)
    await with_options(hass, entry)
    planner = hass.data[DOMAIN][entry.entry_id].smart
    assert hass.states.get("sensor.bil_charge_status").attributes["car_climate_entity"] == "climate.car_climate"
    ready = (dt_util.now() + timedelta(minutes=30)).time().replace(second=0)
    await set_time(hass, "time.bil_ready_by", ready)
    await later(hass, freezer, 1)
    await switch(hass, "switch.bil_precondition_the_car_for_ready_by")
    await later(hass, freezer, 5)
    assert not [m for m in sent if "forvarm" in m.get("title", "")], "not yet: 20 minutes before"
    await later(hass, freezer, 6)
    asked = [m for m in sent if "forvarm" in m.get("title", "")]
    assert len(asked) == 1
    assert not climate, "nothing without an answer"
    yes = asked[0]["data"]["actions"][0]["action"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": yes})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert climate == ["turn_on climate.car_climate"]
    await later(hass, freezer, 5)
    assert len([m for m in sent if "forvarm" in m.get("title", "")]) == 1, "asked once"
    await later(hass, freezer, 45)
    assert climate == ["turn_on climate.car_climate", "turn_off climate.car_climate"], "nobody left: off again"
    assert planner.routines.precondition_goal is None


async def test_precondition_without_answer_does_nothing(hass: HomeAssistant, request, freezer):
    fleet = MockConfigEntry(domain="tesla_fleet")
    fleet.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=fleet.entry_id, identifiers={("tesla_fleet", "1")})
    registry = er.async_get(hass)
    for domain, unique, object_id in (("sensor", "vin_battery", "car_battery"),
                                      ("climate", "vin_climate", "car_climate")):
        registry.async_get_or_create(domain, "tesla_fleet", unique, device_id=device.id, config_entry=fleet,
                                     suggested_object_id=object_id)
    climate: list[str] = []

    async def record(call):
        climate.append(call.service)

    hass.services.async_register("climate", "turn_on", record)
    phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False)
    await with_options(hass, entry)
    ready = (dt_util.now() + timedelta(minutes=30)).time().replace(second=0)
    await set_time(hass, "time.bil_ready_by", ready)
    await switch(hass, "switch.bil_precondition_the_car_for_ready_by")
    for _ in range(8):
        await later(hass, freezer, 10)
    assert not climate


async def test_trip_from_calendar(hass: HomeAssistant, request, freezer, aioclient_mock):
    aioclient_mock.get("https://router.project-osrm.org/route/v1/driving/10.0,56.0;10.5,56.5",
                       json={"code": "Ok", "routes": [{"distance": 100000, "duration": 3600}]})
    hass.config.latitude, hass.config.longitude = 56.0, 10.0
    start = (dt_util.now() + timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
    events = [
        {"start": (start - timedelta(hours=2)).isoformat(), "end": start.isoformat(), "summary": "Tandlæge",
         "location": "Tandlægen"},
        {"start": start.isoformat(), "end": (start + timedelta(hours=2)).isoformat(), "summary": "Bil: møde i Aarhus",
         "location": "56.5, 10.5"},
        {"start": start.date().isoformat(), "end": start.date().isoformat(), "summary": "Bil hele dagen"},
    ]
    asked = []

    async def get_events(call):
        asked.append(call.data["entity_id"])
        return {"calendar.familie": {"events": list(events)}}

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    entry, _ = await setup(hass, request, charger=False, cheap_now=False)
    planner = await with_options(hass, entry, trip_calendar="calendar.familie", trip_calendar_keyword="bil")
    await later(hass, freezer, 1)
    assert asked
    assert planner.trip.source == "calendar"
    assert planner.trip.destination == "56.5, 10.5"
    # 60 min drive + 10 min margin before the event's start.
    assert planner.trip.departure == start - timedelta(minutes=70)
    assert float(state(hass, "sensor.bil_trip_distance")) == 100
    attrs = hass.states.get("datetime.bil_temporary_departure").attributes
    assert attrs["source"] == "calendar"
    # Cleared by hand: not added again.
    await hass.services.async_call("button", "press", {"entity_id": "button.bil_clear_temporary_plan"},
                                   blocking=True)
    await later(hass, freezer, 16)
    assert planner.trip.departure is None
    # A trip set by hand is never replaced by the calendar.
    planner.routines.dismissed.clear()
    manual = dt_util.now() + timedelta(hours=3)
    await hass.services.async_call("datetime", "set_value", {"entity_id": "datetime.bil_temporary_departure",
                                                             "datetime": manual.replace(second=0, microsecond=0)},
                                   blocking=True)
    await later(hass, freezer, 16)
    assert planner.trip.source == "" and planner.trip.departure == manual.replace(second=0, microsecond=0)


async def test_calendar_trip_follows_the_event(hass: HomeAssistant, request, freezer):
    start = (dt_util.now() + timedelta(hours=8)).replace(minute=0, second=0, microsecond=0)
    events = [{"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat(), "summary": "Tur",
               "location": ""}]

    async def get_events(call):
        return {"calendar.familie": {"events": list(events)}}

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    entry, _ = await setup(hass, request, charger=False, cheap_now=False)
    planner = await with_options(hass, entry, trip_calendar="calendar.familie", trip_calendar_keyword="tur")
    await later(hass, freezer, 1)
    assert planner.trip.departure == start - timedelta(minutes=45), "no address: leaves 45 minutes before"
    events.clear()
    await later(hass, freezer, 16)
    assert planner.trip.departure is None, "the event is gone: so is its trip"


async def test_unplugged_mid_plan_sends_what_was_charged(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="60")
    planner = await with_options(hass, entry)
    planner.routines.charge_finished(session(3.0, 1.2, dt_util.now() - timedelta(hours=1), 20, (60, 65)))
    hass.states.async_set(MODE, "disconnected")  # a charger reboot looks like this for a moment
    await later(hass, freezer, 1)
    assert not sent, "not before the unplug grace"
    hass.states.async_set(MODE, "connected_finished")
    await later(hass, freezer, 3)
    assert not sent, "it was a reboot"
    hass.states.async_set(MODE, "disconnected")
    await later(hass, freezer, 3)
    assert [m["title"] for m in sent] == ["Bil: opladning slut"]
    assert sent[0]["message"].startswith("3,0 kWh for 1,20 kr")


async def test_charging_started_message(hass: HomeAssistant, request, freezer):
    sent = phones(hass)
    entry, calls = await setup(hass, request, charger_state="connected_finished", cheap_now=True)
    await with_options(hass, entry)
    hass.states.async_set(MODE, "connected_charging")
    await hass.async_block_till_done(wait_background_tasks=True)
    started = [m for m in sent if "ladning startet" in m.get("title", "")]
    assert len(started) == 1
    text = started[0]["message"]
    assert "Plan: Billigst" in text and "Slut ca." in text and "Forventet pris:" in text and "Mål 80 %" in text
    assert [a["title"] for a in started[0]["data"]["actions"]] == ["Pause"]
    # the car pauses for a moment and goes on: no second message
    hass.states.async_set(MODE, "connected_finished")
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 2)
    hass.states.async_set(MODE, "connected_charging")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len([m for m in sent if "ladning startet" in m.get("title", "")]) == 1


async def test_charging_started_is_quiet_at_night(hass: HomeAssistant, request, freezer):
    freezer.move_to(dt_util.now().replace(hour=1, minute=0, second=5))
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=True)
    await with_options(hass, entry)
    hass.states.async_set(MODE, "connected_charging")
    await hass.async_block_till_done(wait_background_tasks=True)
    started = [m for m in sent if "ladning startet" in m.get("title", "")]
    assert started and started[0]["data"]["push"] == {"interruption-level": "passive"}


async def test_plug_in_before_the_cheapest_start(hass: HomeAssistant, request, freezer):
    from .test_new_features import quarter_prices
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="disconnected", soc="30", cheap_now=False)
    hour = quarter_prices(hass, {q: 0.2 for q in range(8, 16)})  # cheap from two hours on
    planner = await with_options(hass, entry)
    planner.async_set_ready_by((hour + timedelta(hours=10)).time())
    await later(hass, freezer, 60)
    assert not [m for m in sent if "sæt bilen til" in m["title"]], "not yet"
    await later(hass, freezer, 35)
    soon = [m for m in sent if m["title"] == "Bil: sæt bilen til"]
    assert len(soon) == 1 and "Billigste ladning starter" in soon[0]["message"], sent
    await later(hass, freezer, 5)
    assert len([m for m in sent if m["title"] == "Bil: sæt bilen til"]) == 1, "once"


async def test_message_when_power_is_cheap(hass: HomeAssistant, request, freezer):
    from .test_new_features import quarter_prices
    freezer.move_to(dt_util.now().replace(hour=11, minute=0, second=5))
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="disconnected", soc="40", cheap_now=False)
    quarter_prices(hass, {}, default=2.0)
    await with_options(hass, entry)
    await switch(hass, "switch.bil_message_when_power_is_cheap")
    await later(hass, freezer, 1)
    assert not [m for m in sent if "billig" in m["title"]]
    quarter_prices(hass, {}, default=0.6)
    await later(hass, freezer, 1)
    cheap = [m for m in sent if m["title"] == "Bil: strømmen er billig"]
    assert len(cheap) == 1 and "0,60 kr/kWh (under 1,00)." in cheap[0]["message"], cheap
    assert cheap[0]["data"]["actions"][0]["title"] == "Lad nu"
