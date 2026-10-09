"""Tesla models: usable battery, typical consumption and rated range, so the plan fits the car.

Figures are typical values for the model year range, not exact for every car: capacity is the usable
battery when new, consumption a realistic mixed-driving average and range the EPA rated range. The
capacity can always be overridden in the options. No Home Assistant imports, so it can be unit tested.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Vehicle:
    key: str
    name: str
    family: str  # the model name Tesla integrations report on the device ("Model 3", "Model Y", ...)
    capacity_kwh: float
    consumption_wh_km: float
    range_km: float
    body: str  # artwork shape used by th-tesla-dashboard-card


OTHER = "other"

VEHICLES: tuple[Vehicle, ...] = (
    Vehicle("model_3_sr_plus", "Model 3 Standard Range Plus (2019–2020)", "Model 3", 50, 145, 402, "model_3"),
    Vehicle("model_3_rwd", "Model 3 RWD (2021–2023)", "Model 3", 57.5, 150, 438, "model_3"),
    Vehicle("model_3_lr", "Model 3 Long Range AWD (2019–2023)", "Model 3", 75, 160, 576, "model_3"),
    Vehicle("model_3_performance", "Model 3 Performance (2019–2023)", "Model 3", 75, 170, 507, "model_3"),
    Vehicle("model_3_highland_rwd", "Model 3 RWD (2024–)", "Model 3", 57.5, 140, 438, "model_3_highland"),
    Vehicle("model_3_highland_lr_rwd", "Model 3 Long Range RWD (2025–)", "Model 3", 75, 135, 584, "model_3_highland"),
    Vehicle("model_3_highland_lr", "Model 3 Long Range AWD (2024–)", "Model 3", 75, 145, 549, "model_3_highland"),
    Vehicle("model_3_highland_performance", "Model 3 Performance (2024–)", "Model 3", 79, 160, 476,
            "model_3_highland"),
    Vehicle("model_y_rwd", "Model Y RWD (2022–2024)", "Model Y", 57.5, 160, 418, "model_y"),
    Vehicle("model_y_lr", "Model Y Long Range AWD (2020–2024)", "Model Y", 75, 165, 533, "model_y"),
    Vehicle("model_y_performance", "Model Y Performance (2020–2024)", "Model Y", 75, 175, 488, "model_y"),
    Vehicle("model_y_juniper_rwd", "Model Y RWD (2025–)", "Model Y", 60, 150, 460, "model_y_juniper"),
    Vehicle("model_y_juniper_lr_rwd", "Model Y Long Range RWD (2025–)", "Model Y", 78, 145, 575, "model_y_juniper"),
    Vehicle("model_y_juniper_lr", "Model Y Long Range AWD (2025–)", "Model Y", 78, 155, 529, "model_y_juniper"),
    Vehicle("model_y_juniper_performance", "Model Y Performance (2025–)", "Model Y", 78, 165, 491,
            "model_y_juniper"),
    Vehicle("model_s_75", "Model S 75/75D (2016–2019)", "Model S", 72, 190, 417, "model_s"),
    Vehicle("model_s_85", "Model S 85/90D (2012–2017)", "Model S", 81, 195, 435, "model_s"),
    Vehicle("model_s_100", "Model S 100D/P100D (2017–2019)", "Model S", 98, 195, 539, "model_s"),
    Vehicle("model_s_lr", "Model S Long Range (2019–)", "Model S", 95, 180, 652, "model_s"),
    Vehicle("model_s_plaid", "Model S Plaid (2021–)", "Model S", 95, 195, 600, "model_s"),
    Vehicle("model_x_75", "Model X 75D/90D (2016–2019)", "Model X", 81, 225, 417, "model_x"),
    Vehicle("model_x_100", "Model X 100D/P100D (2017–2019)", "Model X", 98, 225, 475, "model_x"),
    Vehicle("model_x_lr", "Model X Long Range (2019–)", "Model X", 95, 210, 539, "model_x"),
    Vehicle("model_x_plaid", "Model X Plaid (2021–)", "Model X", 95, 225, 536, "model_x"),
    Vehicle("cybertruck_rwd", "Cybertruck RWD (2025–)", "Cybertruck", 92, 270, 563, "cybertruck"),
    Vehicle("cybertruck_awd", "Cybertruck AWD (2024–)", "Cybertruck", 123, 285, 512, "cybertruck"),
    Vehicle("cybertruck_cyberbeast", "Cybertruck Cyberbeast (2024–)", "Cybertruck", 123, 300, 483, "cybertruck"),
    Vehicle("roadster", "Roadster (2008–2012)", "Roadster", 53, 135, 393, "roadster"),
)

BY_KEY = {vehicle.key: vehicle for vehicle in VEHICLES}


def get(key: str | None) -> Vehicle | None:
    return BY_KEY.get(key or "")


def family_of(device_model: str | None) -> str | None:
    """Map a device model string ("Model 3", "Tesla Model Y", "model3", "Cybertruck") to a family."""
    text = "".join((device_model or "").lower().split())
    for family in ("Model 3", "Model Y", "Model S", "Model X", "Cybertruck", "Roadster"):
        if "".join(family.lower().split()) in text:
            return family
    if text.endswith(("3", "y", "s", "x")) and text[:-1] in ("", "tesla", "teslamodel", "model"):
        return f"Model {text[-1].upper()}"
    return None


def guess(device_model: str | None, range_km: float | None = None, soc: float | None = None) -> Vehicle | None:
    """The most likely model: the family from the car's device, the variant from the rated range at 100 %."""
    family = family_of(device_model)
    if family is None:
        return None
    candidates = [vehicle for vehicle in VEHICLES if vehicle.family == family]
    if range_km and soc and soc >= 20:
        full = range_km / soc * 100
        # Batteries lose some range with age; a car at 90 % of its rated range still matches its model.
        return min(candidates, key=lambda vehicle: abs(vehicle.range_km * 0.95 - full))
    return candidates[0]
