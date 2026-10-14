"""Tests for the tyre sets: periods, use per set, the comparison at the same temperature, wear and the reminder."""

import importlib.util
import pathlib
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

_path = pathlib.Path(__file__).parents[1] / "custom_components" / "evledger" / "tyres.py"
_spec = importlib.util.spec_from_file_location("tyres", _path)
tyres = importlib.util.module_from_spec(_spec)
sys.modules["tyres"] = tyres
_spec.loader.exec_module(tyres)

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def trip(day: datetime, km: float, drop: float, temp: float, odo: float):
    return SimpleNamespace(started_at=day.isoformat(), ended_at=(day + timedelta(minutes=30)).isoformat(),
                           distance_km=km, start_battery_pct=80, end_battery_pct=80 - drop,
                           start_outside_temp_c=temp, start_odometer_km=odo, end_odometer_km=odo + km)


def charge(day: datetime, kwh: float, price: float | None):
    return SimpleNamespace(ended_at=day.isoformat(), kwh=kwh, price=price)


SUMMER = tyres.TyreSet(id="s", name="Sommer", kind="summer", width_mm=235, aspect_pct=45, rim_inch=18,
                       load_speed="98W", rim_name="Aero 18", rim_width_in=8.5, rim_offset_mm=40)
WINTER = tyres.TyreSet(id="w", name="Vinter", kind="winter", width_mm=235, aspect_pct=45, rim_inch=18)


def test_size_and_rim_as_text():
    assert SUMMER.size == "235/45 R18 98W"
    assert SUMMER.rim == "Aero 18 · 8.5J ET40"
    assert tyres.TyreSet(id="x", name="x").size is None
    assert tyres.TyreSet(id="x", name="x", rim_inch=17).rim == '17"'


def test_periods_and_active_set():
    changes = [tyres.TyreChange("c2", "w", "2026-10-01T10:00:00+00:00", 2000),
               tyres.TyreChange("c1", "s", "2026-04-01T10:00:00+00:00", 1000)]
    parts = tyres.periods(changes, 2500)
    assert [(p.set_id, p.km) for p in parts] == [("s", 1000), ("w", 500)]
    assert tyres.active_set(changes) == "w"
    assert tyres.active_set(changes, datetime(2026, 6, 1, tzinfo=UTC)) == "s"
    assert tyres.active_set(changes, datetime(2026, 1, 1, tzinfo=UTC)) is None


def test_odometer_from_the_trips():
    trips = [trip(datetime(2026, 10, 1, 8, tzinfo=UTC), 10, 2, 8, 2000),
             trip(datetime(2026, 10, 2, 8, tzinfo=UTC), 10, 2, 8, 2010)]
    assert tyres.odometer_at(datetime(2026, 10, 1, 20, tzinfo=UTC), trips, 3000, NOW) == 2010  # after trip 1
    assert tyres.odometer_at(datetime(2026, 10, 2, 8, 10, tzinfo=UTC), trips, 3000, NOW) == 2010  # inside trip 2
    assert tyres.odometer_at(datetime(2026, 9, 30, tzinfo=UTC), trips, 3000, NOW) == 2000  # just before the first
    assert tyres.odometer_at(datetime(2026, 1, 1, tzinfo=UTC), trips, 3000, NOW) is None  # long before: unknown
    assert tyres.odometer_at(NOW, trips, 3000, NOW) == 3000


def test_use_per_set_and_the_same_temperature_comparison():
    changes = [tyres.TyreChange("c1", "s", "2026-04-01T00:00:00+00:00", 1000),
               tyres.TyreChange("c2", "w", "2026-10-01T00:00:00+00:00", 2000)]
    trips = []
    # Summer: 5–10 °C at 150 Wh/km, 15–20 °C at 120 Wh/km. Winter: 5–10 °C at 165 Wh/km, 0–5 °C at 200 Wh/km.
    for i in range(10):
        trips.append(trip(datetime(2026, 4, 10 + i, tzinfo=UTC), 20, 20 * 0.150 / 60 * 100, 8, 1000 + i * 20))
        trips.append(trip(datetime(2026, 7, 1 + i, tzinfo=UTC), 20, 20 * 0.120 / 60 * 100, 17, 1500 + i * 20))
        trips.append(trip(datetime(2026, 10, 2 + i, tzinfo=UTC), 20, 20 * 0.165 / 60 * 100, 7, 2000 + i * 20))
        trips.append(trip(datetime(2026, 10, 2 + i, 15, tzinfo=UTC), 20, 20 * 0.200 / 60 * 100, 2, 2010 + i * 20))
    charges = [charge(datetime(2026, 5, 1, tzinfo=UTC), 150, 300.0), charge(datetime(2026, 10, 5, tzinfo=UTC), 90, 180)]
    out = tyres.stats([SUMMER, WINTER], changes, trips, charges, 60, 2400, NOW)
    summer, winter = out["sets"]
    assert summer["wh_per_km"] == 135 and winter["wh_per_km"] == 182
    assert summer["km"] == 1000 and winter["km"] == 400 and winter["mounted"] and not summer["mounted"]
    assert summer["bands"]["5–10"]["wh_per_km"] == 150 and winter["bands"]["0–5"]["wh_per_km"] == 200
    assert summer["charged_kwh_per_100_km"] == 15.0 and summer["cost_per_km"] == 0.3
    assert winter["charged_kwh_per_100_km"] == 22.5 and winter["cost_per_km"] == 0.45
    # Only the band both drove in counts: 165 against 150 is +10 %, not the +36 % of the plain averages.
    compared = out["comparison"]["sets"][0]
    assert out["comparison"]["reference"] == "Sommer"
    assert compared["bands"] == ["5–10"] and compared["difference_pct"] == 10.0
    assert [s["set"] for s in out["seasons"]] == ["Vinter", "Sommer"] and out["seasons"][0]["to"] is None


def test_a_trip_with_a_charge_on_the_way_is_left_out():
    t = trip(datetime(2026, 10, 2, tzinfo=UTC), 20, -10, 8, 2000)
    assert tyres.trip_energy_kwh(t, 60) is None
    assert tyres.trip_energy_kwh(trip(datetime(2026, 10, 2, tzinfo=UTC), 2, 0, 8, 2000), 60) == 0


def test_wear_counts_only_the_sets_own_km():
    tyre = tyres.TyreSet(id="w", name="Vinter", kind="winter", treads=[
        {"id": "a", "at": "2025-11-01T00:00:00+00:00", "mm": 8.0, "odometer_km": 10000},
        {"id": "b", "at": "2026-10-01T00:00:00+00:00", "mm": 7.0, "odometer_km": 20000}])
    on = [tyres.Period("w", "c1", datetime(2025, 11, 1, tzinfo=UTC), datetime(2026, 4, 1, tzinfo=UTC), 10000, 15000)]
    out = tyres._tread_rate(tyre, on)
    assert out["wear_mm_per_1000_km"] == 0.2, "1 mm over the 5000 km it was on, not the car's 10000"
    assert out["km_left"] == 15000 and out["tread_min_mm"] == 4.0


def test_reminder_by_the_seven_degree_rule():
    cold = [(NOW - timedelta(days=d), 4.0) for d in range(4)]
    tip = tyres.suggestion(SUMMER, [SUMMER, WINTER], cold, NOW)
    assert tip["to"] == "winter" and tip["set"] == "Vinter" and tip["key"] == "winter-2026"
    assert tyres.suggestion(SUMMER, [SUMMER, WINTER], [(NOW, 12.0)] * 4, NOW) is None
    assert tyres.suggestion(SUMMER, [SUMMER, WINTER], cold[:2], NOW) is None, "too few readings"
    spring = datetime(2027, 4, 20, 12, tzinfo=UTC)
    warm = [(spring - timedelta(days=d), 13.0) for d in range(4)]
    tip = tyres.suggestion(WINTER, [SUMMER, WINTER], warm, spring)
    assert tip["to"] == "summer" and tip["key"] == "summer-2026"
    assert tyres.suggestion(tyres.TyreSet(id="a", name="Helår", kind="all_season"), [], cold, NOW) is None


def test_studs_must_be_off_after_15_april():
    studs = tyres.TyreSet(id="p", name="Pigdæk", kind="studded")
    tip = tyres.suggestion(studs, [SUMMER, studs], [], datetime(2027, 4, 16, 12, tzinfo=UTC))
    assert tip["to"] == "summer" and tip["urgent"] and tip["set"] == "Sommer"
    assert tyres.suggestion(studs, [SUMMER, studs], [], datetime(2027, 4, 15, 12, tzinfo=UTC)) is None
