"""Tests for the trip energy calculation."""

from _load import load

trip = load("trip")


def test_round_trip_energy_includes_margin():
    assert round(trip.trip_energy_kwh(100, True, 180, 15), 2) == 41.4
    assert round(trip.trip_energy_kwh(100, False, 180, 0), 2) == 18.0


def test_target_soc_adds_reserve_and_can_exceed_100():
    assert round(trip.trip_target_soc(41.4, 57.5, 10), 1) == 82.0
    assert trip.trip_target_soc(80, 57.5, 10) > 100


def test_coordinates_and_distance():
    assert trip.parse_coordinates("55.68, 12.57") == (55.68, 12.57)
    assert trip.parse_coordinates("Rådhuspladsen 1, København") is None
    assert trip.parse_coordinates("200, 9") is None
    assert round(trip.haversine_km(55.6761, 12.5683, 56.1629, 10.2039)) == 157  # Copenhagen - Aarhus
