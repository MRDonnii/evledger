"""The nights the clocks change (Europe/Copenhagen): 25 Oct 2026 03:00 → 02:00 (an hour twice) and 28 Mar 2027 02:00 →
03:00 (an hour missing). Quarter prices across the change, a plan in several periods, and the car charged by the
ready-by time – with the same random cloud lag and flickers as the other random nights."""

import random
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.evledger.const import DOMAIN
from custom_components.evledger.smart import charger as charger_module

from .test_pause_fuzz import STEP, Sim
from .test_smart_charge import MODE, SWITCH, setup, state

NIGHTS = {"autumn": datetime(2026, 10, 24, 20, 0, tzinfo=UTC), "spring": datetime(2027, 3, 27, 21, 0, tzinfo=UTC)}


def quarter_prices(start_utc, cheap_local_hours) -> list[dict]:
    """30 hours of quarters from start (UTC), cheap in the given local clock hours; ISO times with the local offset."""
    zone = dt_util.get_default_time_zone()
    result = []
    for i in range(30 * 4):
        begin = start_utc + timedelta(minutes=15 * i)
        local = begin.astimezone(zone)
        result.append({"start": local.isoformat(), "end": (begin + timedelta(minutes=15)).astimezone(zone).isoformat(),
                       "price": 0.5 if local.hour in cheap_local_hours else 3.0})
    return result


@pytest.mark.parametrize("night", ["autumn", "spring"])
@pytest.mark.parametrize("seed", range(6))
async def test_the_night_the_clocks_change(hass: HomeAssistant, request, freezer, night: str, seed: int):
    await hass.config.async_set_time_zone("Europe/Copenhagen")
    t0 = NIGHTS[night]  # 22:00 local
    freezer.move_to(t0)
    rng = random.Random(seed)
    soc0 = 46.0
    entry, _ = await setup(hass, request, cheap_now=False, soc=str(int(soc0)))
    # Cheap at 00 and in the changed hour (02 local – twice in autumn, missing in spring) and at 04.
    cheap = (0, 2, 4)
    sim = Sim(rng, t0, soc0, ())
    sim.flicker_at = t0 + timedelta(days=9)  # cloud lag after commands only (flickers are tested elsewhere)
    sim.reboot_at = None

    async def record(self, domain, service, entity_id):
        sim.command(f"{domain}.{service}", dt_util.utcnow())

    with patch.object(charger_module.ChargerBackend, "_call", record):
        hass.states.async_set("sensor.price", "3.0", {"prices": quarter_prices(t0 - timedelta(hours=1), cheap),
                                                      "unit_of_measurement": "kr/kWh"})
        await hass.async_block_till_done(wait_background_tasks=True)
        planner = hass.data[DOMAIN][entry.entry_id].smart
        blocks = [(b.start, b.end) for b in planner.schedule.blocks]
        assert blocks, "a plan"
        for a, b in blocks:
            assert b > a, f"every period has a length (25 Oct 2026 made one of none): {blocks}"
        for a, b in zip(blocks, blocks[1:], strict=False):
            assert a[1] <= b[0], f"periods in order across the change: {blocks}"
        deadline = planner.deadline
        local_deadline = deadline.astimezone(dt_util.get_default_time_zone())
        assert (local_deadline.hour, local_deadline.minute) == (7, 0), f"ready by 07:00 (the default): {local_deadline}"
        now = t0
        end = deadline - timedelta(minutes=5)
        unwanted = 0
        while now < end:
            now += timedelta(seconds=STEP)
            freezer.move_to(now)
            reported = sim.step(now, t0)
            hass.states.async_set("sensor.car_battery", str(round(sim.soc)), {"unit_of_measurement": "%"})
            if reported != hass.states.get(MODE).state:
                hass.states.async_set(MODE, reported)
                hass.states.async_set(SWITCH, "on" if reported == "connected_charging" else "off")
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)
            assert state(hass, "select.bil_charge_mode") == "smart", f"{night} {seed}: Charge now at {now}"
            if sim.charging and not planner.charge_desired:
                unwanted += STEP
            local = now.astimezone(dt_util.get_default_time_zone())
            if sim.charging and local.hour not in cheap and planner.schedule.blocks:
                inside = any(b.start <= now < b.end for b in planner.schedule.blocks)
                assert inside or unwanted <= 90, f"{night} {seed}: charging outside the plan at {local}"
    assert unwanted <= 90, f"{night} {seed}: {unwanted} s charging the plan did not want\\n" + "\\n".join(sim.log)
    assert sim.soc >= 79, f"{night} {seed}: only {sim.soc:.1f} % by the ready-by time\\n" + "\\n".join(sim.log)
