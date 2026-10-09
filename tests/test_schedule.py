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
        cap_override=kw.get("cap_override", True))
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
    assert result.blocks[0].start.hour == 1  # 01:00 was 0.1 yesterday


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
    assert [(b.start.hour, b.end.hour) for b in result.blocks] == [(1, 2)]
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
    assert result.blocks[0].start.hour in (0, 1)  # cheap night slots are added before the trip


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
    # two separate quarters at 1.00, the rest 1.02 -> one block is nearly as cheap
    known = plan.parse_price_attributes({"prices": [
        {"start": (NIGHT + timedelta(minutes=15 * i)).isoformat(),
         "end": (NIGHT + timedelta(minutes=15 * (i + 1))).isoformat(), "price": 1.0 if i in (4, 30) else 1.02}
        for i in range(40)]})
    result = schedule(soc=74, known=known, constraints=[deadline(8)])
    assert len(result.blocks) == 1


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
