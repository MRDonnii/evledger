"""Tests for the pure charge plan calculation."""

import importlib.util
import pathlib
import sys
from datetime import datetime, time, timedelta, timezone

_path = pathlib.Path(__file__).parents[1] / "custom_components" / "evledger" / "smart" / "plan.py"
_spec = importlib.util.spec_from_file_location("plan", _path)
plan = importlib.util.module_from_spec(_spec)
sys.modules["plan"] = plan
_spec.loader.exec_module(plan)

TZ = timezone(timedelta(hours=2))


def hourly(day: int, prices: list[float]) -> list[dict]:
    base = datetime(2026, 9, day, tzinfo=TZ)
    return [{"start": (base + timedelta(hours=h)).isoformat(),
             "end": (base + timedelta(hours=h + 1)).isoformat(), "price": p}
            for h, p in enumerate(prices)]


def make(soc=50.0, target=80.0, deadline=None, slots=None, **kw):
    return plan.PlanInput(soc=soc, target_soc=target, capacity_kwh=kw.get("capacity", 60),
                          efficiency=kw.get("efficiency", 1.0), power_kw=kw.get("power", 11),
                          price_factor=kw.get("factor", 1.0), deadline=deadline, slots=slots or [])


def test_parse_splits_hours_into_quarter_slots():
    slots = plan.parse_price_attributes({"prices": hourly(26, [1.0, 2.0])})
    assert len(slots) == 8
    assert slots[4].price == 2.0 and slots[4].start.hour == 1


def test_parse_nordpool_raw_without_end_and_value_key():
    base = datetime(2026, 9, 26, tzinfo=TZ)
    raw = [{"start": (base + timedelta(minutes=15 * i)).isoformat(), "value": i} for i in range(3)]
    slots = plan.parse_price_attributes({"raw_today": raw})
    assert [s.price for s in slots] == [0, 1, 2]
    assert slots[1].end - slots[1].start == timedelta(minutes=15)


def test_parse_ignores_garbage():
    junk = {"prices": [1, {"start": "x", "price": 1}], "today": [1]}
    assert plan.parse_price_attributes(junk) == []


def test_cheapest_window_before_deadline():
    prices = [3.0] * 24
    prices[2] = prices[3] = 0.5
    prices[5] = 0.1  # after the deadline
    slots = plan.parse_price_attributes({"prices": hourly(27, prices)})
    now = datetime(2026, 9, 26, 22, 0, tzinfo=TZ)
    deadline = datetime(2026, 9, 27, 5, 0, tzinfo=TZ)
    # 30 % of 60 kWh = 18 kWh at 11 kW -> 98 min -> 7 slots
    result = plan.calculate(make(deadline=deadline, slots=slots), now)
    assert result.missing_wall_kwh == 18.0
    assert result.minutes_needed == 98
    assert result.start.hour == 2
    assert result.end == result.start + timedelta(minutes=98.18181818181819)
    assert 0 < result.price < 18 * 3


def test_target_reached_gives_no_window():
    result = plan.calculate(make(soc=85, deadline=datetime(2026, 9, 27, 6, tzinfo=TZ)),
                            datetime(2026, 9, 26, 22, tzinfo=TZ))
    assert result.missing_wall_kwh == 0 and result.start is None


def test_not_enough_prices():
    slots = plan.parse_price_attributes({"prices": hourly(27, [1.0])})
    result = plan.calculate(make(deadline=datetime(2026, 9, 27, 6, tzinfo=TZ), slots=slots),
                            datetime(2026, 9, 26, 22, tzinfo=TZ))
    assert result.start is None and result.missing_wall_kwh == 18.0


def test_unknown_battery():
    assert plan.calculate(make(soc=None), datetime.now(TZ)).missing_wall_kwh is None


def test_next_deadline_rolls_to_tomorrow():
    now = datetime(2026, 9, 26, 19, 0, tzinfo=TZ)
    assert plan.next_deadline(now, time(6, 45)) == datetime(2026, 9, 27, 6, 45, tzinfo=TZ)
    assert plan.next_deadline(now, time(21, 0)) == datetime(2026, 9, 26, 21, 0, tzinfo=TZ)


def test_next_deadline_weekend_time():
    friday = datetime(2026, 10, 9, 22, 0, tzinfo=TZ)
    assert plan.next_deadline(friday, time(7, 0), time(10, 0)) == datetime(2026, 10, 10, 10, 0, tzinfo=TZ)
    assert plan.next_deadline(friday, time(7, 0)) == datetime(2026, 10, 10, 7, 0, tzinfo=TZ), "no weekend time"
    saturday_morning = datetime(2026, 10, 10, 8, 0, tzinfo=TZ)
    assert plan.next_deadline(saturday_morning, time(7, 0), time(10, 0)) == datetime(2026, 10, 10, 10, 0, tzinfo=TZ)
    sunday_evening = datetime(2026, 10, 11, 22, 0, tzinfo=TZ)
    assert plan.next_deadline(sunday_evening, time(7, 0), time(10, 0)) == datetime(2026, 10, 12, 7, 0, tzinfo=TZ)


def test_matches_reference_template_sensors():
    """Same inputs as the original template sensors: 95 % -> 100 %, 57.5 kWh, 90 %, 11 kW, factor 0.75."""
    tomorrow = [1.973915, 1.929249, 1.864633, 1.818775, 1.773315, 1.746239, 1.805191, 1.7256]
    slots = plan.parse_price_attributes({"prices": hourly(27, tomorrow)})
    now = datetime(2026, 9, 26, 19, 20, tzinfo=TZ)
    data = plan.PlanInput(95, 100, 57.5, 0.9, 11, 0.75, plan.next_deadline(now, time(6, 45)), slots)
    result = plan.calculate(data, now)
    assert (result.missing_wall_kwh, result.minutes_needed, result.price) == (3.19, 17, 4.32)
    assert result.start == datetime(2026, 9, 27, 6, 15, tzinfo=TZ)
