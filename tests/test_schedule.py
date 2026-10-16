"""Tests for the charge schedules (modes, constraints, estimated prices)."""

from datetime import datetime, time, timedelta, timezone

from _load import load

plan = load("plan")

TZ = timezone(timedelta(hours=2))
NIGHT = datetime(2026, 10, 9, 22, 0, tzinfo=TZ)


def hourly(start: datetime, prices: list[float]) -> list:
    raw = [{"start": (start + timedelta(hours=h)).isoformat(), "end": (start + timedelta(hours=h + 1)).isoformat(),
            "price": p} for h, p in enumerate(prices)]
    return plan.parse_price_attributes({"prices": raw})


def schedule(mode="smart", soc=50.0, target=80.0, now=NIGHT, known=None, horizon=None, **kw):
    known = known if known is not None else hourly(NIGHT, [3, 2, 1, 0.5, 0.6, 2, 3, 3, 3, 3])
    horizon = horizon or now + timedelta(hours=10)
    data = plan.ScheduleInput(
        mode=mode, soc=soc, target_soc=target, capacity_kwh=kw.get("capacity", 60), efficiency=1.0,
        power_kw=kw.get("power", 12), price_factor=kw.get("factor", 1.0),
        timeline=plan.build_timeline(now, known, horizon), constraints=tuple(kw.get("constraints", ())),
        window=kw.get("window"), price_cap=kw.get("price_cap"), min_soc=kw.get("min_soc"),
        cap_override=kw.get("cap_override", True), max_price=kw.get("max_price"), floor_soc=kw.get("floor_soc"),
        wake_h=kw.get("wake_min", 0) / 60, charging=kw.get("charging", False))
    return plan.build_schedule(data, now)


def deadline(hours: float, target=80.0):
    return plan.Constraint(NIGHT + timedelta(hours=hours), target)


def test_smart_picks_cheapest_quarters_also_non_contiguous():
    # 30 % of 60 kWh = 18 kWh at 12 kW = 1.5 h = 6 quarters; cheapest hours are 01 (0.5) and 02 (0.6)
    result = schedule(constraints=[deadline(8)])
    assert result.energy_kwh == 18.0
    # 01:00-02:00 at 0.5 and two quarters at 0.6, next to it rather than at the end of the hour
    assert [(b.start, b.end) for b in result.blocks] == [
        (datetime(2026, 10, 10, 1, 0, tzinfo=TZ), datetime(2026, 10, 10, 2, 30, tzinfo=TZ))]
    assert result.cost == round(3 * 4 * 0.5 + 3 * 2 * 0.6, 2)
    assert not result.charge_now


def test_smart_charges_now_when_the_deadline_is_too_close():
    result = schedule(constraints=[deadline(1)])
    assert result.charge_now and result.shortfall_kwh == 6.0


def test_target_reached_means_no_plan():
    result = schedule(soc=85, constraints=[deadline(8)])
    assert result.blocks == () and not result.charge_now and result.energy_kwh == 0


def test_unknown_prices_are_borrowed_from_the_day_before():
    known = hourly(NIGHT - timedelta(days=1), [3, 2, 1, 0.1, 0.6, 2, 3, 3, 3, 3])
    result = schedule(known=known, constraints=[deadline(8)])
    assert result.estimated
    assert result.blocks[0].start.astimezone(TZ).hour == 1  # 01:00 was 0.1 yesterday


def test_fixed_window_charges_in_its_cheapest_quarters():
    window = plan.fixed_window(NIGHT, time(23, 0), time(5, 0))
    result = schedule(mode="fixed", window=window)
    # 6 quarters: the 0.5 hour at 01 and the next two at 0.6, all inside 23-05
    assert [(b.start, b.end) for b in result.blocks] == [
        (datetime(2026, 10, 10, 1, 0, tzinfo=TZ), datetime(2026, 10, 10, 2, 30, tzinfo=TZ))]


def test_fixed_window_never_charges_outside_it():
    window = plan.fixed_window(NIGHT, time(22, 0), time(0, 0))  # 22-24: cheap hours lie after it
    result = schedule(mode="fixed", window=window)
    assert all(b.start >= window[0] and b.end <= window[1] for b in result.blocks)


def test_fixed_window_wraps_and_contains_now():
    assert plan.fixed_window(datetime(2026, 10, 10, 2, 0, tzinfo=TZ), time(22, 0), time(6, 0)) == (
        datetime(2026, 10, 9, 22, 0, tzinfo=TZ), datetime(2026, 10, 10, 6, 0, tzinfo=TZ))
    assert plan.fixed_window(datetime(2026, 10, 10, 7, 0, tzinfo=TZ), time(22, 0), time(6, 0))[0] == (
        datetime(2026, 10, 10, 22, 0, tzinfo=TZ))


def test_price_cap_only_uses_known_cheap_slots():
    result = schedule(mode="price_cap", price_cap=0.55)
    assert [(b.start.astimezone(TZ).hour, b.end.astimezone(TZ).hour) for b in result.blocks] == [(1, 2)]
    assert result.energy_kwh == 12.0  # not enough cheap power for the whole target


def test_price_cap_is_exceeded_only_to_reach_the_target_in_time():
    # 18 kWh to 80 %; below the cap only the 01-02 hour (12 kWh) lies before the ready-by time
    result = schedule(mode="price_cap", price_cap=0.55, constraints=[deadline(10)])
    assert result.energy_kwh == 18.0
    assert result.over_cap_kwh == 6.0
    assert result.over_cap_max_price == 0.6  # the next cheapest hour, 02-03
    assert result.over_cap_extra == 0.3
    assert result.cap_soc == 70.0  # 50 % + 12 kWh of 60
    strict = schedule(mode="price_cap", price_cap=0.55, constraints=[deadline(10)], cap_override=False)
    assert strict.energy_kwh == 12.0
    assert strict.over_cap_kwh == 0
    assert strict.shortfall_kwh == 6.0


def test_price_cap_minimum_is_not_counted_as_over_the_cap():
    result = schedule(mode="price_cap", soc=10, price_cap=0.55, min_soc=20)
    assert result.over_cap_kwh == 0


def test_fixed_window_too_short_reports_the_shortfall():
    window = plan.fixed_window(NIGHT, time(23, 0), time(0, 0))  # one hour: 12 of the 18 kWh
    result = schedule(mode="fixed", window=window)
    assert result.energy_kwh == 12.0
    assert result.shortfall_kwh == 6.0


def test_price_cap_charges_to_minimum_right_away():
    result = schedule(mode="price_cap", soc=10, price_cap=0.55, min_soc=20)
    assert result.charge_now
    assert result.blocks[0].start == NIGHT


def test_charge_now_ignores_prices():
    result = schedule(mode="now")
    assert result.charge_now and result.blocks[0].start == NIGHT


def test_off_has_no_plan():
    result = schedule(mode="off", constraints=[deadline(8)])
    assert result.blocks == () and not result.charge_now


def test_trip_constraint_adds_energy_before_departure():
    # daily: 80 % by 06:00; trip: 95 % by 04:00 -> 27 kWh = 9 quarters before 04:00
    result = schedule(constraints=[deadline(8), deadline(6, 95)])
    assert result.target_soc == 95
    assert result.energy_kwh == 27.0
    assert all(block.end <= NIGHT + timedelta(hours=6) for block in result.blocks)


def test_fixed_mode_still_meets_a_trip():
    window = plan.fixed_window(NIGHT, time(4, 0), time(5, 0))
    result = schedule(mode="fixed", window=window, constraints=[deadline(5, 90)])
    assert result.energy_kwh == 24.0
    assert result.blocks[0].start.astimezone(TZ).hour in (0, 1)  # cheap night slots are added before the trip


def test_partial_current_quarter():
    now = NIGHT + timedelta(minutes=10)
    result = schedule(mode="now", now=now, soc=79, target=80)
    assert result.blocks[0].start == now
    assert result.blocks[0].end == now + timedelta(minutes=3)


def test_dst_change_keeps_quarters_aligned():
    tz = timezone(timedelta(hours=1))
    now = datetime(2026, 10, 25, 1, 50, tzinfo=timezone(timedelta(hours=2)))
    known = plan.parse_price_attributes({"prices": [
        {"start": "2026-10-25T00:00:00+02:00", "end": "2026-10-25T03:00:00+02:00", "price": 1},
        {"start": datetime(2026, 10, 25, 2, 0, tzinfo=tz).isoformat(),
         "end": datetime(2026, 10, 25, 6, 0, tzinfo=tz).isoformat(), "price": 0.2}]})
    timeline = plan.build_timeline(now, known, now + timedelta(hours=4))
    assert all(b.start == a.end for a, b in zip(timeline, timeline[1:], strict=False))
    assert not any(slot.estimated for slot in timeline[:16])


def test_cheaper_quarter_prices_can_split_the_plan():
    known = plan.parse_price_attributes({"prices": [
        {"start": (NIGHT + timedelta(minutes=15 * i)).isoformat(),
         "end": (NIGHT + timedelta(minutes=15 * (i + 1))).isoformat(), "price": 1 if i in (2, 9) else 3}
        for i in range(40)]})
    result = schedule(soc=74, known=known, constraints=[deadline(8)])  # 3.6 kWh = two quarters
    assert len(result.blocks) == 2


def test_small_saving_does_not_split_the_plan():
    # two separate quarters at 1.000, the rest 1.004 -> one block is nearly as cheap (0.2 %)
    known = plan.parse_price_attributes({"prices": [
        {"start": (NIGHT + timedelta(minutes=15 * i)).isoformat(),
         "end": (NIGHT + timedelta(minutes=15 * (i + 1))).isoformat(), "price": 1.0 if i in (4, 30) else 1.004}
        for i in range(40)]})
    result = schedule(soc=74, known=known, constraints=[deadline(8)])
    assert len(result.blocks) == 1


def test_a_saving_of_one_percent_splits_the_plan():
    # 6 kWh = two quarters; two separate quarters at 1.00, the rest 1.02: splitting saves 1 % (more than 0.5 %)
    known = plan.parse_price_attributes({"prices": [
        {"start": (NIGHT + timedelta(minutes=15 * i)).isoformat(),
         "end": (NIGHT + timedelta(minutes=15 * (i + 1))).isoformat(), "price": 1.0 if i in (4, 30) else 1.02}
        for i in range(40)]})
    result = schedule(soc=70, known=known, constraints=[deadline(8)])
    assert len(result.blocks) == 2


def test_later_deadline_with_lower_target_is_ignored():
    with_trip = schedule(soc=60, constraints=[deadline(8, 90), deadline(10, 50)])
    assert with_trip == schedule(soc=60, constraints=[deadline(8, 90)])


def _tonight(now):
    # 9-10 Oct 2026: three hours a tenth of a cent apart, the cheapest at 05.
    start = datetime(2026, 10, 10, 0, 0, tzinfo=TZ)
    return hourly(start, [0.393263, 0.392002, 0.394221, 0.400503, 0.413489, 0.391698, 0.690464, 0.844427])


def test_near_equal_windows_do_not_move_the_plan():
    """A window a fraction of a cent cheaper at the next quarter must not move (or stop) the plan."""
    deadline_c = plan.Constraint(datetime(2026, 10, 10, 6, 45, tzinfo=TZ), 100.0)

    def plan_at(now, keep=None):
        timeline = plan.build_timeline(now, _tonight(now), now + timedelta(hours=9))
        data = plan.ScheduleInput(mode="smart", soc=75.0, target_soc=100.0, capacity_kwh=60, efficiency=1.0,
                                  power_kw=11, price_factor=1.0, timeline=timeline, constraints=(deadline_c,),
                                  keep_start=keep)
        return plan.build_schedule(data, now)
    evening = plan_at(datetime(2026, 10, 9, 22, 0, tzinfo=TZ))
    first = evening.blocks[0].start
    later = plan_at(datetime(2026, 10, 10, 0, 30, 28, tzinfo=TZ), keep=first)
    assert later.blocks[0].start == first, "the announced start is kept"
    running = datetime(2026, 10, 10, 0, 32, tzinfo=TZ)
    kept = plan_at(running, keep=running)
    assert kept.charge_now, "a charge already running is not stopped for a fraction of a cent"


def test_a_running_charge_is_not_stopped_when_the_cheap_hours_lie_together():
    """10 Oct 01:13, 88 %: the rest fitted into 05:15-05:55 (0.3917 kr) instead of going on now (0.3920 kr)."""
    now = datetime(2026, 10, 10, 1, 13, tzinfo=TZ)
    deadline_c = plan.Constraint(datetime(2026, 10, 10, 6, 45, tzinfo=TZ), 100.0)
    timeline = plan.build_timeline(now, _tonight(now), now + timedelta(hours=6))

    def plan_at(keep):
        data = plan.ScheduleInput(mode="smart", soc=88.0, target_soc=100.0, capacity_kwh=60, efficiency=1.0,
                                  power_kw=11, price_factor=1.0, timeline=timeline, constraints=(deadline_c,),
                                  keep_start=keep)
        return plan.build_schedule(data, now)

    assert not plan_at(None).charge_now, "without a running charge the cheapest hour later is fine"
    kept = plan_at(now)
    assert kept.charge_now, "a running charge goes on"
    assert kept.blocks[0].start == now


def quarterly(start: datetime, prices: list[float]) -> list:
    raw = [{"start": (start + timedelta(minutes=15 * q)).isoformat(),
            "end": (start + timedelta(minutes=15 * (q + 1))).isoformat(), "price": p} for q, p in enumerate(prices)]
    return plan.parse_price_attributes({"prices": raw})


def schedule_in(minutes: int, known, need_hours: float, constraints, now=NIGHT):
    data = plan.ScheduleInput(
        mode="smart", soc=50.0, target_soc=50.0 + need_hours * 12 / 60 * 100, capacity_kwh=60, efficiency=1.0,
        power_kw=12, price_factor=1.0, timeline=plan.build_timeline(now, known, now + timedelta(hours=10), minutes),
        constraints=tuple(constraints))
    return plan.build_schedule(data, now)


def test_an_hour_slot_has_the_mean_price_of_its_quarters():
    known = quarterly(NIGHT, [0.2, 0.2, 2.0, 2.0, 0.9, 0.9, 0.9, 0.9])
    hours = plan.build_timeline(NIGHT, known, NIGHT + timedelta(hours=2), 60)
    assert [(slot.start, slot.end - slot.start, round(slot.price, 3)) for slot in hours[:2]] == [
        (NIGHT, timedelta(hours=1), 1.1), (NIGHT + timedelta(hours=1), timedelta(hours=1), 0.9)]
    assert not hours[0].estimated


def test_quarters_or_whole_hours():
    # Half an hour of charging: the two cheap quarters at 22:00, or the cheapest whole hour (23:00, mean 0.9 < 1.1).
    known = quarterly(NIGHT, [0.2, 0.2, 2.0, 2.0, 0.9, 0.9, 0.9, 0.9] + [3.0] * 32)
    by_quarter = schedule_in(15, known, 0.5, [deadline(8, 60.0)])
    by_hour = schedule_in(60, known, 0.5, [deadline(8, 60.0)])
    assert [(b.start, b.end) for b in by_quarter.blocks] == [(NIGHT, NIGHT + timedelta(minutes=30))]
    assert [(b.start, b.end) for b in by_hour.blocks] == [
        (NIGHT + timedelta(hours=1), NIGHT + timedelta(hours=1, minutes=30))]


def test_an_hour_slot_is_cut_at_the_ready_by_time():
    # Cheapest at 06:00-07:00, ready by 06:45: the hour plan still uses 06:00-06:45.
    known = hourly(NIGHT, [3, 3, 3, 3, 3, 3, 3, 3, 0.1, 3])
    result = schedule_in(60, known, 0.5, [plan.Constraint(NIGHT + timedelta(hours=8, minutes=45), 60.0)])
    assert [(b.start, b.end) for b in result.blocks] == [
        (NIGHT + timedelta(hours=8), NIGHT + timedelta(hours=8, minutes=30))]


# Price limit on the cheapest plan (user, 2026-10-15: "kun lade hvis prisen er under X kr, eller hvis den er under en %
# grænse"). Prices from 22:00: 3, 2, 1, 0.5, 0.6, 2, 3, 3, 3, 3.
EXPENSIVE = [3, 2.5, 2.2, 2.1, 2.4, 2.6, 3, 3, 3, 3]


def test_price_limit_skips_a_night_above_the_limit():
    result = schedule(soc=49, known=hourly(NIGHT, EXPENSIVE), constraints=[deadline(8)], max_price=2.0, floor_soc=20)
    assert result.blocks == () and result.energy_kwh == 0
    assert result.limit_kwh == round(31 * 0.6, 2), "the energy to the target that was too expensive"
    assert result.shortfall_kwh == 0, "not a warning: left out on purpose"


def test_price_limit_still_charges_to_the_floor_in_the_cheapest_slots():
    result = schedule(soc=10, known=hourly(NIGHT, EXPENSIVE), constraints=[deadline(8)], max_price=2.0, floor_soc=20)
    assert result.energy_kwh == 6.0, "10 % → 20 % whatever the price"
    first, last = result.blocks[0], result.blocks[-1]
    assert first.start.astimezone(TZ).hour == 1 and last.end <= datetime(2026, 10, 10, 2, 0, tzinfo=TZ), \
        "in the cheapest hour (2.1)"
    assert round(result.limit_soc) == 20


def test_price_limit_charges_below_it_up_to_the_target():
    # 0.5 and 0.6 are below 1.0: two hours = 24 kWh, enough for 49 → 80 % (18.6 kWh).
    result = schedule(soc=49, constraints=[deadline(8)], max_price=1.0, floor_soc=20)
    assert result.energy_kwh == 18.6 and result.limit_kwh == 0
    assert all(slot.price <= 1.0 for slot in result.slots)
    # Only one cheap hour below 0.55: as far as it reaches, the rest is too expensive.
    partial = schedule(soc=49, constraints=[deadline(8)], max_price=0.55, floor_soc=20)
    assert partial.energy_kwh == 12.0 and partial.limit_kwh == round(18.6 - 12.0, 2)
    assert round(partial.limit_soc) == 69


def test_price_limit_does_not_hold_back_a_trip():
    trip = plan.Constraint(NIGHT + timedelta(hours=8), 80.0, firm=True)
    result = schedule(soc=49, known=hourly(NIGHT, EXPENSIVE), constraints=[trip], max_price=2.0, floor_soc=20)
    assert result.energy_kwh == 18.6 and result.limit_kwh == 0


def test_price_limit_only_in_the_cheapest_plan():
    result = schedule(mode="now", soc=49, known=hourly(NIGHT, EXPENSIVE), max_price=2.0, floor_soc=20)
    assert result.charge_now and result.energy_kwh > 0


def test_cheaper_later_finds_the_best_day_and_night_period_below_the_limit():
    # From 22:00: 3.0 all night, the next day 10-13 at 1.5, the night after 02-05 at 1.2 and 1.0 (hours 28-30).
    prices = [3.0] * 36
    for hour in (12, 13, 14):
        prices[hour] = 1.5
    prices[28], prices[29], prices[30] = 1.2, 1.0, 1.0
    timeline = plan.build_timeline(NIGHT, hourly(NIGHT, prices), NIGHT + timedelta(hours=36))
    after = NIGHT + timedelta(hours=8)  # 06:00
    found = plan.cheaper_later(timeline, after, 18.0, 12.0, 1.0, 2.0)  # 1.5 h of charging
    assert [(w.kind, w.start.astimezone(TZ).hour, w.end.astimezone(TZ).strftime("%H:%M"), w.price) for w in found] == [
        ("day", 10, "11:30", 1.5), ("night", 3, "04:30", 1.0)]
    assert plan.cheaper_later(timeline, after, 18.0, 12.0, 1.0, 1.1) == [found[1]], "only periods below the limit"
    assert plan.cheaper_later(timeline, after, 0.0, 12.0, 1.0, 2.0) == []


def test_cheaper_later_needs_an_unbroken_run_of_prices():
    timeline = plan.build_timeline(NIGHT, hourly(NIGHT, [3.0, 1.0]), NIGHT + timedelta(hours=2))
    assert plan.cheaper_later(timeline[:4] + timeline[5:], NIGHT, 20.0, 12.0, 1.0) == []


def test_a_running_charge_pauses_when_splitting_saves():
    # Charging since 22:00 (12 kWh = 4 quarters): 22:00-22:15 at 1.00, then 1.02 until 05:30-06:00 (the ready-by
    # time) at 1.00 again. Going on without a pause costs 1 % more, so the charge stops after 22:15.
    prices = [1.0 if i in (0, 30, 31, 32) else 1.02 for i in range(40)]
    known = plan.parse_price_attributes({"prices": [
        {"start": (NIGHT + timedelta(minutes=15 * i)).isoformat(),
         "end": (NIGHT + timedelta(minutes=15 * (i + 1))).isoformat(), "price": p} for i, p in enumerate(prices)]})
    data = plan.ScheduleInput(mode="smart", soc=60, target_soc=80, capacity_kwh=60, efficiency=1.0, power_kw=12,
                              price_factor=1.0, timeline=plan.build_timeline(NIGHT, known, NIGHT + timedelta(hours=10)),
                              constraints=(deadline(8),), keep_start=NIGHT)
    result = plan.build_schedule(data, NIGHT)
    local = [(b.start.astimezone(TZ).strftime("%H:%M"), b.end.astimezone(TZ).strftime("%H:%M")) for b in result.blocks]
    assert local == [("22:00", "22:15"), ("05:15", "06:00")], "the charge stops between the cheap quarters"


# -- the time the car takes to wake up (measured; 14 Oct 2026: ten minutes) ------------------------------------------


def local(blocks):
    return [(b.start.astimezone(TZ).strftime("%H:%M"), b.end.astimezone(TZ).strftime("%H:%M")) for b in blocks]


def test_waking_adds_time_to_a_period_so_the_target_is_still_reached():
    # 18 kWh = 1.5 h at 12 kW; with 10 minutes to wake the car the period is 10 minutes longer, and the energy the same
    plain = schedule(constraints=[deadline(8)])
    woken = schedule(constraints=[deadline(8)], wake_min=10)
    assert local(plain.blocks) == [("01:00", "02:30")]
    assert local(woken.blocks) == [("01:00", "02:40")], "one more quarter, the car draws power from 01:10"
    assert woken.energy_kwh == plain.energy_kwh == 18.0
    assert woken.cost > plain.cost, "the extra quarter costs (the waking itself costs nothing)"


def test_a_period_that_is_already_charging_loses_no_time():
    now = datetime(2026, 10, 10, 1, 0, tzinfo=TZ)
    running = schedule(now=now, constraints=[deadline(8)], wake_min=10, charging=True)
    asleep = schedule(now=now, constraints=[deadline(8)], wake_min=10)
    assert local(running.blocks)[0][0] == "01:00"
    assert running.blocks[-1].end < asleep.blocks[-1].end, "no waking time while it is charging already"


def test_short_periods_are_dropped_when_waking_eats_them():
    # Two single cheap quarters apart vs one hour slightly dearer: with a car that starts at once the split is cheapest,
    # with ten minutes of waking each quarter gives only five minutes – the single period is better.
    known = quarterly(NIGHT, [3] * 8 + [0.5] + [3] * 3 + [0.5] + [3] * 3 + [0.8] * 4 + [3] * 20)
    need = 0.5  # hours
    fast = schedule_wake(known, need, 0)
    slow = schedule_wake(known, need, 10)
    assert len(fast.blocks) == 2, local(fast.blocks)
    # (the later of equally cheap windows: the battery sits full for as short a time as possible)
    assert len(slow.blocks) == 1 and local(slow.blocks) == [("02:15", "02:55")], local(slow.blocks)


def test_a_little_missing_energy_makes_a_period_longer_instead_of_opening_a_new_one():
    # The cheap half hour then dear; a cheaper quarter far later. Five minutes of waking leave 1 kWh missing: the period
    # is made longer (a dear quarter next to it) rather than opening another period for those minutes.
    known = quarterly(NIGHT, [3] * 8 + [0.2, 0.2, 2.0, 2.0] + [3] * 4 + [0.9] + [3] * 30)
    result = schedule_wake(known, 0.5, 5)
    assert len(result.blocks) == 1 and local(result.blocks) == [("00:00", "00:35")], local(result.blocks)


def test_a_quarter_spent_waking_still_switches_the_charger_on():
    known = quarterly(NIGHT, [3] * 8 + [0.2] * 6 + [3] * 30)
    result = schedule_wake(known, 0.5, 20)
    first = result.slots[0]
    assert result.blocks[0].start == first.start, "the charger is on from the period's start"
    assert first.kwh == 0 and first.end == first.start + timedelta(minutes=15), "the whole quarter goes to waking"
    assert len(result.blocks) == 1 and result.blocks[0].end - result.blocks[0].start == timedelta(minutes=50)
    assert result.energy_kwh == 6.0


def schedule_wake(known, need_hours: float, wake_min: float):
    target = 50.0 + need_hours * 12 / 60 * 100
    data = plan.ScheduleInput(
        mode="smart", soc=50.0, target_soc=target, capacity_kwh=60, efficiency=1.0,
        power_kw=12, price_factor=1.0, timeline=plan.build_timeline(NIGHT, known, NIGHT + timedelta(hours=10)),
        constraints=(deadline(8, target),), wake_h=wake_min / 60)
    return plan.build_schedule(data, NIGHT)


def test_the_plan_is_done_a_margin_before_the_ready_by_time_when_it_can_be():
    # The cheapest quarters are the last ones before 06:00; with a margin of 15 minutes the plan avoids the last quarter
    # when the slots before can hold the target.
    known = quarterly(NIGHT, [3] * 28 + [1.0, 1.0, 0.5, 0.5] + [3] * 12)  # 05:00–06:00 cheap, the last half cheapest
    data = dict(mode="smart", soc=50.0, target_soc=60.0, capacity_kwh=60, efficiency=1.0, power_kw=12,
                price_factor=1.0, timeline=plan.build_timeline(NIGHT, known, NIGHT + timedelta(hours=10)),
                constraints=(deadline(8, 60.0),))
    plain = plan.build_schedule(plan.ScheduleInput(**data), NIGHT)
    early = plan.build_schedule(plan.ScheduleInput(**data, margin_h=0.25), NIGHT)
    assert local(plain.blocks)[-1][1] == "06:00"
    assert early.blocks[-1].end <= datetime(2026, 10, 10, 5, 45, tzinfo=TZ), local(early.blocks)
    assert early.energy_kwh == plain.energy_kwh


def test_the_margin_gives_way_when_the_time_is_short():
    # Only half an hour left before 06:00 and half an hour of charging needed: the margin is not kept, the car is
    # charged up to the ready-by time.
    now = datetime(2026, 10, 10, 5, 30, tzinfo=TZ)
    known = quarterly(NIGHT, [1.0] * 40)
    data = plan.ScheduleInput(mode="smart", soc=50.0, target_soc=60.0, capacity_kwh=60, efficiency=1.0, power_kw=12,
                              price_factor=1.0, timeline=plan.build_timeline(now, known, now + timedelta(hours=3)),
                              constraints=(deadline(8, 60.0),), margin_h=0.25)
    result = plan.build_schedule(data, now)
    assert result.shortfall_kwh == 0 and result.energy_kwh == 6.0
    assert result.blocks[-1].end == datetime(2026, 10, 10, 6, 0, tzinfo=TZ)
