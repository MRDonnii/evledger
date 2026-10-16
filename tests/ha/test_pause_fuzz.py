"""Many random nights against a simulated Zaptec charger and car (user, 2026-10-16: "stop ikke før du er sikker på den
gør som den skal"). The charger's cloud lags behind the commands, reports old states and flickers, the car takes
minutes to wake, Home Assistant restarts – and through every night the plan must hold:

- it never goes to Charge now (nobody starts the car from the app here),
- the car really charges only while the plan wants it to, apart from the seconds a stop takes (an old "charging" from
  the cloud must never keep it charging, start it in a pause or count as the user's start),
- dear charging is only the make-up for a car that woke late,
- the car reaches the target by the morning.
"""

import os
import random
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.evledger.const import DOMAIN
from custom_components.evledger.smart import charger as charger_module

from .test_pauses import NIGHT_16_OCT
from .test_smart_charge import AUTHORIZE, MODE, SWITCH, setup, state

STEP = 15  # seconds per simulation step
NIGHTS = int(os.environ.get("EVLEDGER_FUZZ_NIGHTS", "40"))
FIRST = int(os.environ.get("EVLEDGER_FUZZ_FIRST", "0"))
# Hours after 22:00 with the cheap price: one, two or three periods with dear hours between them.
PATTERNS = [(1, 3), (1, 3, 5), (2, 3), (1, 4, 6), (3,), (1, 2, 5)]


def prices(t0, cheap) -> list[dict]:
    return [{"start": (t0 + timedelta(hours=h)).isoformat(), "end": (t0 + timedelta(hours=h + 1)).isoformat(),
             "price": 0.5 if h in cheap else 3.0} for h in range(-1, 30)]


class Sim:
    """The real charger and car, and what Zaptec's cloud tells Home Assistant about them."""

    def __init__(self, rng: random.Random, now, soc: float, cheap) -> None:
        self.rng = rng
        self.cheap = cheap
        self.needs_authorize = False  # Zaptec waits for authorisation (connected_requesting)
        self.reboot_at = now + timedelta(minutes=rng.uniform(40, 400)) if rng.random() < 0.3 else None
        self.offline_until = None
        self.charging = False  # really charging
        self.wake_at = None  # the car starts drawing power at this moment after a start
        self.soc = soc
        self.reported = "connected_finished"
        self.stale_until = None  # the cloud keeps reporting `stale_state` until then
        self.stale_state = None
        self.flicker_at = now + timedelta(minutes=rng.uniform(5, 90))
        self.dear_charging = 0.0  # seconds really charging in a dear hour
        self.unwanted = 0.0  # seconds really charging while the plan did not want it
        self.log: list[str] = []

    def command(self, service: str, now) -> None:
        self.log.append(f"{now:%H:%M:%S} {service}")
        old = self.reported  # the cloud's last answer
        if service == "button.press" and self.needs_authorize:
            self.needs_authorize = False
            self.wake_at = now + timedelta(seconds=self.rng.uniform(0, 12 * 60))
        elif service == "switch.turn_on" and not self.needs_authorize:
            if not self.charging and self.wake_at is None:
                if self.rng.random() < 0.3:
                    self.needs_authorize = True  # resumed, but now it asks for authorisation (as on 14 Oct)
                else:
                    self.wake_at = now + timedelta(seconds=self.rng.uniform(0, 12 * 60))
        elif service == "switch.turn_off":
            self.charging, self.wake_at = False, None
        if self.rng.random() < 0.5:  # the cloud answers with the old state for a while
            self.stale_state, self.stale_until = old, now + timedelta(seconds=self.rng.uniform(2, 150))

    def step(self, now, t0) -> str:
        if self.wake_at is not None and now >= self.wake_at and self.soc < 99.5:
            self.charging, self.wake_at = True, None
        if self.charging:
            self.soc = min(100.0, self.soc + 11 * STEP / 3600 * 0.9 / 60 * 100)
            hour = int((now - t0).total_seconds() // 3600)
            if hour not in self.cheap:
                self.dear_charging += STEP
        self.reported = self._answer(now)
        return self.reported

    def _answer(self, now) -> str:
        if self.reboot_at is not None and now >= self.reboot_at:  # the charger reboots
            self.reboot_at, self.offline_until = None, now + timedelta(seconds=self.rng.uniform(40, 150))
            self.charging, self.wake_at, self.needs_authorize = False, None, True
        if self.offline_until is not None:
            if now < self.offline_until - timedelta(seconds=20):
                return "unavailable"
            if now < self.offline_until:
                return "disconnected"
            self.offline_until = None
        real = ("connected_charging" if self.charging
                else "connected_requesting" if self.needs_authorize else "connected_finished")
        if self.stale_until is not None and now < self.stale_until:
            return self.stale_state
        self.stale_until = None
        if now >= self.flicker_at:  # an old state out of nowhere, for half a minute to four minutes
            self.flicker_at = now + timedelta(minutes=self.rng.uniform(20, 120))
            self.stale_state = "connected_finished" if self.charging else "connected_charging"
            if self.needs_authorize:
                return real
            self.stale_until = now + timedelta(seconds=self.rng.uniform(30, 240))
            return self.stale_state
        return real


async def play_night(hass: HomeAssistant, request, freezer, seed: int, lad_kun_til: bool = False) -> None:
    rng = random.Random(seed)
    t0 = dt_util.now().replace(hour=22, minute=0, second=0, microsecond=0)
    freezer.move_to(t0)
    # 58 %: 14.7 kWh to the 80 % target, the cheap hours are enough even with a sleepy car. With "Lad kun til" the
    # real night of 16 Oct: 46 %, its quarter prices from 23:30, a price limit of 2.00 and 44.4 % to keep.
    soc0 = 46.0 if lad_kun_til else 58.0
    entry, _ = await setup(hass, request, cheap_now=False, soc=str(int(soc0)))
    cheap = () if lad_kun_til else rng.choice(PATTERNS)
    sim = Sim(rng, t0, soc0, cheap)
    sim.log.append("Lad kun til, 16 Oct prices" if lad_kun_til else f"cheap hours after 22:00: {cheap}")

    async def record(self, domain, service, entity_id):
        sim.command(f"{domain}.{service}", dt_util.now())

    with patch.object(charger_module.ChargerBackend, "_call", record):
        if lad_kun_til:
            start = t0 + timedelta(minutes=90)
            quarters = [{"start": (start + timedelta(minutes=15 * i)).isoformat(),
                         "end": (start + timedelta(minutes=15 * (i + 1))).isoformat(), "price": price}
                        for i, price in enumerate(NIGHT_16_OCT)]
            hass.states.async_set("sensor.price", "2.09", {"prices": quarters, "unit_of_measurement": "kr/kWh"})
            planner = hass.data[DOMAIN][entry.entry_id].smart
            planner.flags["smart_price_limit"] = True
            planner.limit_wait, planner.wait_params = True, (44.4, 2.0)
            planner.async_recalculate()
        else:
            hass.states.async_set("sensor.price", "3.0", {"prices": prices(t0, cheap), "unit_of_measurement": "kr/kWh"})
        await hass.async_block_till_done(wait_background_tasks=True)
        restart_at = t0 + timedelta(minutes=rng.uniform(30, 300)) if rng.random() < 0.5 else None
        now = t0
        end = t0 + timedelta(hours=8, minutes=40)  # 06:40, just before the ready-by time
        last_soc = None
        while now < end:
            now += timedelta(seconds=STEP)
            freezer.move_to(now)
            reported = sim.step(now, t0)
            if round(sim.soc) != last_soc:
                last_soc = round(sim.soc)
                hass.states.async_set("sensor.car_battery", str(last_soc), {"unit_of_measurement": "%"})
            if reported != hass.states.get(MODE).state:
                hass.states.async_set(MODE, reported)
                hass.states.async_set(SWITCH, "on" if reported == "connected_charging" else "off")
            async_fire_time_changed(hass)
            await hass.async_block_till_done(wait_background_tasks=True)
            if restart_at is not None and now >= restart_at:
                restart_at = None
                sim.log.append(f"{now:%H:%M:%S} restart")
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done(wait_background_tasks=True)
            mode = state(hass, "select.bil_charge_mode")
            assert mode == "smart", f"seed {seed}: went to {mode} at {now:%H:%M:%S}\n" + "\n".join(sim.log[-12:])
            planner = hass.data[DOMAIN][entry.entry_id].smart
            if sim.charging and not planner.charge_desired:
                sim.unwanted += STEP
    detail = f"seed {seed}: " + "\n".join(sim.log)
    assert sim.unwanted <= 90, f"{sim.unwanted / 60:.1f} min charging the plan did not want – {detail}"
    if lad_kun_til:
        # Five quarters at or below 2.00 kr: about 67 % at most (60 kWh test battery) – never on to the target.
        assert sim.soc <= 69, f"charged to {sim.soc:.1f} % although waiting with the rest was chosen – {detail}"
        assert planner.limit_wait, "the answer stays for the night (also across restarts)"
        return
    # Dear charging only for what the cheap hours cannot hold, plus the make-up for a car that woke late.
    need_min = (80 - soc0) / 100 * 60 / 0.9 / 11 * 60
    allowed = max(0.0, need_min - 60 * len(cheap)) + 25
    assert sim.dear_charging <= allowed * 60, (
        f"{sim.dear_charging / 60:.1f} min charging at the dear price (allowed {allowed:.0f}) – {detail}")
    assert sim.soc >= 79, f"only {sim.soc:.1f} % by the morning – {detail}"
    planner = hass.data[DOMAIN][entry.entry_id].smart
    assert planner.mode == "smart"


@pytest.mark.parametrize("seed", range(FIRST, FIRST + NIGHTS))
async def test_random_nights_hold_the_plan(hass: HomeAssistant, request, freezer, seed: int):
    await play_night(hass, request, freezer, seed)


@pytest.mark.parametrize("seed", range(FIRST, FIRST + NIGHTS))
async def test_random_nights_with_lad_kun_til_never_charge_to_the_target(hass: HomeAssistant, request, freezer,
                                                                        seed: int):
    await play_night(hass, request, freezer, seed, lad_kun_til=True)


def test_authorize_is_simulated():
    assert AUTHORIZE.startswith("button.")
