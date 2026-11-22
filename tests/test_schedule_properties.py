"""Properties of the charge schedule that must hold for any input (Hypothesis): random prices (also negative), battery
levels, targets, power, efficiency, modes, ready-by times, price limits, the car's wake-up time, the ready-ahead margin
and a charge running now. The schedule never fails and is always sane – the periods have a length and do not overlap,
it never charges more than needed, it meets every ready-by time it does not report short, its price adds up, no quarter
gets more than the charger can give, and a price limit is only passed for the floor."""

import math
from datetime import datetime, timedelta, timezone

from _load import load
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

plan = load("plan")

TZ = timezone(timedelta(hours=2))
BASE = datetime(2026, 10, 9, 22, 0, tzinfo=TZ)
SLACK = 1e-6


@st.composite
def cases(draw):
    count = draw(st.integers(min_value=8, max_value=110))
    prices = draw(st.lists(st.floats(min_value=-0.5, max_value=6.0, allow_nan=False), min_size=count, max_size=count))
    raw = [{"start": (BASE + timedelta(minutes=15 * i)).isoformat(),
            "end": (BASE + timedelta(minutes=15 * (i + 1))).isoformat(), "price": round(p, 4)}
           for i, p in enumerate(prices)]
    now = BASE + timedelta(minutes=draw(st.integers(min_value=0, max_value=120)), seconds=draw(st.integers(0, 59)))
    horizon_end = BASE + timedelta(minutes=15 * count)
    mode = draw(st.sampled_from(["smart", "smart", "smart", "fixed", "now", "price_cap"]))
    soc = draw(st.floats(min_value=0, max_value=100))
    target = draw(st.floats(min_value=0, max_value=100))
    constraints = []
    for _ in range(draw(st.integers(min_value=0, max_value=2))):
        minutes = draw(st.integers(min_value=15, max_value=15 * count))
        constraints.append(plan.Constraint(BASE + timedelta(minutes=minutes), draw(st.floats(0, 100)),
                                           draw(st.booleans())))
    window = None
    if mode == "fixed":
        a = draw(st.integers(min_value=0, max_value=15 * count - 15))
        b = draw(st.integers(min_value=a + 15, max_value=15 * count))
        window = (BASE + timedelta(minutes=a), BASE + timedelta(minutes=b))
    limited = mode == "smart" and draw(st.booleans())
    return dict(
        raw=raw, now=now, horizon=horizon_end, mode=mode, soc=soc, target=target, constraints=tuple(constraints),
        window=window, capacity=draw(st.floats(min_value=20, max_value=110)),
        efficiency=draw(st.floats(min_value=0.7, max_value=1.0)), power=draw(st.floats(min_value=1.4, max_value=22)),
        factor=draw(st.floats(min_value=0.5, max_value=1.5)),
        price_cap=draw(st.one_of(st.none(), st.floats(min_value=-0.5, max_value=5))) if mode == "price_cap" else None,
        min_soc=draw(st.one_of(st.none(), st.floats(min_value=0, max_value=60))),
        max_price=draw(st.floats(min_value=-0.5, max_value=5)) if limited else None,
        floor=draw(st.floats(min_value=0, max_value=60)) if limited else None,
        wake_min=draw(st.sampled_from([0, 0, 0.5, 3, 5, 10, 20])),
        margin_min=draw(st.sampled_from([0, 0, 15, 30, 60])),
        charging=draw(st.booleans()),
    )


def build(case):
    known = plan.parse_price_attributes({"prices": case["raw"]})
    data = plan.ScheduleInput(
        mode=case["mode"], soc=case["soc"], target_soc=case["target"], capacity_kwh=case["capacity"],
        efficiency=case["efficiency"], power_kw=case["power"], price_factor=case["factor"],
        timeline=plan.build_timeline(case["now"], known, case["horizon"]), constraints=case["constraints"],
        window=case["window"], price_cap=case["price_cap"], min_soc=case["min_soc"],
        max_price=case["max_price"], floor_soc=case["floor"], wake_h=case["wake_min"] / 60,
        margin_h=case["margin_min"] / 60, charging=case["charging"])
    return data, plan.build_schedule(data, case["now"])


def wall(case, level):
    return max(level - case["soc"], 0.0) * case["capacity"] / 100 / case["efficiency"]


@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(cases())
def test_the_schedule_is_always_sane(case):
    data, result = build(case)
    now = case["now"]
    # the periods: a length, in order, apart
    for block in result.blocks:
        assert block.end > block.start, result.blocks
        assert block.kwh >= -SLACK
    for a, b in zip(result.blocks, result.blocks[1:], strict=False):
        assert a.end < b.start, result.blocks
    # never more than needed, and only what the slots can give
    levels = [case["target"], *(c.target_soc for c in case["constraints"])]
    if case["min_soc"] is not None:
        levels.append(case["min_soc"])
    if case["floor"] is not None:
        levels.append(case["floor"])
    assert result.energy_kwh <= wall(case, max(levels)) + 0.02
    for slot in result.slots:
        assert slot.kwh >= -SLACK
        assert slot.start >= now - timedelta(seconds=1) and slot.end <= case["horizon"] + timedelta(seconds=1)
        hours = (slot.end - slot.start).total_seconds() / 3600
        assert slot.kwh <= case["power"] * hours + 1e-6, (slot, case["power"])
    # the price adds up
    cost = sum(slot.kwh * slot.price * case["factor"] for slot in result.slots)
    assert result.cost is None or abs(result.cost - round(cost, 2)) <= 0.02 + 1e-9
    # every ready-by time is met when the plan does not report a shortfall (smart, no price limit)
    if case["mode"] == "smart" and case["max_price"] is None and result.shortfall_kwh == 0 and not case["window"]:
        for constraint in case["constraints"]:
            delivered = sum(slot.kwh for slot in result.slots if slot.end <= constraint.deadline + timedelta(seconds=1))
            assert delivered >= wall(case, constraint.target_soc) - 0.05, (constraint, delivered)
    # a single ready-by time that the slots before it can hold (one wake-up included) is never reported short
    if case["mode"] == "smart" and case["max_price"] is None and len(case["constraints"]) == 1:
        constraint = case["constraints"][0]
        need = wall(case, max(constraint.target_soc, 0.0))
        room = sum(min(case["power"] * (slot.end - max(slot.start, now)).total_seconds() / 3600,
                       case["power"] * (slot.end - slot.start).total_seconds() / 3600)
                   for slot in data.timeline if slot.end <= constraint.deadline and slot.end > now)
        if room >= need + case["power"] * case["wake_min"] / 60 * 2 + 0.05:
            assert result.shortfall_kwh == 0, (room, need, result.shortfall_kwh)
    # a price limit is only passed for the floor
    if case["max_price"] is not None and case["constraints"]:
        dear = sum(slot.kwh for slot in result.slots
                   if slot.price * case["factor"] > case["max_price"] + 1e-9 or slot.estimated)
        firm = any(c.firm for c in case["constraints"])
        if not firm:
            floor = min(case["floor"], max(c.target_soc for c in case["constraints"]))
            assert dear <= wall(case, floor) + 0.02, (dear, floor)
    # charge now really charges now
    if case["mode"] == "now" and result.energy_kwh > 0:
        assert result.charge_now
        assert result.blocks[0].start <= now + timedelta(minutes=15)


TIMES = [(BASE + timedelta(minutes=15 * i)).isoformat() for i in range(-4, 12)] + [
    "2026-10-25T02:30:00+02:00", "2026-10-25T02:30:00+01:00", "2026-10-09T22:00:00", "2026-10-09T22:00:00Z",
    "2100-01-01T00:00:00+00:00", "0001-01-01T00:00:00+00:00", "x"]
VALUES = st.one_of(st.none(), st.text(max_size=25), st.sampled_from(TIMES), st.integers(),
                   st.floats(allow_nan=True, allow_infinity=True),
                   st.sampled_from(["inf", "-inf", "nan", "1e400", "2,5"]))


@settings(max_examples=1500, deadline=None)
@given(st.lists(st.one_of(
    st.none(), st.text(max_size=8), st.integers(), st.floats(allow_nan=True, allow_infinity=True),
    st.dictionaries(st.sampled_from(["start", "end", "price", "value", "hour", "time"]), VALUES, max_size=4)),
    max_size=30))
def test_price_lists_of_any_shape_never_break_the_parser(items):
    slots = plan.parse_price_attributes({"prices": items})
    assert len(slots) <= 30 * 96, "no price makes more than a day of quarters"
    for slot in slots:
        assert slot.start.tzinfo is not None and slot.end > slot.start
        assert math.isfinite(slot.price), slot  # neither NaN nor infinite
    for a, b in zip(slots, slots[1:], strict=False):
        assert a.start < b.start
