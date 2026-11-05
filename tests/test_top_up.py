"""Sparing an NMC battery: the part above the top-up level is charged just before departure (Claude AI, 2026-11-05)."""

import random
from datetime import datetime, timedelta, timezone

from _load import load

plan = load("plan")

TZ = timezone(timedelta(hours=2))
NIGHT = datetime(2026, 10, 9, 22, 0, tzinfo=TZ)
READY = NIGHT + timedelta(hours=9)  # 07:00
# 22, 23, 00, 01, 02, 03, 04, 05, 06: cheapest at night, dearer in the morning (and 00:00 cheaper than 04-06).
PRICES = [3, 2, 1, 0.5, 0.5, 0.6, 1.5, 1.2, 2.0]
QUARTER_KWH = 6 * 0.25  # 6 kW


def hourly(start: datetime, prices: list[float]) -> list:
    raw = [{"start": (start + timedelta(hours=h)).isoformat(), "end": (start + timedelta(hours=h + 1)).isoformat(),
            "price": p} for h, p in enumerate(prices)]
    return plan.parse_price_attributes({"prices": raw})


def schedule(soc=50.0, constraints=None, prices=None, horizon_h=9, mode="smart", **kw):
    known = hourly(NIGHT, prices or PRICES)
    data = plan.ScheduleInput(
        mode=mode, soc=soc, target_soc=kw.get("target", 100.0), capacity_kwh=60, efficiency=1.0, power_kw=6,
        price_factor=1.0, timeline=plan.build_timeline(NIGHT, known, NIGHT + timedelta(hours=horizon_h)),
        constraints=tuple(constraints or (plan.Constraint(READY, kw.get("target", 100.0)),)),
        window=kw.get("window"), price_cap=kw.get("price_cap"), min_soc=kw.get("min_soc"),
        keep_start=kw.get("keep_start"), wake_h=kw.get("wake_min", 0) / 60, margin_h=kw.get("margin_h", 0.0),
        charging=kw.get("charging", False), top_up_soc=kw.get("top_up_soc"), max_price=kw.get("max_price"),
        floor_soc=kw.get("floor_soc"),
        top_up_margin_h=kw.get("top_up_margin_h", 0.5), top_up_window_h=kw.get("top_up_window_h", 3.0))
    return plan.build_schedule(data, NIGHT)


def at(hour: int, minute: int = 0, day: int = 10) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def before(result, moment: datetime) -> float:
    return sum(slot.kwh for slot in result.slots if slot.start < moment)


def test_nmc_bulk_cheap_early_and_the_top_part_late():
    off = schedule()
    on = schedule(top_up_soc=80)
    assert off.energy_kwh == on.energy_kwh == 30.0
    # Without the rule the cheapest five hours, also 00:00 (price 1) far from departure.
    assert any(slot.start < at(1) for slot in off.slots)
    # With it: up to 80 % (18 kWh) in the cheapest hours 01-04, the last 12 kWh in 04-06 (the window 03:30-06:30).
    assert [(block.start, block.end) for block in on.blocks] == [(at(1), at(6))]
    assert before(on, at(3, 30)) <= 18.0 + QUARTER_KWH
    assert all(slot.end <= READY - timedelta(minutes=30) for slot in on.slots)
    assert on.top_up == (plan.TopUpWindow(READY, 80, at(3, 30), at(6, 30)),)
    assert on.shortfall_kwh == 0


def test_off_and_target_not_above_the_level_change_nothing():
    assert schedule(top_up_soc=None) == schedule()
    assert schedule(target=80.0, top_up_soc=80) == schedule(target=80.0)
    assert schedule(target=80.0, top_up_soc=80).top_up == ()


def test_other_modes_are_not_affected():
    window = (at(0), at(6))
    for mode, kw in (("now", {}), ("fixed", {"window": window}), ("price_cap", {"price_cap": 0.55})):
        assert schedule(mode=mode, top_up_soc=80, **kw) == schedule(mode=mode, **kw), mode


def test_already_above_the_level_charges_everything_in_the_window():
    result = schedule(soc=85.0, top_up_soc=80)
    assert result.energy_kwh == 9.0
    assert all(at(3, 30) <= slot.start and slot.end <= at(6, 30) for slot in result.slots)
    # The cheapest 1.5 h in the window: 03:30-04:00 (0.6) and 05:00-06:00 (1.2).
    assert [(block.start, block.end) for block in result.blocks] == [(at(3, 30), at(4)), (at(5), at(6))]


def test_a_window_too_short_starts_earlier_and_the_target_is_still_reached():
    # One hour (05:30-06:30) holds 6 kWh; the last 12 kWh need two hours.
    result = schedule(top_up_soc=80, top_up_window_h=1.0)
    assert result.energy_kwh == 30.0 and result.shortfall_kwh == 0
    window = result.top_up[0]
    assert window.end == at(6, 30) and window.start == at(4, 30)
    assert before(result, window.start) <= 18.0 + QUARTER_KWH
    assert all(slot.end <= at(6, 30) for slot in result.slots)


def test_no_time_for_the_rule_still_reaches_the_target():
    # 30 kWh at 6 kW is five hours; with a ready-by time 5.25 h away and a margin of an hour there is no room.
    ready = NIGHT + timedelta(hours=5, minutes=15)
    result = schedule(constraints=[plan.Constraint(ready, 100.0)], top_up_soc=80, top_up_margin_h=1.0)
    assert result.energy_kwh == 30.0 and result.shortfall_kwh == 0


def test_two_ready_by_times_each_get_their_own_window():
    # Tomorrow 07:00 to 90 %, the day after 07:00 to 100 % (a trip, a week plan day with its own target).
    second = READY + timedelta(days=1)
    prices = PRICES + [3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3] + PRICES
    result = schedule(constraints=[plan.Constraint(READY, 90.0), plan.Constraint(second, 100.0)], prices=prices,
                      horizon_h=33, top_up_soc=80)
    assert result.energy_kwh == 30.0 and result.shortfall_kwh == 0
    first, last = result.top_up
    assert (first.start, first.end) == (at(3, 30), at(6, 30))
    assert (last.start, last.end) == (at(3, 30, day=11), at(6, 30, day=11))
    # Up to 80 % before the first window, up to 90 % by its end, the last 10 % (6 kWh) in the second window.
    assert before(result, first.start) <= 18.0 + QUARTER_KWH
    assert before(result, first.end) == 24.0
    assert before(result, last.start) == 24.0
    assert all(slot.end <= at(6, 30, day=11) for slot in result.slots)


def test_the_ready_margin_and_a_waking_car_keep_the_rule():
    result = schedule(top_up_soc=80, margin_h=0.25, wake_min=10, keep_start=at(1))
    assert result.shortfall_kwh == 0
    assert all(slot.end <= at(6, 30) for slot in result.slots)
    assert before(result, at(3, 30)) <= 18.0 + 2 * QUARTER_KWH


def test_random_prices_never_lose_energy_and_keep_the_window():
    rng = random.Random(20261105)
    for _ in range(200):
        prices = [round(rng.uniform(-0.5, 4), 2) for _ in range(9)]
        soc = rng.choice([10.0, 40.0, 70.0, 85.0])
        level = rng.choice([60, 80, 90])
        kw = {"wake_min": rng.choice([0, 10]), "margin_h": rng.choice([0.0, 0.25]),
              "top_up_window_h": rng.choice([1.0, 3.0, 8.0])}
        off = schedule(soc=soc, prices=prices, **kw)
        on = schedule(soc=soc, prices=prices, top_up_soc=level, **kw)
        assert on.shortfall_kwh <= off.shortfall_kwh + 1e-6, (prices, soc, level, kw)
        assert abs(on.energy_kwh - off.energy_kwh) < 0.02 or on.shortfall_kwh < off.shortfall_kwh
        window = on.top_up[0] if on.top_up else None
        # Where the slots before the window's end can hold the energy (a wake-up or two included), nothing is later.
        if window and on.energy_kwh + 2 <= (window.end - NIGHT).total_seconds() / 3600 * 6:
            assert all(slot.end <= window.end for slot in on.slots), (prices, soc, level, kw)


def test_a_price_limit_keeps_its_slots_and_the_rule_moves_the_window_earlier():
    # Below 1.3 only 00-04 and 05-06: the window (03:30-06:30) holds 6 kWh below the limit, so it starts earlier.
    off = schedule(max_price=1.3, floor_soc=40.0)
    on = schedule(max_price=1.3, floor_soc=40.0, top_up_soc=80)
    assert (on.energy_kwh, on.limit_kwh, on.cost) == (off.energy_kwh, off.limit_kwh, off.cost)
    assert all(slot.price <= 1.3 for slot in on.slots)
    assert on.top_up[0].start < at(3, 30)
