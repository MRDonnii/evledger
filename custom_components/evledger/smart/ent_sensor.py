"""Charge plan sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfLength
from homeassistant.util import dt as dt_util

from .const import STATUSES
from .entity import EvSmartChargeListenerEntity
from .planner import ChargePlanner

NO_PLAN = "—"


def _clock(value) -> str:
    return dt_util.as_local(value).strftime("%H:%M") if value else NO_PLAN


def _next_block(planner: ChargePlanner):
    return planner.schedule.next_block(dt_util.now())


def _route(planner: ChargePlanner, field: str):
    return getattr(planner.trip.route, field) if planner.trip.route else None


@dataclass(frozen=True, kw_only=True)
class PlanSensorDescription(SensorEntityDescription):
    value: Callable[[ChargePlanner], Any]


SENSORS: tuple[PlanSensorDescription, ...] = (
    PlanSensorDescription(key="charge_status", device_class=SensorDeviceClass.ENUM, options=STATUSES,
                          icon="mdi:ev-plug-type2", value=lambda p: p.status),
    PlanSensorDescription(key="next_charge_start", device_class=SensorDeviceClass.TIMESTAMP,
                          icon="mdi:clock-start",
                          value=lambda p: (block := _next_block(p)) and block.start),
    PlanSensorDescription(key="next_charge_end", device_class=SensorDeviceClass.TIMESTAMP,
                          icon="mdi:clock-end",
                          value=lambda p: (block := _next_block(p)) and block.end),
    PlanSensorDescription(key="planned_cost", device_class=SensorDeviceClass.MONETARY,
                          icon="mdi:cash-clock", suggested_display_precision=2,
                          value=lambda p: p.schedule.cost),
    PlanSensorDescription(key="planned_energy", device_class=SensorDeviceClass.ENERGY,
                          native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                          suggested_display_precision=1, icon="mdi:lightning-bolt",
                          value=lambda p: p.schedule.energy_kwh),
    PlanSensorDescription(key="plan_target_soc", native_unit_of_measurement=PERCENTAGE,
                          suggested_display_precision=0, icon="mdi:battery-charging-high",
                          value=lambda p: p.schedule.target_soc),
    PlanSensorDescription(key="trip_distance", device_class=SensorDeviceClass.DISTANCE,
                          native_unit_of_measurement=UnitOfLength.KILOMETERS,
                          suggested_display_precision=0, icon="mdi:map-marker-distance",
                          value=lambda p: _route(p, "distance_km")),
    PlanSensorDescription(key="trip_energy", device_class=SensorDeviceClass.ENERGY,
                          native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                          suggested_display_precision=1, icon="mdi:car-electric",
                          value=lambda p: p.trip_energy_kwh),
    PlanSensorDescription(key="trip_target_soc", native_unit_of_measurement=PERCENTAGE,
                          suggested_display_precision=0, icon="mdi:battery-arrow-up-outline",
                          value=lambda p: p.trip_target_soc),
)


def build(planner) -> list:
    return list(PlanSensor(planner, description) for description in SENSORS)


class PlanSensor(EvSmartChargeListenerEntity, SensorEntity):
    entity_description: PlanSensorDescription

    def __init__(self, planner: ChargePlanner, description: PlanSensorDescription) -> None:
        super().__init__(planner, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.entity_description.value(self.planner)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.key in ("best_charge_price", "planned_cost"):
            return self.planner.price_unit or "kr"
        return self.entity_description.native_unit_of_measurement

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key = self.entity_description.key
        planner = self.planner
        if key == "best_charge_start":
            return {"deadline": planner.deadline, "price_slots": planner.slot_count}
        if key == "charge_status":
            return {
                "mode": planner.mode,
                "charger_state": planner.charger_state if planner.controls_charger else None,
                "controls_charger": planner.controls_charger,
                "car_plugged_in": planner.car_present if planner.controls_charger else None,
                "trip_active": planner.trip_active,
                "vehicle_model": planner.vehicle.key if planner.vehicle else None,
                "vehicle_name": planner.vehicle.name if planner.vehicle else None,
                "vehicle_body": planner.vehicle.body if planner.vehicle else None,
                "battery_capacity_kwh": planner.capacity,
            }
        if key == "next_charge_start":
            schedule = planner.schedule
            return {
                "mode": planner.mode,
                "charge_now": schedule.charge_now,
                "blocks": [
                    {"start": block.start.isoformat(), "end": block.end.isoformat(),
                     "kwh": round(block.kwh, 2), "cost": round(block.cost, 2), "estimated": block.estimated}
                    for block in schedule.blocks
                ],
                "deadline": planner.deadline,
                "trip_departure": planner.trip.departure,
                "estimated_prices": schedule.estimated,
                "shortfall_kwh": schedule.shortfall_kwh,
            }
        if key == "planned_cost":
            return {"alternatives": {
                mode: {
                    "cost": plan.cost, "energy_kwh": plan.energy_kwh, "estimated": plan.estimated,
                    "start": plan.blocks[0].start.isoformat() if plan.blocks else None,
                    "end": plan.blocks[-1].end.isoformat() if plan.blocks else None,
                    "blocks": len(plan.blocks), "shortfall_kwh": plan.shortfall_kwh,
                }
                for mode, plan in planner.alternatives.items()
            }}
        if key == "trip_distance":
            route = planner.trip.route
            return {
                "destination": planner.trip.destination or None,
                "resolved_name": route.name if route else None,
                "duration_min": route.duration_min if route else None,
                "method": route.method if route else None,
                "round_trip": planner.trip.round_trip,
                "looking_up": planner.trip.looking_up,
                "error": planner.trip.error,
            }
        return None
