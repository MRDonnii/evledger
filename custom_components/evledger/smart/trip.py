"""Temporary trip plan: distance to a destination and the battery level the trip needs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"
USER_AGENT = "ha-ev-smart-charge (+https://github.com/MRDonnii/ha-ev-smart-charge)"
# Roads are longer than the straight line; used only when no route could be fetched.
DETOUR_FACTOR = 1.3
COORDINATES = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")


@dataclass(frozen=True)
class Route:
    distance_km: float
    duration_min: float | None
    name: str
    latitude: float
    longitude: float
    method: str  # "route" (road distance) or "straight_line" (estimate)


def parse_coordinates(text: str) -> tuple[float, float] | None:
    match = COORDINATES.match(text or "")
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return lat, lon
    return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = rlat2 - rlat1, math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def trip_energy_kwh(distance_km: float, round_trip: bool, consumption_wh_km: float, margin_pct: float) -> float:
    """Energy the trip takes from the battery, including the safety margin."""
    km = distance_km * (2 if round_trip else 1)
    return km * consumption_wh_km / 1000 * (1 + margin_pct / 100)


def trip_target_soc(energy_kwh: float, capacity_kwh: float, reserve_pct: float) -> float:
    """Battery level to leave with, so the reserve is still left when the trip is over. Can exceed 100."""
    if capacity_kwh <= 0:
        return 100.0
    return reserve_pct + energy_kwh / capacity_kwh * 100


async def async_geocode(session, text: str, language: str | None) -> tuple[float, float, str] | None:
    params = {"q": text, "format": "jsonv2", "limit": "1"}
    if language:
        params["accept-language"] = language
    async with session.get(NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT},
                           timeout=_timeout()) as response:
        response.raise_for_status()
        results = await response.json()
    if not results:
        return None
    first = results[0]
    return float(first["lat"]), float(first["lon"]), str(first.get("display_name") or text)


async def async_road_distance(session, origin: tuple[float, float],
                              destination: tuple[float, float]) -> tuple[float, float] | None:
    url = OSRM_URL.format(lat1=origin[0], lon1=origin[1], lat2=destination[0], lon2=destination[1])
    async with session.get(url, params={"overview": "false"}, headers={"User-Agent": USER_AGENT},
                           timeout=_timeout()) as response:
        response.raise_for_status()
        data = await response.json()
    routes = data.get("routes") or []
    if data.get("code") != "Ok" or not routes:
        return None
    return routes[0]["distance"] / 1000, routes[0]["duration"] / 60


def _timeout():
    import aiohttp

    return aiohttp.ClientTimeout(total=15)
