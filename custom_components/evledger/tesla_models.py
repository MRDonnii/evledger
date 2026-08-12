"""Reference table of Tesla model/trim specs, for efficiency comparison.

These are best-effort figures from Tesla's own published EU WLTP combined
consumption and usable battery capacity, by model generation/trim. They are
NOT pulled from any live API — the Tesla Custom Integration exposes no VIN,
trim, or battery-size data at all (only a bare "Model 3"/"Model Y"/... model
name), so there is no way to auto-detect the exact configuration.

Treat every entry here as a starting point, not ground truth: exact figures
shift by model year, wheel size, and regional homologation. Always prefer the
number from your own delivery paperwork or Tesla account if it differs from
the closest match here — the config flow's "Custom" option exists precisely
for that. Corrections and additions via PR are welcome.
"""
from __future__ import annotations

from typing import TypedDict


class TeslaModelSpec(TypedDict):
    label: str
    battery_kwh: float
    wltp_wh_per_km: int


TESLA_MODEL_SPECS: dict[str, TeslaModelSpec] = {
    # --- Model 3 (2017-2023, pre-"Highland" refresh) ---
    "model3_2017_sr_rwd": {
        "label": "Model 3 Standard Range/Standard Range Plus RWD (2019-2023)",
        "battery_kwh": 55.0,
        "wltp_wh_per_km": 149,
    },
    "model3_2017_lr_awd": {
        "label": "Model 3 Long Range AWD (2019-2023)",
        "battery_kwh": 75.0,
        "wltp_wh_per_km": 158,
    },
    "model3_2017_performance": {
        "label": "Model 3 Performance (2019-2023)",
        "battery_kwh": 75.0,
        "wltp_wh_per_km": 168,
    },
    # --- Model 3 "Highland" refresh (2023+) ---
    "model3_2023_rwd": {
        "label": "Model 3 RWD (2023+ Highland refresh)",
        "battery_kwh": 57.5,
        "wltp_wh_per_km": 137,
    },
    "model3_2023_lr_awd": {
        "label": "Model 3 Long Range AWD (2023+ Highland refresh)",
        "battery_kwh": 75.0,
        "wltp_wh_per_km": 142,
    },
    "model3_2024_performance": {
        "label": "Model 3 Performance (2024+ Highland refresh)",
        "battery_kwh": 75.0,
        "wltp_wh_per_km": 158,
    },
    # --- Model Y (2020-2024, pre-"Juniper" refresh) ---
    "modely_2020_rwd": {
        "label": "Model Y RWD (2021-2024)",
        "battery_kwh": 60.0,
        "wltp_wh_per_km": 153,
    },
    "modely_2020_lr_awd": {
        "label": "Model Y Long Range AWD (2020-2024)",
        "battery_kwh": 75.0,
        "wltp_wh_per_km": 163,
    },
    "modely_2020_performance": {
        "label": "Model Y Performance (2020-2024)",
        "battery_kwh": 75.0,
        "wltp_wh_per_km": 175,
    },
    # --- Model Y "Juniper" refresh (2025+) ---
    "modely_2025_rwd": {
        "label": "Model Y RWD (2025+ Juniper refresh)",
        "battery_kwh": 60.0,
        "wltp_wh_per_km": 148,
    },
    "modely_2025_lr_awd": {
        "label": "Model Y Long Range AWD (2025+ Juniper refresh)",
        "battery_kwh": 78.4,
        "wltp_wh_per_km": 153,
    },
    # --- Model S (2021+ refresh) ---
    "models_2021": {
        "label": "Model S (2021+)",
        "battery_kwh": 100.0,
        "wltp_wh_per_km": 184,
    },
    "models_2021_plaid": {
        "label": "Model S Plaid (2021+)",
        "battery_kwh": 100.0,
        "wltp_wh_per_km": 200,
    },
    # --- Model X (2021+ refresh) ---
    "modelx_2021": {
        "label": "Model X (2021+)",
        "battery_kwh": 100.0,
        "wltp_wh_per_km": 210,
    },
    "modelx_2021_plaid": {
        "label": "Model X Plaid (2021+)",
        "battery_kwh": 100.0,
        "wltp_wh_per_km": 216,
    },
}

CUSTOM_MODEL_KEY = "custom"
