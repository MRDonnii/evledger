"""A plan in several periods must hold its pauses (16 Oct 2026: at the end of a period the charger was stopped, Zaptec's
cloud said "charging" again two seconds later for a minute, the plan took that for a start from the app, switched to
Charge now and charged to 100 % instead of the 68 % chosen with "Lad kun til"). These tests play whole nights against a
simulated Zaptec charger, with the charger's cloud lagging behind the commands."""

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.evledger.const import DOMAIN

from .test_smart_charge import MODE, SWITCH, asked_about_waiting, limit_prices, limit_setup, setup, state


def split_prices(start) -> list[dict]:
    """Two cheap hours with a dear one between them: the plan charges at 23 and at 01 and pauses at 00."""
    result = []
    for hour in range(-1, 30):
        begin = start + timedelta(hours=hour)
        price = 0.5 if hour in (1, 3) else 3.0
        result.append({"start": begin.isoformat(), "end": (begin + timedelta(hours=1)).isoformat(), "price": price})
    return result


async def at(hass, freezer, moment):
    """Move the clock to a moment (and run the planner's minute tick)."""
    freezer.move_to(moment)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def charger(hass, mode: str) -> None:
    hass.states.async_set(MODE, mode)
    hass.states.async_set(SWITCH, "on" if mode == "connected_charging" else "off")
    await hass.async_block_till_done(wait_background_tasks=True)


async def night(hass, request, freezer):
    """22:00, the car plugged in at 50 % with a target of 80 % (60 kWh): two periods, 23–00 and 01–02."""
    t0 = dt_util.now().replace(hour=22, minute=0, second=0, microsecond=0)
    freezer.move_to(t0)
    entry, calls = await setup(hass, request, cheap_now=False)
    hass.states.async_set("sensor.price", "3.0", {"prices": split_prices(t0), "unit_of_measurement": "kr/kWh"})
    await hass.async_block_till_done(wait_background_tasks=True)
    planner = hass.data[DOMAIN][entry.entry_id].smart
    blocks = [(b.start, b.end) for b in planner.schedule.blocks]
    assert len(blocks) == 2, f"a plan in two periods, got {blocks}"
    assert blocks[0][0] == t0 + timedelta(hours=1) and blocks[1][0] == t0 + timedelta(hours=3)
    return entry, calls, planner, t0


async def first_period(hass, freezer, calls, t0):
    """The first period runs and ends with our own stop."""
    await at(hass, freezer, t0 + timedelta(hours=1, seconds=30))
    assert calls["switch.turn_on"] == [SWITCH], "the first period starts"
    await charger(hass, "connected_charging")
    await at(hass, freezer, t0 + timedelta(hours=1, minutes=30))
    assert calls["switch.turn_on"] == [SWITCH] and not calls["switch.turn_off"], "charges through the period"
    hass.states.async_set("sensor.car_battery", "66", {"unit_of_measurement": "%"})  # 11 kWh in the battery
    end = t0 + timedelta(hours=2)
    await at(hass, freezer, end + timedelta(seconds=20))
    assert calls["switch.turn_off"] == [SWITCH], "stopped at the end of the period"
    return end


async def test_the_pause_holds_when_the_charger_lags_behind_the_stop(hass: HomeAssistant, request, freezer):
    entry, calls, planner, t0 = await night(hass, request, freezer)
    end = await first_period(hass, freezer, calls, t0)
    await charger(hass, "connected_finished")
    # The cloud's old state two seconds later, for a minute (as on 16 Oct 2026), with the old power.
    await at(hass, freezer, end + timedelta(seconds=22))
    hass.states.async_set("sensor.charger_power", "11091", {"unit_of_measurement": "W"})
    await charger(hass, "connected_charging")
    assert state(hass, "select.bil_charge_mode") == "smart", "not taken for a start from the app"
    assert state(hass, "sensor.bil_charge_status") != "charging", "an old 'charging' is not shown as charging"
    await at(hass, freezer, end + timedelta(seconds=80))
    assert calls["switch.turn_off"] == [SWITCH, SWITCH], "still 'charging': the stop is sent again"
    hass.states.async_set("sensor.charger_power", "0", {"unit_of_measurement": "W"})
    await charger(hass, "connected_finished")
    # Through the whole pause: never started, never Charge now.
    for minute in range(2, 60, 3):
        await at(hass, freezer, end + timedelta(minutes=minute))
        assert state(hass, "select.bil_charge_mode") == "smart"
        assert calls["switch.turn_on"] == [SWITCH], f"no start in the pause ({minute} min)"
        assert state(hass, "sensor.bil_charge_status") == "waiting"
    # The second period starts on time.
    await at(hass, freezer, t0 + timedelta(hours=3, seconds=30))
    assert calls["switch.turn_on"] == [SWITCH, SWITCH], "the second period starts"
    await charger(hass, "connected_charging")
    await at(hass, freezer, t0 + timedelta(hours=3, minutes=20))
    assert state(hass, "select.bil_charge_mode") == "smart"
    assert state(hass, "sensor.bil_charge_status") == "charging"


async def test_a_long_lag_of_four_minutes_still_holds_the_pause(hass: HomeAssistant, request, freezer):
    _, calls, _, t0 = await night(hass, request, freezer)
    end = await first_period(hass, freezer, calls, t0)
    # No "paused" at all at first: the cloud keeps saying "charging" for four minutes after the stop.
    for second in range(30, 240, 30):
        await at(hass, freezer, end + timedelta(seconds=20 + second))
        assert state(hass, "select.bil_charge_mode") == "smart", f"{second} s after the stop"
    assert len(calls["switch.turn_off"]) >= 3, "the stop is repeated while the charger says charging"
    await charger(hass, "connected_finished")
    for minute in range(5, 60, 5):
        await at(hass, freezer, end + timedelta(minutes=minute))
        assert calls["switch.turn_on"] == [SWITCH], f"no start in the pause ({minute} min)"
        assert state(hass, "select.bil_charge_mode") == "smart"


async def test_flickers_at_every_period_end_through_a_whole_night(hass: HomeAssistant, request, freezer):
    """Both period ends flicker; the night ends with exactly two starts and the plan never leaves Cheapest."""
    _, calls, _, t0 = await night(hass, request, freezer)
    end = await first_period(hass, freezer, calls, t0)
    for flick in ("connected_finished", "connected_charging", "connected_finished", "connected_charging",
                  "connected_finished"):
        await charger(hass, flick)
        await at(hass, freezer, dt_util.now() + timedelta(seconds=15))
    await at(hass, freezer, end + timedelta(minutes=30))
    assert calls["switch.turn_on"] == [SWITCH]
    await at(hass, freezer, t0 + timedelta(hours=3, seconds=30))
    await charger(hass, "connected_charging")
    assert calls["switch.turn_on"] == [SWITCH, SWITCH]
    # The second period ends early (the car is at its target) – the charger reports finished and flickers.
    hass.states.async_set("sensor.car_battery", "80", {"unit_of_measurement": "%"})
    for flick in ("connected_finished", "connected_charging", "connected_finished"):
        await charger(hass, flick)
        await at(hass, freezer, dt_util.now() + timedelta(seconds=20))
    for minute in range(50, 180, 10):
        await at(hass, freezer, t0 + timedelta(hours=3, minutes=minute))
        assert state(hass, "select.bil_charge_mode") == "smart", f"{minute} min after 01:00"
    assert calls["switch.turn_on"] == [SWITCH, SWITCH], "no start after the target"


async def test_a_real_start_from_the_app_later_in_the_pause_is_followed(hass: HomeAssistant, request, freezer):
    """Well after our stop a start from the app or the car is the user's: Charge now, as before."""
    _, calls, _, t0 = await night(hass, request, freezer)
    end = await first_period(hass, freezer, calls, t0)
    await charger(hass, "connected_finished")
    await at(hass, freezer, end + timedelta(minutes=20))
    await charger(hass, "connected_charging")
    assert state(hass, "select.bil_charge_mode") == "smart", "confirmed first: it must last 5 minutes"
    await at(hass, freezer, end + timedelta(minutes=23))
    assert state(hass, "select.bil_charge_mode") == "smart"
    assert calls["switch.turn_off"] == [SWITCH], "and it is not stopped meanwhile"
    await at(hass, freezer, end + timedelta(minutes=26))
    assert state(hass, "select.bil_charge_mode") == "now"


async def test_an_old_stopped_right_after_our_start_is_not_an_outside_stop(hass: HomeAssistant, request, freezer):
    _, calls, _, t0 = await night(hass, request, freezer)
    await at(hass, freezer, t0 + timedelta(hours=1, seconds=30))
    assert calls["switch.turn_on"] == [SWITCH]
    await charger(hass, "connected_charging")
    await at(hass, freezer, t0 + timedelta(hours=1, seconds=50))
    await charger(hass, "connected_finished")  # the cloud's old state, 20 s after our start
    assert state(hass, "sensor.bil_charge_status") != "stopped_externally"
    await at(hass, freezer, t0 + timedelta(hours=1, minutes=5))
    assert len(calls["switch.turn_on"]) == 2, "started again a few minutes later"
    await charger(hass, "connected_charging")
    await at(hass, freezer, t0 + timedelta(hours=1, minutes=40))
    assert state(hass, "sensor.bil_charge_status") == "charging"
    assert not calls["switch.turn_off"], "not stopped inside the period"


async def test_the_waiting_answer_survives_a_restart_before_the_car_reports(hass: HomeAssistant, request, freezer):
    """16 Oct 2026, 00:51: after a restart the car's level was not known yet, the question could not be worked out and
    the plan went back to the target (six periods to 100 %) although "Lad kun til 68 %" had been chosen."""
    entry, _, sent = await limit_setup(hass, request, freezer)
    planner = hass.data[DOMAIN][entry.entry_id].smart
    # Tonight's cheap hour only, less the 5 minutes the car takes to wake up (the 80 % target would be 20.67 kWh).
    waiting_kwh = 11.0 - 11 * 5 / 60
    hass.bus.async_fire("mobile_app_notification_action",
                        {"action": asked_about_waiting(sent)[0]["data"]["actions"][0]["action"]})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert planner.schedule.energy_kwh == pytest.approx(waiting_kwh, abs=0.01)
    answer = planner.wait_params
    assert answer is not None, "the answer is kept as it was given"
    # Restarted before the car and the price forecast are back: only tonight's prices, so no cheaper period can be
    # worked out (the old code then planned to the target again).
    tonight = [p for p in limit_prices() if dt_util.parse_datetime(p["start"]) < dt_util.now() + timedelta(hours=12)]
    hass.states.async_set("sensor.price", "3.0", {"prices": tonight, "unit_of_measurement": "kr/kWh"})
    hass.states.async_set("sensor.car_battery", "unavailable")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    planner = hass.data[DOMAIN][entry.entry_id].smart
    assert state(hass, "switch.bil_wait_above_the_price_limit") == "on"
    assert planner.wait_params == answer, "restored after the restart"
    assert planner.schedule.energy_kwh == pytest.approx(waiting_kwh, abs=0.01), "still tonight's level, not the target"
    hass.states.async_set("sensor.car_battery", "49", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.price", "3.0", {"prices": limit_prices(), "unit_of_measurement": "kr/kWh"})
    await at(hass, freezer, dt_util.now() + timedelta(minutes=2))
    assert planner.schedule.energy_kwh == pytest.approx(waiting_kwh, abs=0.01), "and when the car reports again"
    attrs = hass.states.get("sensor.bil_next_charge_start").attributes
    assert attrs["limit_wait"] is True and attrs["limit_soc"] is not None


async def test_a_restart_in_the_pause_keeps_the_pause(hass: HomeAssistant, request, freezer):
    entry, calls, _, t0 = await night(hass, request, freezer)
    end = await first_period(hass, freezer, calls, t0)
    await charger(hass, "connected_finished")
    await at(hass, freezer, end + timedelta(minutes=10))
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    for minute in range(12, 60, 4):
        await at(hass, freezer, end + timedelta(minutes=minute))
        assert calls["switch.turn_on"] == [SWITCH], f"no start in the pause after the restart ({minute} min)"
        assert state(hass, "select.bil_charge_mode") == "smart"
    await at(hass, freezer, t0 + timedelta(hours=3, seconds=30))
    assert calls["switch.turn_on"] == [SWITCH, SWITCH], "the second period starts after the restart"


async def test_an_old_charging_at_start_up_in_the_pause_is_stopped(hass: HomeAssistant, request, freezer):
    """Home Assistant restarts in the pause and the charger's cloud says "charging" while it is down: the plan stops
    the charger after the start and keeps the pause instead of going on charging."""
    entry, calls, _, t0 = await night(hass, request, freezer)
    end = await first_period(hass, freezer, calls, t0)
    await charger(hass, "connected_finished")
    await at(hass, freezer, end + timedelta(minutes=10))
    stops = len(calls["switch.turn_off"])
    assert await hass.config_entries.async_unload(entry.entry_id)
    await charger(hass, "connected_charging")  # what the cloud says while Home Assistant restarts
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await at(hass, freezer, end + timedelta(minutes=11))
    assert state(hass, "select.bil_charge_mode") == "smart"
    assert len(calls["switch.turn_off"]) > stops, "stopped again"
    await charger(hass, "connected_finished")
    for minute in range(14, 60, 4):
        await at(hass, freezer, end + timedelta(minutes=minute))
        assert calls["switch.turn_on"] == [SWITCH], f"no start in the pause ({minute} min)"
        assert state(hass, "select.bil_charge_mode") == "smart"
    await at(hass, freezer, t0 + timedelta(hours=3, seconds=30))
    assert calls["switch.turn_on"] == [SWITCH, SWITCH], "the second period starts"


# The night of 15–16 Oct 2026, quarter by quarter from 23:30 (Strømligning, kr/kWh).
NIGHT_16_OCT = [2.0907, 2.0719, 2.0763, 2.0864, 1.9394, 1.894, 2.0484, 1.9951, 2.0076, 2.0114, 2.0449, 2.0406, 2.0345,
                2.0363, 2.107, 2.0768, 2.0283, 2.0223, 2.0941, 2.0422, 2.0381, 2.0429, 1.9787, 1.9947, 2.0362, 2.1342,
                2.3438, 2.5423, 2.6, 2.7, 2.8, 2.9]


async def test_replay_of_the_night_of_16_october(hass: HomeAssistant, request, freezer):
    """The real night: 46 %, "Lad kun til" with a price limit of 2.00 kr and 44.4 % to keep, the three periods
    00:30–01:00, 01:15–01:30 and 05:00–05:30, and Zaptec's cloud saying "charging" 01:00:24–01:01:24 after the stop.
    It must end around 66 % (60 kWh test battery) with three starts – not "Charge now" to the target."""
    t0 = dt_util.now().replace(hour=23, minute=30, second=0, microsecond=0)
    freezer.move_to(t0 - timedelta(minutes=30))
    entry, calls = await setup(hass, request, cheap_now=False, soc="46")
    quarters = [{"start": (t0 + timedelta(minutes=15 * i)).isoformat(),
                 "end": (t0 + timedelta(minutes=15 * (i + 1))).isoformat(), "price": p}
                for i, p in enumerate(NIGHT_16_OCT)]
    hass.states.async_set("sensor.price", "2.09", {"prices": quarters, "unit_of_measurement": "kr/kWh"})
    planner = hass.data[DOMAIN][entry.entry_id].smart
    planner.flags["smart_price_limit"] = True
    planner.limit_wait, planner.wait_params = True, (44.4, 2.0)  # the answer "Lad kun til" as given that night
    planner.async_recalculate()
    await hass.async_block_till_done(wait_background_tasks=True)
    starts = [dt_util.as_local(b.start).strftime("%H:%M") for b in planner.schedule.blocks]
    assert starts == [(t0 + timedelta(minutes=m)).strftime("%H:%M") for m in (60, 105, 330)], starts
    soc = 46.0

    async def period(begin_min: int, end_min: int, flicker: bool) -> None:
        nonlocal soc
        await at(hass, freezer, t0 + timedelta(minutes=begin_min, seconds=30))
        await charger(hass, "connected_charging")
        await at(hass, freezer, t0 + timedelta(minutes=end_min - 1))
        soc += (end_min - begin_min) / 60 * 11 * 0.9 / 60 * 100
        hass.states.async_set("sensor.car_battery", str(round(soc)), {"unit_of_measurement": "%"})
        await at(hass, freezer, t0 + timedelta(minutes=end_min, seconds=20))
        await at(hass, freezer, t0 + timedelta(minutes=end_min, seconds=22))
        await charger(hass, "connected_finished")
        if flicker:
            await at(hass, freezer, t0 + timedelta(minutes=end_min, seconds=24))
            await charger(hass, "connected_charging")  # Zaptec's cloud, a minute behind
            await at(hass, freezer, t0 + timedelta(minutes=end_min + 1, seconds=24))
            await charger(hass, "connected_finished")

    await period(60, 90, flicker=True)
    assert len(calls["switch.turn_on"]) == 1 and state(hass, "select.bil_charge_mode") == "smart"
    for minute in range(92, 105, 3):  # the pause 01:00–01:15
        await at(hass, freezer, t0 + timedelta(minutes=minute))
        assert len(calls["switch.turn_on"]) == 1, "no start in the first pause"
    await period(105, 120, flicker=True)
    for minute in range(122, 330, 10):  # the long pause 01:30–05:00
        await at(hass, freezer, t0 + timedelta(minutes=minute))
        assert len(calls["switch.turn_on"]) == 2, f"no start in the long pause ({minute} min)"
        assert state(hass, "select.bil_charge_mode") == "smart"
    await period(330, 360, flicker=False)
    assert len(calls["switch.turn_on"]) == 3, "three periods, three starts"
    for minute in range(362, 450, 10):
        await at(hass, freezer, t0 + timedelta(minutes=minute))
        assert len(calls["switch.turn_on"]) == 3, "nothing after the last period"
    assert state(hass, "select.bil_charge_mode") == "smart"
    assert 64 <= soc <= 68, f"ends at tonight's level, not the target: {soc:.1f} %"


async def test_a_car_waking_after_a_restart_and_after_its_period_is_stopped(hass: HomeAssistant, request, freezer):
    """The start was sent, Home Assistant restarted, and the car woke only after the period had ended: still the
    plan's own start (kept across the restart), so it is stopped – not taken for a start from the app."""
    entry, calls, _, t0 = await night(hass, request, freezer)
    await at(hass, freezer, t0 + timedelta(hours=1, seconds=30))
    assert calls["switch.turn_on"] == [SWITCH], "started, but the car sleeps"
    await at(hass, freezer, t0 + timedelta(hours=1, minutes=10))
    # Only the second cheap hour is needed now, so charging at the dear price after the period is clearly dearer.
    hass.states.async_set("sensor.car_battery", "68", {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await at(hass, freezer, t0 + timedelta(hours=2, minutes=1))  # the period is over
    stops = len(calls["switch.turn_off"])
    await at(hass, freezer, t0 + timedelta(hours=2, minutes=4))
    await charger(hass, "connected_charging")  # the start carried out late (or the car waking) after the stop
    for minute in (5, 7, 10):
        await at(hass, freezer, t0 + timedelta(hours=2, minutes=minute))
        assert state(hass, "select.bil_charge_mode") == "smart", f"not Charge now ({minute})"
    assert len(calls["switch.turn_off"]) > stops, "stopped: the pause holds"


async def test_a_charger_command_that_fails_with_any_error_is_only_logged(hass: HomeAssistant, caplog):
    """The charger's cloud times out (not a Home Assistant error): the command is logged as failed and nothing breaks;
    the controller sends it again (a stop every 45 s while the charger says charging, a start after 3 minutes)."""
    from custom_components.evledger.smart import charger as charger_module

    async def timeout(call):
        raise TimeoutError("Zaptec cloud timeout")

    hass.services.async_register("switch", "turn_off", timeout)
    backend = charger_module.ChargerBackend(hass)
    await backend._call("switch", "turn_off", "switch.charger_charging")
    assert "switch.turn_off on switch.charger_charging failed" in caplog.text


async def test_home_assistant_away_in_the_night_catches_up_and_tells(hass: HomeAssistant, request, freezer):
    """Home Assistant is away 00:10–01:40, past the start of the second period: back again, the plan catches up at once
    (it still reaches the target before the ready-by time) and the phones are told."""
    from .test_routines import phones, with_options

    entry, calls, planner, t0 = await night(hass, request, freezer)
    sent = phones(hass)
    await with_options(hass, entry)
    end = await first_period(hass, freezer, calls, t0)
    await charger(hass, "connected_finished")
    await at(hass, freezer, end + timedelta(minutes=10))
    assert await hass.config_entries.async_unload(entry.entry_id)  # Home Assistant goes away …
    freezer.move_to(t0 + timedelta(hours=3, minutes=40))  # … and is back at 01:40
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await at(hass, freezer, t0 + timedelta(hours=3, minutes=41))
    told = [m for m in sent if "Home Assistant var nede" in m.get("title", "")]
    assert len(told) == 1, [m.get("title") for m in sent]
    assert "Planen er lagt om" in told[0]["message"] and "bilen når stadig 80 %" in told[0]["message"]
    starts = len(calls["switch.turn_on"])
    assert starts >= 2, "the charge starts again at once"
    await at(hass, freezer, t0 + timedelta(hours=4))
    assert len([m for m in sent if "Home Assistant var nede" in m.get("title", "")]) == 1, "once"


async def test_a_normal_restart_is_not_an_outage(hass: HomeAssistant, request, freezer):
    from .test_routines import phones, with_options

    entry, calls, _, t0 = await night(hass, request, freezer)
    sent = phones(hass)
    await with_options(hass, entry)
    await at(hass, freezer, t0 + timedelta(minutes=58))
    assert await hass.config_entries.async_unload(entry.entry_id)
    freezer.move_to(t0 + timedelta(hours=1, minutes=2))  # two minutes, across the start of a period
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    await at(hass, freezer, t0 + timedelta(hours=1, minutes=5))
    assert not [m for m in sent if "Home Assistant var nede" in m.get("title", "")]
