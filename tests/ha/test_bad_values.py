"""Broken numbers from other integrations – "nan", "inf", text, levels out of range, prices of infinity – never make
the plan fail, never show up as "nan" on an entity and never start a charge on their own."""

import logging
import math
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .test_smart_charge import SWITCH, car_with_limit, later, setup

BAD = ["nan", "NaN", "inf", "-inf", "Infinity", "1e400", "abc", "", "150", "-5"]


def broken_numbers(hass: HomeAssistant) -> list[str]:
    found = []
    for item in hass.states.async_all():
        if not item.entity_id.split(".", 1)[1].startswith("bil_"):
            continue
        values = [("state", item.state), *item.attributes.items()]
        for name, value in values:
            if isinstance(value, float) and not math.isfinite(value):
                found.append(f"{item.entity_id} {name}={value}")
            elif isinstance(value, str) and value.lower() in ("nan", "inf", "-inf", "infinity", "-infinity"):
                found.append(f"{item.entity_id} {name}={value}")
    return found


def errors(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records
            if record.levelno >= logging.ERROR and "evledger" in record.name]


@pytest.mark.parametrize("value", BAD)
async def test_a_broken_battery_level(hass: HomeAssistant, request, freezer, caplog, value):
    _, calls = await setup(hass, request, soc=value, cheap_now=False)
    await later(hass, freezer, 5)
    hass.states.async_set("sensor.car_battery", value, {"unit_of_measurement": "%"})
    await later(hass, freezer, 15)
    assert not errors(caplog)
    assert not broken_numbers(hass)
    status = hass.states.get("sensor.bil_charge_status").state
    if value not in ("150",):
        assert status != "target_reached", "a level that cannot be read is not a full battery"


@pytest.mark.parametrize("value", BAD)
async def test_a_broken_charger_power(hass: HomeAssistant, request, freezer, caplog, value):
    _, calls = await setup(hass, request, charger_state="connected_charging", cheap_now=True)
    hass.states.async_set("sensor.charger_power", value, {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.charger_session", value, {"unit_of_measurement": "kWh"})
    await later(hass, freezer, 15)
    assert not errors(caplog)
    assert not broken_numbers(hass)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "abc", ""])
async def test_a_broken_price(hass: HomeAssistant, request, freezer, caplog, value):
    """The price now and the prices in the list can be broken: those quarters are skipped, never cheap."""
    _, calls = await setup(hass, request, cheap_now=False)
    start = dt_util.now().replace(minute=0, second=0, microsecond=0)
    listing = [{"start": (start + timedelta(hours=h)).isoformat(), "end": (start + timedelta(hours=h + 1)).isoformat(),
                "price": value if h < 3 else 2.0} for h in range(24)]
    hass.states.async_set("sensor.price", value, {"prices": listing, "unit_of_measurement": "kr/kWh"})
    await later(hass, freezer, 15)
    assert not errors(caplog)
    assert not broken_numbers(hass)
    assert SWITCH not in calls["switch.turn_on"], "a quarter of unknown price is never chosen as the cheapest"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "abc", "", "1e400"])
async def test_a_broken_charge_limit_range_and_position(hass: HomeAssistant, request, freezer, caplog, value):
    """The car's charge limit, its range and its position can be broken too: the limit is then not known (the plan
    keeps its own target), and a setting of the target never sends a broken number to the car."""
    car_with_limit(hass, 70)
    hass.states.async_set("number.car_charge_limit", value, {"unit_of_measurement": "%", "min": value, "max": value})
    hass.states.async_set("sensor.car_range", value, {"unit_of_measurement": "km"})
    sent = []

    async def fake_set(call):
        sent.append(dict(call.data))

    _, calls = await setup(hass, request, cheap_now=False)
    hass.services.async_register("number", "set_value", fake_set)
    hass.states.async_set("device_tracker.car", "home", {"latitude": value, "longitude": value})
    await later(hass, freezer, 5)
    target = hass.data["entity_components"]["number"].get_entity("number.bil_target_soc")
    await target.async_set_native_value(85)
    await hass.async_block_till_done(wait_background_tasks=True)
    await later(hass, freezer, 5)
    assert not errors(caplog)
    assert not broken_numbers(hass)
    assert "car_limit" not in hass.states.get("select.bil_charge_mode").attributes or \
        hass.states.get("select.bil_charge_mode").attributes["car_limit"] is None
    assert float(hass.states.get("sensor.bil_plan_target_soc").state) == 85
    for data in sent:
        assert math.isfinite(float(data["value"])) and 50 <= data["value"] <= 100, data


@pytest.mark.parametrize("value", [float("inf"), float("nan"), "50", True])
async def test_a_broken_battery_level_from_before_a_restart(hass: HomeAssistant, request, caplog, value):
    from homeassistant.core import State

    from .test_restart import restore

    restore(hass, [State("select.bil_charge_mode", "smart", {"last_soc": value})])
    entry, _ = await setup(hass, request, soc="unavailable", cheap_now=False)
    assert not errors(caplog)
    assert not broken_numbers(hass)
    assert entry.runtime_data.last_soc == (50.0 if value == "50" else None)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
async def test_numbers_and_services_turn_away_nan_and_infinity(hass: HomeAssistant, request, freezer, caplog, value):
    """number.set_value on every EV Ledger number, and the ledger's own services, with a value that is no number."""
    import voluptuous as vol
    from homeassistant.exceptions import HomeAssistantError

    from .test_smart_charge import DOMAIN

    entry, _ = await setup(hass, request, cheap_now=False)
    numbers = [item.entity_id for item in hass.states.async_all("number") if item.entity_id.startswith("number.bil_")]
    assert len(numbers) > 10
    before = {entity_id: hass.states.get(entity_id).state for entity_id in numbers}
    for entity_id in numbers:
        try:
            await hass.services.async_call("number", "set_value", {"entity_id": entity_id, "value": value},
                                           blocking=True)
        except (HomeAssistantError, vol.Invalid, ValueError):
            pass
    await later(hass, freezer, 2)
    for service, data in (("log_public_charge", {"kwh": value, "price": 10}),
                          ("log_public_charge", {"kwh": 10, "price": value}),
                          ("add_charge", {"kwh": value, "started_at": dt_util.now().isoformat()})):
        with pytest.raises((vol.Invalid, HomeAssistantError)):
            await hass.services.async_call(DOMAIN, service, {"entry_id": entry.entry_id, **data}, blocking=True)
    await later(hass, freezer, 2)
    assert not errors(caplog)
    assert not broken_numbers(hass)
    assert {entity_id: hass.states.get(entity_id).state for entity_id in numbers} == before
