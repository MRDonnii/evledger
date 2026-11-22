"""Chaos: every input EV Ledger reads – the car's battery, odometer, range, position, charge limit and charging sensor,
the charger's mode, switch, power and session energy, and the price sensor with its list – is set again and again to
random values, good ones and broken ones (unknown, unavailable, text, NaN, infinity, out of range, gone), while the
clock runs and the user presses buttons, picks plans and sets numbers. Nothing may ever log an error, show NaN or
infinity on an entity, or leave an entity of EV Ledger missing.

EVLEDGER_CHAOS_RUNS sets how many random runs (each its own seed) are made; EVLEDGER_CHAOS_FIRST the first seed."""

import logging
import math
import os
import random
from datetime import timedelta

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .test_bad_values import broken_numbers
from .test_routines import phones, with_options
from .test_smart_charge import MODE, SWITCH, car_with_limit, setup

RUNS = int(os.environ.get("EVLEDGER_CHAOS_RUNS", "16"))
FIRST = int(os.environ.get("EVLEDGER_CHAOS_FIRST", "1"))
STEPS = 70

GARBAGE = ["unknown", "unavailable", "", "nan", "inf", "-inf", "abc", "1e400", "None", "-1", "100000"]
NUMBER_LIKE = [lambda r: f"{r.uniform(0, 100):.1f}", lambda r: str(r.randint(0, 100)), lambda r: r.choice(GARBAGE)]


def errors(caplog) -> list[str]:
    return [f"{record.name}: {record.getMessage()}"[:400] for record in caplog.records
            if record.levelno >= logging.ERROR]


def price_list(r: random.Random) -> list:
    start = dt_util.now().replace(minute=0, second=0, microsecond=0) + timedelta(hours=r.randint(-30, 5))
    shape = r.random()
    items = []
    for hour in range(r.randint(0, 48)):
        begin = start + timedelta(hours=hour)
        price = r.choice([r.uniform(-0.5, 5), r.uniform(0, 3), 1.0]) if shape < 0.8 else r.choice(
            [r.uniform(-1, 6), "nan", "inf", None, "x", float("nan"), float("inf"), True, [1], {"a": 1}])
        item = {"start": begin.isoformat(), "end": (begin + timedelta(hours=1)).isoformat(), "price": price}
        if r.random() < 0.05:
            item = r.choice([None, 1, "x", {"start": "garbage", "price": 1}, {"price": 1},
                             {"start": begin.isoformat(), "end": "2100-01-01T00:00:00+00:00", "price": 1},
                             {"start": begin.replace(tzinfo=None).isoformat(), "price": 1}])
        items.append(item)
    return items


def chaos_step(hass: HomeAssistant, r: random.Random) -> None:
    pick = r.random()
    if pick < 0.15:
        hass.states.async_set("sensor.car_battery", r.choice(NUMBER_LIKE)(r), {"unit_of_measurement": "%"})
    elif pick < 0.32:
        mode = r.choice(["disconnected", "connected_requesting", "connected_charging", "connected_finished",
                         "unknown", "unavailable", "weird"])
        hass.states.async_set(MODE, mode)
        if r.random() < 0.7:
            hass.states.async_set(SWITCH, "on" if mode == "connected_charging" else r.choice(["off", "on", "unknown"]))
    elif pick < 0.42:
        hass.states.async_set("sensor.charger_power", r.choice(
            [str(r.randint(0, 11500)), "0", r.choice(GARBAGE)]), {"unit_of_measurement": r.choice(["W", "kW", "W"])})
        hass.states.async_set("sensor.charger_session", r.choice(NUMBER_LIKE)(r), {"unit_of_measurement": "kWh"})
    elif pick < 0.52:
        prices = price_list(r)
        hass.states.async_set("sensor.price", r.choice(["1.0", "2.3", *GARBAGE]),
                              {"prices": prices if r.random() < 0.9 else r.choice([None, "x", 5, {"a": 1}]),
                               "unit_of_measurement": "kr/kWh"})
    elif pick < 0.58:
        hass.states.async_set("number.car_charge_limit", r.choice(["80", "68", "100", "50", *GARBAGE]),
                              {"unit_of_measurement": "%", "min": r.choice([50, "x", None]), "max": 100})
    elif pick < 0.63:
        value = r.choice(["home", "not_home", "unknown"])
        hass.states.async_set("device_tracker.car", value, r.choice([
            {"latitude": 56.0, "longitude": 10.0}, {"latitude": "nan", "longitude": None}, {}]))
    elif pick < 0.67:
        hass.states.async_set("binary_sensor.car_charging", r.choice(["on", "off", "unknown", "unavailable"]))
    elif pick < 0.70:
        hass.states.async_set("sensor.car_odometer", r.choice([str(1000 + r.randint(0, 50)), *GARBAGE]),
                              {"unit_of_measurement": "km"})
    elif pick < 0.72:
        entity_id = r.choice(["sensor.car_battery", "sensor.price", "sensor.charger_power", MODE,
                              "number.car_charge_limit"])
        hass.states.async_remove(entity_id)  # gone, as when an integration is removed or reloads


async def user_step(hass: HomeAssistant, r: random.Random, prefix: str) -> None:
    pick = r.random()
    try:
        if pick < 0.3:
            options = hass.states.get("select.bil_charge_mode").attributes["options"]
            await hass.services.async_call("select", "select_option", {
                "entity_id": "select.bil_charge_mode", "option": r.choice(options)}, blocking=True)
        elif pick < 0.5:
            numbers = [s for s in hass.states.async_all("number") if s.entity_id.startswith("number.bil_")]
            number = r.choice(numbers)
            low, high = float(number.attributes["min"]), float(number.attributes["max"])
            await hass.services.async_call("number", "set_value", {
                "entity_id": number.entity_id, "value": round(r.uniform(low, high), 1)}, blocking=True)
        elif pick < 0.7:
            switches = [s.entity_id for s in hass.states.async_all("switch") if s.entity_id.startswith("switch.bil_")]
            await hass.services.async_call("switch", r.choice(["turn_on", "turn_off"]),
                                           {"entity_id": r.choice(switches)}, blocking=True)
        elif pick < 0.8:
            buttons = [s.entity_id for s in hass.states.async_all("button") if s.entity_id.startswith("button.bil_")]
            await hass.services.async_call("button", "press", {"entity_id": r.choice(buttons)}, blocking=True)
        elif pick < 0.9:
            hass.bus.async_fire("mobile_app_notification_action", {
                "action": r.choice([f"{prefix}{a}" for a in ("CONFIRM", "WAIT", "FULL", "CAP_OK", "CAP_STOP", "NOW",
                                                                "PRE_ON", "PUBLIC_x")]
                                   + ["x", "", None]), "reply_text": r.choice(["", "23,4 82,50", "x", None])})
    except (HomeAssistantError, vol.Invalid, ValueError) as err:
        # A choice the entity turns away (e.g. a plan that needs a charger) is fine; it must not be an error log.
        logging.getLogger(__name__).debug("turned away: %s", err)


@pytest.mark.parametrize("seed", range(FIRST, FIRST + RUNS))
async def test_chaos(hass: HomeAssistant, request, freezer, caplog, seed):
    r = random.Random(seed)
    phones(hass)
    car_with_limit(hass, 80)
    entry, calls = await setup(hass, request, charger_state=r.choice(["disconnected", "connected_finished"]),
                               cheap_now=r.random() < 0.5, soc=str(r.randint(10, 90)))
    planner = await with_options(hass, entry)
    ours = {item.entity_id for item in hass.states.async_all() if item.entity_id.split(".", 1)[1].startswith("bil_")}
    for step in range(STEPS):
        for _ in range(r.randint(1, 4)):
            chaos_step(hass, r)
        if r.random() < 0.35:
            await user_step(hass, r, planner.notify.prefix)
        freezer.tick(timedelta(seconds=r.choice([5, 30, 60, 60, 120, 600, 1800])))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
        found = errors(caplog)
        assert not found, f"seed {seed} step {step}: {found}"
        assert not broken_numbers(hass), f"seed {seed} step {step}"
    missing = [entity_id for entity_id in ours if hass.states.get(entity_id) is None]
    assert not missing
    for entity_id in ours:
        value = hass.states.get(entity_id).state
        assert value.lower() not in ("nan", "inf", "-inf"), entity_id
    assert all(isinstance(e, str) for e in calls.get("switch.turn_on", []))
    assert all(math.isfinite(value) for value in planner.settings.values() if isinstance(value, float))
