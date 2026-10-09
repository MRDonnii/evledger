"""Tests for the Tesla model catalog."""

from _load import load

vehicles = load("vehicles")


def test_keys_are_unique_and_values_sane():
    assert len(vehicles.BY_KEY) == len(vehicles.VEHICLES)
    for vehicle in vehicles.VEHICLES:
        assert 40 <= vehicle.capacity_kwh <= 130
        assert 120 <= vehicle.consumption_wh_km <= 320
        assert vehicle.name.startswith(vehicle.family)


def test_family_from_device_model():
    assert vehicles.family_of("Model 3") == "Model 3"
    assert vehicles.family_of("Tesla Model Y") == "Model Y"
    assert vehicles.family_of("modely") == "Model Y"
    assert vehicles.family_of("Cybertruck") == "Cybertruck"
    assert vehicles.family_of("ID.4") is None


def test_guess_variant_from_range():
    # 307.6 km at 75 % -> 410 km at 100 %: a somewhat aged Model 3 RWD
    assert vehicles.guess("Model 3", 307.6, 75).key == "model_3_rwd"
    assert vehicles.guess("Model 3", 440, 80).key == "model_3_lr"
    assert vehicles.guess("Model Y", 250, 50).key == "model_y_juniper_lr"
    assert vehicles.guess("Model 3").family == "Model 3"
    assert vehicles.guess("Leaf", 200, 80) is None
