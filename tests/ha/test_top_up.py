"""Sparing an NMC battery: the switch, its numbers, the battery chemistry and what the plan sensor shows
(Claude AI, 2026-11-05)."""

from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import mock_restore_cache

from .test_new_features import quarter_prices
from .test_smart_charge import DOMAIN, setup, state

SWITCH = "switch.bil_charge_the_last_part_just_before_departure_nmc"
PLAN = "sensor.bil_next_charge_start"


def evening() -> datetime:
    return datetime(2026, 10, 12, 21, 0, tzinfo=dt_util.get_default_time_zone())


async def car(hass: HomeAssistant, request, freezer, model: str | None):
    """Monday 21:00, the car at 50 % wants 100 % by 07:00. Cheap 00-04, a little dearer 04-06:30, dear otherwise."""
    freezer.move_to(evening())
    entry, _ = await setup(hass, request, soc="50", cheap_now=False)
    if model:
        hass.config_entries.async_update_entry(entry, data={**entry.data, "tesla_model_key": model})
        await hass.async_block_till_done(wait_background_tasks=True)
    planner = hass.data[DOMAIN][entry.entry_id].smart
    quarter_prices(hass, {**{q: 0.5 for q in range(12, 28)}, **{q: 1.5 for q in range(28, 38)}})
    await hass.services.async_call("number", "set_value", {"entity_id": "number.bil_target_soc", "value": 100},
                                   blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    return planner


async def turn(hass: HomeAssistant, on: bool) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": SWITCH}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


def blocks(hass: HomeAssistant) -> list[tuple[datetime, datetime]]:
    return [(dt_util.parse_datetime(block["start"]), dt_util.parse_datetime(block["end"]))
            for block in hass.states.get(PLAN).attributes["blocks"]]


async def test_nmc_charges_the_last_part_just_before_departure(hass: HomeAssistant, request, freezer):
    planner = await car(hass, request, freezer, "model3_2017_lr_awd")
    assert planner.chemistry == "nmc"
    assert state(hass, SWITCH) == "off", "a choice, off by default"
    off = blocks(hass)
    assert hass.states.get(PLAN).attributes["top_up"]["reason"] == "off"
    assert off[-1][1] <= evening() + timedelta(hours=7), "without it, all in the cheap hours 00-04"

    await turn(hass, True)
    on = blocks(hass)
    ready = evening() + timedelta(hours=10)
    top_up = hass.states.get(PLAN).attributes["top_up"]
    assert top_up["active"] is True and top_up["from_soc"] == 80 and top_up["reason"] is None
    assert dt_util.parse_datetime(top_up["window_end"]) == ready - timedelta(minutes=30)
    assert dt_util.parse_datetime(top_up["window_start"]) == ready - timedelta(hours=3, minutes=30)
    assert on != off
    assert on[-1][1] <= ready - timedelta(minutes=30), "done 30 minutes before the ready-by time"
    assert on[-1][1] > evening() + timedelta(hours=7), "the part above 80 % is charged after 04:00"
    assert planner.schedule.energy_kwh == round(30 / 0.9, 2)
    assert planner.schedule.shortfall_kwh == 0
    attributes = hass.states.get(SWITCH).attributes
    assert attributes["active"] is True and attributes["chemistry"] == "nmc"

    # The numbers move the window: done an hour before, from 90 %.
    for entity_id, value in (("number.bil_top_up_done_before_departure", 60), ("number.bil_top_up_from_level", 90)):
        await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value},
                                       blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    top_up = hass.states.get(PLAN).attributes["top_up"]
    assert top_up["from_soc"] == 90
    assert dt_util.parse_datetime(top_up["window_end"]) == ready - timedelta(hours=1)
    assert blocks(hass)[-1][1] <= ready - timedelta(hours=1)


async def test_lfp_is_left_alone(hass: HomeAssistant, request, freezer):
    planner = await car(hass, request, freezer, "model3_2021_rwd")
    assert planner.chemistry == "lfp"
    off = blocks(hass)
    await turn(hass, True)
    assert blocks(hass) == off, "an LFP battery is fine at 100 %"
    attributes = hass.states.get(SWITCH).attributes
    assert attributes["active"] is False and attributes["reason"] == "lfp"
    top_up = hass.states.get(PLAN).attributes["top_up"]
    assert top_up["active"] is False and top_up["reason"] == "lfp" and top_up["windows"] == []


async def test_an_unknown_battery_follows_the_choice(hass: HomeAssistant, request, freezer):
    planner = await car(hass, request, freezer, None)
    assert planner.chemistry is None
    await turn(hass, True)
    assert hass.states.get(PLAN).attributes["top_up"]["active"] is True
    await turn(hass, False)
    assert hass.states.get(PLAN).attributes["top_up"]["active"] is False


async def test_the_choice_survives_a_restart(hass: HomeAssistant, request, freezer):
    mock_restore_cache(hass, (State(SWITCH, "on"),))
    planner = await car(hass, request, freezer, None)
    assert planner.flags["nmc_top_up"] is True and state(hass, SWITCH) == "on"
    assert planner.schedule.top_up
