"""Services of other integrations that fail – a phone that cannot be reached, a car that does not wake for a refresh,
a calendar that times out – are logged as warnings. They never stop the plan, never send a message twice and never
leave an error in the log."""

import asyncio
import logging
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .test_car_refresh import fleet_twin
from .test_routines import set_time, with_options
from .test_smart_charge import MODE, SWITCH, later, setup

FAILURES = [HomeAssistantError("no phone"), RuntimeError("push service down"), TimeoutError(), KeyError("token"),
            asyncio.CancelledError()]


def errors(caplog) -> list[str]:
    return [f"{record.name}: {record.getMessage()}" for record in caplog.records if record.levelno >= logging.ERROR]


def failing(hass: HomeAssistant, domain: str, service: str, error: BaseException) -> list[dict]:
    tried: list[dict] = []

    async def fail(call):
        tried.append(dict(call.data))
        raise error

    hass.services.async_register(domain, service, fail)
    return tried


@pytest.mark.parametrize("error", FAILURES[:4], ids=lambda e: type(e).__name__)
async def test_a_phone_that_fails(hass: HomeAssistant, request, freezer, caplog, error):
    tried = failing(hass, "notify", "mobile_app_a", error)
    entry, calls = await setup(hass, request, charger_state="connected_finished", cheap_now=True)
    await with_options(hass, entry)
    hass.states.async_set(MODE, "connected_charging")
    await hass.async_block_till_done(wait_background_tasks=True)
    for _ in range(5):
        await later(hass, freezer, 3)
    started = [m for m in tried if "ladning startet" in m.get("title", "")]
    assert len(started) == 1, "a failed message is not sent again and again"
    assert not errors(caplog)
    assert hass.states.get("sensor.bil_charge_status").state not in ("unknown", "unavailable")


@pytest.mark.parametrize("error", FAILURES[:4], ids=lambda e: type(e).__name__)
async def test_an_evening_reminder_to_a_phone_that_fails(hass: HomeAssistant, request, freezer, caplog, error):
    tried = failing(hass, "notify", "mobile_app_a", error)
    entry, _ = await setup(hass, request, charger_state="disconnected", cheap_now=False, soc="30")
    await with_options(hass, entry)
    now = dt_util.now()
    await set_time(hass, "time.bil_evening_check_at", (now + timedelta(minutes=2)).time().replace(second=0))
    for _ in range(10):
        await later(hass, freezer, 3)
    assert len([m for m in tried if m.get("title") == "Bil: sæt bilen til"]) == 1
    assert not errors(caplog)


@pytest.mark.parametrize("error", FAILURES[:4], ids=lambda e: type(e).__name__)
async def test_a_car_refresh_that_fails(hass: HomeAssistant, request, freezer, caplog, error):
    """The car without data after a restart (asked for an update every few minutes) and at the plug-in."""
    fleet_twin(hass)
    tried = failing(hass, "homeassistant", "update_entity", error)
    _, calls = await setup(hass, request, charger_state="disconnected", cheap_now=True)
    for _ in range(3):
        await later(hass, freezer, 3)
    hass.states.async_set(MODE, "connected_requesting")
    await hass.async_block_till_done(wait_background_tasks=True)
    for _ in range(3):
        await later(hass, freezer, 3)
    assert len(tried) >= 3, "the car was asked for fresh data"
    assert not errors(caplog)
    assert SWITCH in calls["switch.turn_on"] or calls["button.press"], "the plan still starts the charge"


CALENDAR_ANSWERS = [
    HomeAssistantError("calendar gone"), RuntimeError("caldav down"), TimeoutError(),
    None, {}, {"calendar.familie": None}, {"calendar.familie": []}, {"calendar.familie": {"events": "x"}},
    {"calendar.familie": {"events": [None, 1, "x", {"start": 5}, {"start": "garbage", "summary": None},
                                     {"start": "2026-10-10T10:00:00", "summary": "Bil uden zone"},
                                     {"start": {"dateTime": "x"}, "summary": "bil"}]}},
]


@pytest.mark.parametrize("answer", CALENDAR_ANSWERS, ids=lambda a: type(a).__name__ + str(len(str(a))))
async def test_a_calendar_that_fails_or_answers_nonsense(hass: HomeAssistant, request, freezer, caplog, answer):
    from homeassistant.core import SupportsResponse

    asked = []

    async def get_events(call):
        asked.append(call.data["entity_id"])
        if isinstance(answer, BaseException):
            raise answer
        return answer

    hass.services.async_register("calendar", "get_events", get_events, supports_response=SupportsResponse.ONLY)
    entry, _ = await setup(hass, request, charger=False, cheap_now=False)
    planner = await with_options(hass, entry, trip_calendar="calendar.familie", trip_calendar_keyword="bil")
    for _ in range(3):
        await later(hass, freezer, 16)
    assert len(asked) >= 2, "asked again later"
    assert not errors(caplog)
    assert planner.trip.departure is None


@pytest.mark.parametrize("error", FAILURES[:4], ids=lambda e: type(e).__name__)
async def test_a_car_climate_that_fails(hass: HomeAssistant, request, freezer, caplog, error):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .test_routines import phones, switch

    fleet = MockConfigEntry(domain="tesla_fleet")
    fleet.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(config_entry_id=fleet.entry_id, identifiers={("tesla_fleet", "1")})
    registry = er.async_get(hass)
    for domain, unique, object_id in (("sensor", "vin_battery", "car_battery"),
                                      ("climate", "vin_climate", "car_climate")):
        registry.async_get_or_create(domain, "tesla_fleet", unique, device_id=device.id, config_entry=fleet,
                                     suggested_object_id=object_id)
    hass.states.async_set("climate.car_climate", "off")
    tried = failing(hass, "climate", "turn_on", error)
    sent = phones(hass)
    entry, _ = await setup(hass, request, charger_state="connected_finished", cheap_now=False)
    await with_options(hass, entry)
    ready = (dt_util.now() + timedelta(minutes=30)).time().replace(second=0)
    await set_time(hass, "time.bil_ready_by", ready)
    await later(hass, freezer, 1)
    await switch(hass, "switch.bil_precondition_the_car_for_ready_by")
    await later(hass, freezer, 11)
    asked = [m for m in sent if "forvarm" in m.get("title", "")]
    assert len(asked) == 1
    hass.bus.async_fire("mobile_app_notification_action", {"action": asked[0]["data"]["actions"][0]["action"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 5)
    assert tried
    assert not errors(caplog)


@pytest.mark.parametrize("reply", ["9" * 400 + " 50", "0 82,50", "23,4 " + "9" * 400, "ingen pris", "", "-5",
                                   "500 82", "23,4 82,50 kr og 12 andre ting"])
async def test_a_public_price_answer_that_makes_no_sense(hass: HomeAssistant, request, caplog, reply):
    from custom_components.evledger.models import ChargeSession

    from .test_routines import phones
    from .test_smart_charge import DOMAIN

    sent = phones(hass)
    entry, _ = await setup(hass, request)
    planner = await with_options(hass, entry)
    store = hass.data[DOMAIN][entry.entry_id].store
    now = dt_util.now()
    await store.async_upsert_charge(ChargeSession(
        id="pub1", location_kind="public", provider="vehicle", started_at=(now - timedelta(hours=1)).isoformat(),
        ended_at=now.isoformat(), kwh=None, price=None, price_currency="DKK", location_name=None,
        start_battery_pct=40.0, end_battery_pct=80.0, needs_review=True))
    planner.routines.answer_public("pub1", reply)
    await hass.async_block_till_done(wait_background_tasks=True)
    saved = store.get_charge("pub1")
    assert not errors(caplog)
    if reply.startswith("23,4 82,50"):
        assert (saved.kwh, saved.price) == (23.4, 82.5)
    else:
        assert saved.needs_review and saved.kwh is None, "nonsense is not saved"
        assert any("kunne ikke læses" in m["message"] for m in sent)
