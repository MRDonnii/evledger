"""The optional smarter plans: waiting for a cheaper day, learned departure times, green power, the price of a public
charge asked on the phone, and a message when the target was not reached. All off until switched on."""

from datetime import datetime, time, timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.evledger.models import ChargeSession, Trip
from custom_components.evledger.smart import co2 as grid_co2
from custom_components.evledger.smart import departures
from custom_components.evledger.smart.plan import (
    MODE_SMART,
    Constraint,
    ScheduleInput,
    TimelineSlot,
    build_schedule,
)

from .test_routines import phones, switch, with_options
from .test_smart_charge import DOMAIN, setup


def hourly_prices(hass: HomeAssistant, cheap: set[int], days: int = 4, price: float = 3.0,
                  low: float = 0.5) -> datetime:
    """Hourly prices from the current hour for some days; the hours in cheap (counted from now) cost low."""
    hour = dt_util.now().replace(minute=0, second=0, microsecond=0)
    items = [{"start": (hour + timedelta(hours=h)).isoformat(), "end": (hour + timedelta(hours=h + 1)).isoformat(),
              "price": low if h in cheap else price} for h in range(-1, 24 * days)]
    hass.states.async_set("sensor.price", str(price), {"prices": items, "unit_of_measurement": "kr/kWh"})
    return hour


async def add_trips(hass: HomeAssistant, entry, trips: list[Trip]) -> None:
    store = hass.data[DOMAIN][entry.entry_id].store
    for item in trips:
        await store.async_upsert_trip(item)


def trip(start: datetime, km: float, home: tuple[float, float]) -> Trip:
    return Trip(id=f"t{start.timestamp()}", started_at=start.isoformat(),
                ended_at=(start + timedelta(minutes=30)).isoformat(), start_odometer_km=1000.0,
                end_odometer_km=1000.0 + km, distance_km=km, start_lat=home[0], start_lon=home[1], end_lat=None,
                end_lon=None, start_battery_pct=None, end_battery_pct=None)


def daily_driving(hass: HomeAssistant, days: int = 10, km: float = 20.0) -> list[Trip]:
    home = (hass.config.latitude, hass.config.longitude)
    now = dt_util.now()
    return [trip(now - timedelta(days=day, hours=4), km, home) for day in range(1, days + 1)]


# -- wait for a cheaper day ------------------------------------------------------------------

async def test_waits_for_a_clearly_cheaper_night(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="70")
    planner = entry.runtime_data
    # Noon now: the night before the day after tomorrow (two days on, 02:00-04:00) is cheap, everything else dear.
    hourly_prices(hass, cheap={38, 39})
    await add_trips(hass, entry, daily_driving(hass))
    planner.async_set_setting("target_soc", 80.0)
    await hass.async_block_till_done()
    assert planner.waiting is None, "off by default"
    tonight = planner.schedule.blocks[0].start
    assert tonight < planner.deadline

    await switch(hass, "switch.bil_wait_for_a_cheaper_day")
    waiting = planner.waiting
    assert waiting is not None and waiting["deadline"] > planner.deadline
    assert waiting["price"] == pytest.approx(0.5, rel=0.01) and waiting["price_now"] == pytest.approx(3.0, rel=0.01)
    assert planner.schedule.blocks[0].start >= planner.deadline, "nothing tonight"
    assert hass.states.get("sensor.bil_next_charge_start").attributes["waiting_for"]["saving"] > 2
    attrs = hass.states.get("switch.bil_wait_for_a_cheaper_day").attributes
    assert attrs["daily_use_kwh"] == pytest.approx(20 * 180 / 1000, rel=0.05)
    assert "Venter til" in planner.notify.plan_text(planner)


async def test_does_not_wait_when_the_battery_would_run_low(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="35")
    planner = entry.runtime_data
    hourly_prices(hass, cheap={38, 39})
    await add_trips(hass, entry, daily_driving(hass, km=60.0))  # about 18 % a day
    await switch(hass, "switch.bil_wait_for_a_cheaper_day")
    assert planner.waiting is None
    assert planner.schedule.blocks[0].start < planner.deadline


async def test_a_running_charge_is_not_stopped_to_wait(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger_state="connected_charging", cheap_now=False, soc="70")
    planner = entry.runtime_data
    hourly_prices(hass, cheap={38, 39})
    await add_trips(hass, entry, daily_driving(hass))
    await switch(hass, "switch.bil_wait_for_a_cheaper_day")
    assert planner.waiting is None


async def test_does_not_wait_without_driving_history(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="70")
    planner = entry.runtime_data
    hourly_prices(hass, cheap={38, 39})
    await switch(hass, "switch.bil_wait_for_a_cheaper_day")
    assert planner.waiting is None, "no idea how much the car drives"


# -- learned departure times -----------------------------------------------------------------

def weekday_departures(hass: HomeAssistant, at: time, weeks: int = 4) -> list[Trip]:
    home = (hass.config.latitude, hass.config.longitude)
    today = dt_util.now().date()
    result = []
    for offset in range(1, weeks * 7 + 1):
        day = today - timedelta(days=offset)
        if day.weekday() < 5:
            start = datetime.combine(day, at, tzinfo=dt_util.get_default_time_zone())
            result.append(trip(start, 15.0, home))
    return result


def test_learning_departures():
    home = (56.0, 10.0)
    now = datetime(2026, 10, 10, 12, 0, tzinfo=dt_util.get_default_time_zone())
    trips = []
    for offset in range(1, 36):
        day = (now - timedelta(days=offset)).date()
        if day.weekday() < 5:
            for moment, where in ((time(7, 30), home), (time(16, 0), (56.2, 10.2))):
                trips.append(trip(datetime.combine(day, moment, tzinfo=now.tzinfo), 15.0, where))
    learned = departures.learn(trips, home, now)
    assert learned["times"][0] == time(7, 15), "an early one of the usual departures, a quarter before"
    assert learned["times"][5] is None and learned["times"][6] is None, "no regular weekend departures"
    assert departures.as_text(learned["times"])["lør"] is None
    assert departures.learn(trips[:6], home, now)["times"] is None, "too short a history"
    nxt = departures.deadlines(now, learned["times"], 2)  # Saturday noon: Monday and Tuesday
    assert [d.weekday() for d in nxt] == [0, 1] and nxt[0].time() == time(7, 15)


async def test_learned_departures_set_the_ready_by_time(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="50")
    planner = entry.runtime_data
    await add_trips(hass, entry, weekday_departures(hass, time(7, 30)))
    regular = planner.deadline
    await switch(hass, "switch.bil_learn_departure_times")
    expected = next(d for d in (dt_util.now() + timedelta(days=n) for n in range(1, 8)) if d.weekday() < 5)
    assert planner.deadline == datetime.combine(expected.date(), time(7, 15), tzinfo=regular.tzinfo)
    attrs = hass.states.get("switch.bil_learn_departure_times").attributes
    assert attrs["departures"]["man"] == "07:15" and attrs["departures"]["søn"] is None
    if planner.deadline.date() > regular.date():
        # A weekend without departures: the usual ready-by time keeps the minimum level.
        assert any(c.deadline == regular and c.target_soc == planner.settings["min_soc"]
                   for c in planner.constraints(dt_util.now()))
    await switch(hass, "switch.bil_learn_departure_times", on=False)
    assert planner.deadline == regular


# -- prefer green power ----------------------------------------------------------------------

def slots(now: datetime, prices: list[float], co2: list[float | None]) -> list[TimelineSlot]:
    return [TimelineSlot(now + timedelta(hours=h), now + timedelta(hours=h + 1), price, False, gram)
            for h, (price, gram) in enumerate(zip(prices, co2, strict=True))]


def test_green_takes_the_cleaner_of_nearly_equal_windows():
    now = datetime(2026, 10, 10, 20, 0, tzinfo=dt_util.get_default_time_zone())
    # Two one-hour windows: 1.00 kr with 300 g/kWh, and 1.02 kr (2 % more) with 60 g/kWh.
    timeline = slots(now, [3.0, 1.02, 3.0, 1.0, 3.0], [200.0, 60.0, 200.0, 300.0, 200.0])
    data = dict(mode=MODE_SMART, soc=50.0, target_soc=60.0, capacity_kwh=60.0, efficiency=1.0, power_kw=6.0,
                price_factor=1.0, timeline=timeline, constraints=(Constraint(now + timedelta(hours=5), 60.0),))
    plain = build_schedule(ScheduleInput(**data), now)
    green = build_schedule(ScheduleInput(**data, green=True), now)
    assert plain.blocks[0].start == now + timedelta(hours=3) and plain.co2 == 300
    assert green.blocks[0].start == now + timedelta(hours=1) and green.co2 == 60
    # Clearly dearer is not worth it.
    dear = slots(now, [3.0, 1.2, 3.0, 1.0, 3.0], [200.0, 60.0, 200.0, 300.0, 200.0])
    assert build_schedule(ScheduleInput(**{**data, "timeline": dear}, green=True), now).blocks[0].start == \
        now + timedelta(hours=3)


def test_price_area():
    assert grid_co2.price_area(56.43, 9.94) == "DK1"  # Randers
    assert grid_co2.price_area(55.68, 12.57) == "DK2"  # Copenhagen
    assert grid_co2.price_area(55.1, 14.9) == "DK2"  # Bornholm
    assert grid_co2.price_area(52.5, 13.4) is None


async def test_green_plan_with_the_co2_forecast(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False, soc="70")
    planner = entry.runtime_data
    hour = dt_util.now().replace(minute=0, second=0, microsecond=0)
    planner.co2 = {(hour + timedelta(minutes=15 * q)).astimezone(dt_util.UTC): 100.0 for q in range(0, 30 * 4)}
    planner.co2_updated = dt_util.utcnow()
    await switch(hass, "switch.bil_prefer_green_power")
    assert planner.schedule.co2 == 100
    assert hass.states.get("sensor.bil_next_charge_start").attributes["co2"] == 100
    assert "CO₂: ca. 100 g/kWh" in planner.notify.plan_text(planner)


# -- the price of a public charge ------------------------------------------------------------

async def test_public_charge_price_from_the_phone(hass: HomeAssistant, request):
    sent = phones(hass)
    entry, _ = await setup(hass, request)
    planner = await with_options(hass, entry)
    store = hass.data[DOMAIN][entry.entry_id].store
    now = dt_util.now()
    charge = ChargeSession(id="pub1", location_kind="public", provider="vehicle",
                           started_at=(now - timedelta(hours=1)).isoformat(), ended_at=now.isoformat(), kwh=None,
                           price=None, price_currency="DKK", location_name=None, start_battery_pct=40.0,
                           end_battery_pct=80.0, needs_review=True)
    await store.async_upsert_charge(charge)
    planner.routines.ask_public_price(charge)
    await hass.async_block_till_done()
    assert not [m for m in sent if "offentlig" in m["title"]], "off by default"

    await switch(hass, "switch.bil_ask_for_the_price_of_public_charges")
    planner.routines.ask_public_price(charge)
    await hass.async_block_till_done()
    asked = [m for m in sent if m["title"] == "Bil: offentlig ladning"]
    assert len(asked) == 1 and "40 → 80 %" in asked[0]["message"] and "ca. 25,3 kWh" in asked[0]["message"]
    action = asked[0]["data"]["actions"][0]
    assert action["behavior"] == "textInput" and action["action"].endswith("PUBLIC_pub1")

    hass.bus.async_fire("mobile_app_notification_action", {"action": action["action"], "reply_text": "23,4 82,50"})
    await hass.async_block_till_done(wait_background_tasks=True)
    saved = store.get_charge("pub1")
    assert (saved.kwh, saved.price, saved.needs_review) == (23.4, 82.5, False)
    assert any("Gemt: 23,4 kWh for 82,50 kr" in m["message"] for m in sent)


# -- the target was not reached --------------------------------------------------------------

async def test_message_when_the_target_was_not_reached(hass: HomeAssistant, request):
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", soc="70")
    planner = await with_options(hass, entry)
    now = dt_util.now()
    planner.passed_deadline, planner.passed_target = now - timedelta(minutes=5), 100.0
    planner.routines.morning_check(now)
    await hass.async_block_till_done()
    assert not [m for m in sent if "målet" in m["title"]], "off by default"
    await switch(hass, "switch.bil_message_if_the_target_is_not_reached")
    planner.passed_deadline, planner.passed_target = now - timedelta(minutes=5), 100.0
    planner.routines.morning_check(now)
    planner.routines.morning_check(now)
    await hass.async_block_till_done()
    missed = [m for m in sent if m["title"] == "Bil: målet blev ikke nået"]
    assert len(missed) == 1 and missed[0]["message"].startswith("Bilen nåede ikke målet: 70 % af 100 %")


async def test_the_passed_ready_by_time_is_noted(hass: HomeAssistant, request, freezer):
    from .test_smart_charge import later
    entry, _ = await setup(hass, request, charger_state="connected_finished", soc="70")
    planner = entry.runtime_data
    deadline = planner.deadline
    freezer.move_to(deadline + timedelta(minutes=1))
    await later(hass, freezer, 1)
    assert planner.passed_deadline == deadline and planner.passed_target == planner.target
